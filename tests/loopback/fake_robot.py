"""Loopback WebSocket robot. Synthetic ids only."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import websockets

from narwal_skill.vendor.narwal_client.protocol import parse_frame

Handler = Callable[[object, bytes, str, int], Awaitable[None]]


class FakeRobot:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.received: list[str] = []
        self.connections = 0
        self.port = 0
        self._server = None

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._serve,
            "127.0.0.1",
            0,
            ping_interval=None,
        )
        sock = self._server.sockets[0]
        self.port = sock.getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _serve(self, ws) -> None:
        self.connections += 1
        conn = self.connections
        async for raw in ws:
            if isinstance(raw, str):
                continue
            msg = parse_frame(raw)
            short = msg.short_topic
            self.received.append(short)
            await self.handler(ws, raw, short, conn)


async def push_later(ws, frame: bytes, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await ws.send(frame)
    except Exception:
        return
