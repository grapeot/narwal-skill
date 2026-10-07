"""Exit codes and error types for narwal-local."""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_PARTIAL = 3
EXIT_TRANSPORT = 10
EXIT_QUERY = 11
EXIT_DECODE = 12
EXIT_ARTIFACT = 13

SAME_IP_CLOSE = "connection with same ip, close old one"


class NarwalError(Exception):
    """Base error. detail preserves the original exception text."""

    exit_code = EXIT_QUERY
    code = "failed"

    def __init__(self, message: str, *, detail: str | None = None, cause: BaseException | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail if detail is not None else message
        self.cause = cause
        if cause is not None and SAME_IP_CLOSE in str(cause) and SAME_IP_CLOSE not in self.detail:
            self.detail = f"{self.detail}; {cause}"


class UsageError(NarwalError):
    exit_code = EXIT_USAGE
    code = "usage"


class TransportError(NarwalError):
    exit_code = EXIT_TRANSPORT
    code = "transport"


class QueryError(NarwalError):
    exit_code = EXIT_QUERY
    code = "query"


class QueryTimeout(QueryError):
    code = "query_timeout"


class QueryRejected(QueryError):
    code = "query_rejected"


class DecodeError(NarwalError):
    exit_code = EXIT_DECODE
    code = "decode"


class ArtifactError(NarwalError):
    exit_code = EXIT_ARTIFACT
    code = "artifact"


class ReadOnlyViolation(NarwalError):
    exit_code = EXIT_USAGE
    code = "readonly_violation"


class ConnectionCap(QueryError):
    code = "connection_cap"


class BudgetExceeded(QueryError):
    code = "budget_exceeded"


def exception_payload(exc: BaseException) -> dict[str, str]:
    """Structured error fields. Never includes a traceback."""
    if isinstance(exc, NarwalError):
        detail = exc.detail
        code = exc.code
        message = exc.message
    else:
        detail = f"{type(exc).__name__}: {exc}"
        code = "failed"
        message = str(exc) or type(exc).__name__
    cause = exc.__cause__ or exc.__context__
    if cause is not None and str(cause) not in detail:
        detail = f"{detail}; cause={type(cause).__name__}: {cause}"
    if SAME_IP_CLOSE in str(exc) and SAME_IP_CLOSE not in detail:
        detail = f"{detail}; {SAME_IP_CLOSE}"
    return {
        "code": code,
        "message": message,
        "exception_type": type(exc).__name__,
        "detail": detail,
    }
