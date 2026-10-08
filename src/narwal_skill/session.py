"""Short-lived read-only WebSocket session.

Field 5 replies have an empty topic and no request id, so the session keeps
one outstanding query. A subscription ack is also field 5 and is consumed
before the next query. A timeout closes the socket immediately. The next
query needs a new session. There is no reconnect loop inside a session.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from narwal_skill.allowlist import UNACKNOWLEDGED_SEND_TOPICS, require_send_topic
from narwal_skill.codec import decode_payload, heartbeat_payload, subscription_payload
from narwal_skill.errors import (
    SAME_IP_CLOSE,
    BudgetExceeded,
    DecodeError,
    NarwalError,
    QueryError,
    QueryRejected,
    QueryTimeout,
    TransportError,
)
from narwal_skill.vendor.narwal_client.protocol import (
    PROTOBUF_FIELD5_TAG,
    ProtocolError,
    build_frame,
    parse_frame,
)

_TEXT_FATAL = SAME_IP_CLOSE
CLOSE_TIMEOUT_S = 1.5


@dataclass
class Broadcast:
    topic: str
    short_topic: str
    payload: bytes
    decoded: dict[str, Any] | None
    decode_error: str | None
    observed_at: str
    received_monotonic: float


@dataclass
class QueryResult:
    topic: str
    decoded: dict[str, Any]
    payload: bytes
    observed_at: str
    response_topic: str


@dataclass
class ReadOnlySession:
    url: str
    connect_timeout_s: float
    deadline: float
    allow_control: bool = False
    sent_topics: list[str] = field(default_factory=list)
    broadcasts: list[Broadcast] = field(default_factory=list)
    text_frames: list[str] = field(default_factory=list)
    unexpected_field5: int = 0
    frame_errors: int = 0
    decode_errors: int = 0
    listen_clipped: bool = False
    _ignore_deadline: bool = False
    _ws: Any = None
    _reader: asyncio.Task[None] | None = None
    _outstanding: asyncio.Future[Any] | None = None
    _closed: bool = False
    _dead: bool = False
    _fatal_detail: str | None = None

    @property
    def usable(self) -> bool:
        return not self._closed and not self._dead and self._ws is not None

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def _check_budget(self) -> None:
        if self.remaining() <= 0:
            raise BudgetExceeded(
                "session budget exhausted",
                detail=f"deadline exceeded before the next read-only operation on {self.url}",
            )

    async def connect(self) -> None:
        self._check_budget()
        timeout = min(self.connect_timeout_s, self.remaining())
        try:
            import websockets
        except ImportError as exc:
            raise TransportError("websockets is not installed", detail=str(exc), cause=exc) from exc
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(
                    self.url,
                    ping_interval=None,
                    open_timeout=timeout,
                    close_timeout=CLOSE_TIMEOUT_S,
                    max_size=8_000_000,
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise TransportError(
                f"websocket connect timed out after {timeout:.1f}s",
                detail=f"TimeoutError connecting to {self.url}",
                cause=exc,
            ) from exc
        except Exception as exc:
            raise TransportError(
                f"websocket connect failed: {exc}",
                detail=_detail_from_exc(exc),
                cause=exc,
            ) from exc
        self._reader = asyncio.create_task(self._read_loop(), name="narwal-readonly-reader")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._dead = True
        self._closed = True
        ws = self._ws
        self._ws = None
        reader = self._reader
        self._reader = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(), timeout=2.0)
        if reader is not None and not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        if self._outstanding is not None and not self._outstanding.done():
            self._outstanding.cancel()
        self._outstanding = None

    async def query(
        self,
        short_topic: str,
        payload: bytes = b"",
        *,
        full_topic: str,
        timeout_s: float,
        control: bool = False,
    ) -> QueryResult:
        require_send_topic(short_topic, control=control and self.allow_control)
        if short_topic in UNACKNOWLEDGED_SEND_TOPICS:
            raise QueryError(
                f"{short_topic} is not a request/response query",
                detail="heartbeat must use send_heartbeat so it does not occupy the field5 slot",
            )
        if not self.usable:
            raise TransportError(
                "connection is closed",
                detail=self._fatal_detail or "session was closed after a timeout or transport error",
            )
        if self._outstanding is not None:
            raise QueryError(
                "a query is already outstanding",
                detail="field5 replies cannot be correlated; refusing to pipeline",
            )
        self._check_budget()
        timeout_s = min(timeout_s, self.remaining())
        if timeout_s <= 0:
            raise BudgetExceeded("session budget exhausted", detail="no time left for query")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[str, bytes]] = loop.create_future()
        self._outstanding = future
        frame = build_frame(full_topic, payload)
        try:
            await self._ws.send(frame)
            self.sent_topics.append(short_topic)
            topic, raw = await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError as exc:
            self._dead = True
            await self.aclose()
            raise QueryTimeout(
                f"no field5 reply for {short_topic} within {timeout_s:.1f}s; connection closed",
                detail=(
                    f"QueryTimeout topic={short_topic} timeout_s={timeout_s:.3f}; "
                    "socket closed so a late reply cannot satisfy the next query"
                ),
                cause=exc,
            ) from exc
        except NarwalError:
            self._dead = True
            await self.aclose()
            raise
        except Exception as exc:
            self._dead = True
            await self.aclose()
            raise TransportError(
                f"send/receive failed for {short_topic}: {exc}",
                detail=_detail_from_exc(exc),
                cause=exc,
            ) from exc
        finally:
            if self._outstanding is future:
                self._outstanding = None
        from narwal_skill.envelope import utc_now

        try:
            decoded = decode_payload(raw)
        except DecodeError:
            self._dead = True
            await self.aclose()
            raise
        from narwal_skill.response_shape import validate_control_response, validate_query_response

        try:
            if topic:
                raise DecodeError(
                    "field5 reply topic is non-empty and is not a correlation id",
                    detail=f"response_topic={topic!r}",
                )
            if control:
                validate_control_response(short_topic, decoded)
            else:
                validate_query_response(short_topic, decoded)
        except (DecodeError, QueryError):
            self._dead = True
            await self.aclose()
            raise
        return QueryResult(
            topic=short_topic,
            decoded=decoded,
            payload=raw,
            observed_at=utc_now(),
            response_topic=topic,
        )

    async def subscribe(self, duration_s: int, *, full_topic: str, timeout_s: float) -> QueryResult:
        """Send active_robot_publish and consume its field5 ack before any later query."""
        bounded = max(1, min(int(duration_s), 300))
        return await self.query(
            "common/active_robot_publish",
            subscription_payload(bounded),
            full_topic=full_topic,
            timeout_s=timeout_s,
        )

    async def send_heartbeat(self, *, full_topic: str) -> None:
        """Unacknowledged heartbeat. It does not take the field5 slot."""
        require_send_topic("status/app_status_heartbeat")
        if not self.usable or self._outstanding is not None:
            return
        if not self._ignore_deadline:
            self._check_budget()
        frame = build_frame(full_topic, heartbeat_payload())
        try:
            await self._ws.send(frame)
            self.sent_topics.append("status/app_status_heartbeat")
        except Exception as exc:
            self._dead = True
            await self.aclose()
            raise TransportError(
                f"heartbeat send failed: {exc}",
                detail=_detail_from_exc(exc),
                cause=exc,
            ) from exc

    async def listen(
        self,
        seconds: float,
        *,
        heartbeat_topic: str | None = None,
        within_deadline: bool = True,
    ) -> list[Broadcast]:
        """Collect broadcasts for a bounded interval. Does not pipeline a query.

        Setup listens stop at the shared deadline. Watch duration passes
        within_deadline=False after setup has already succeeded.
        """
        if not self.usable:
            raise TransportError("connection is closed", detail=self._fatal_detail or "session closed")
        if self._outstanding is not None:
            raise QueryError("cannot listen while a query is outstanding", detail="field5 slot is occupied")
        if within_deadline:
            remaining = self.remaining()
            if remaining <= 0:
                raise BudgetExceeded(
                    "listen exceeds the setup budget",
                    detail=f"requested {seconds}s listen with no budget remaining",
                )
            if seconds > remaining:
                self.listen_clipped = True
                seconds = remaining
        self._ignore_deadline = not within_deadline
        end = time.monotonic() + seconds
        start_count = len(self.broadcasts)
        next_heartbeat = time.monotonic()
        try:
            while time.monotonic() < end:
                if self._dead or self._closed:
                    raise TransportError(
                        "connection closed during listen",
                        detail=self._fatal_detail or "socket closed before the listen window ended",
                    )
                if heartbeat_topic and time.monotonic() >= next_heartbeat:
                    await self.send_heartbeat(full_topic=heartbeat_topic)
                    next_heartbeat = time.monotonic() + 30.0
                await asyncio.sleep(min(0.05, max(0.0, end - time.monotonic())))
        finally:
            self._ignore_deadline = False
        return self.broadcasts[start_count:]

    async def _read_loop(self) -> None:
        from narwal_skill.envelope import utc_now

        ws = self._ws
        try:
            while not self._dead and ws is not None:
                data = await ws.recv()
                if isinstance(data, str):
                    self.text_frames.append(data)
                    if _TEXT_FATAL in data:
                        self._fatal_detail = data
                        self._fail_outstanding(TransportError("robot closed the session", detail=data))
                        self._dead = True
                        return
                    continue
                if not isinstance(data, (bytes, bytearray)):
                    continue
                try:
                    msg = parse_frame(bytes(data))
                except ProtocolError as exc:
                    self.frame_errors += 1
                    if self._outstanding is not None and not self._outstanding.done():
                        self._fail_outstanding(
                            DecodeError("response frame could not be parsed", detail=str(exc), cause=exc)
                        )
                    continue
                if msg.field_tag == PROTOBUF_FIELD5_TAG:
                    if self._outstanding is not None and not self._outstanding.done():
                        self._outstanding.set_result((msg.topic, msg.payload))
                    else:
                        self._drop_unsolicited_field5()
                        return
                    continue
                decoded: dict[str, Any] | None
                decode_error: str | None
                try:
                    decoded = decode_payload(msg.payload)
                    decode_error = None
                except DecodeError as exc:
                    decoded = None
                    decode_error = exc.detail
                    self.decode_errors += 1
                self.broadcasts.append(
                    Broadcast(
                        topic=msg.topic,
                        short_topic=msg.short_topic or msg.topic,
                        payload=msg.payload,
                        decoded=decoded,
                        decode_error=decode_error,
                        observed_at=utc_now(),
                        received_monotonic=time.monotonic(),
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            detail = _detail_from_exc(exc)
            self._fatal_detail = detail
            self._dead = True
            self._fail_outstanding(TransportError(f"connection closed: {exc}", detail=detail, cause=exc))

    def _drop_unsolicited_field5(self) -> None:
        """A field5 with nothing pending means the stream is no longer trustworthy.

        Close the socket without awaiting this reader, so the next query cannot
        reuse the connection and the reader does not wait on itself.
        """
        self.unexpected_field5 += 1
        self._dead = True
        self._fatal_detail = (
            "unexpected field5 with no pending query; connection closed"
        )
        ws = self._ws
        self._ws = None
        if ws is not None:
            loop = asyncio.get_running_loop()
            loop.create_task(self._close_socket(ws))

    @staticmethod
    async def _close_socket(ws: Any) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.close(), timeout=2.0)

    def _fail_outstanding(self, exc: Exception) -> None:
        if self._outstanding is not None and not self._outstanding.done():
            self._outstanding.set_exception(exc)


def _detail_from_exc(exc: BaseException) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]
    reason = getattr(getattr(exc, "rcvd", None), "reason", None)
    if reason and str(reason) not in parts[0]:
        parts.append(f"close_reason={reason}")
    cause = exc.__cause__
    if cause is not None:
        parts.append(f"cause={type(cause).__name__}: {cause}")
    text = "; ".join(parts)
    blob = f"{exc} {reason or ''} {cause or ''}"
    if SAME_IP_CLOSE in blob and SAME_IP_CLOSE not in text:
        text = f"{text}; {SAME_IP_CLOSE}"
    return text


def rejected_or_none(decoded: dict[str, Any], topic: str) -> QueryRejected | None:
    from narwal_skill.telemetry import is_rejected_result

    code = is_rejected_result(decoded)
    if code is None:
        return None
    return QueryRejected(
        f"{topic} was rejected and is not data",
        detail=f"result_code={code} topic={topic} keys={sorted(decoded)}",
    )
