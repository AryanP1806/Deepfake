"""In-memory ring buffer of backend log records, shown in the Lab / System UI."""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone

_buf: deque[dict] = deque(maxlen=2000)
_lock = threading.Lock()
_seq = 0


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _seq
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        with _lock:
            _seq += 1
            _buf.append({"seq": _seq, "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                         "level": record.levelname, "logger": record.name, "message": msg[:4000]})


QUIET_PATHS = ("/api/system", "/api/lab/result", "/api/lab/runs", "/events", "/api/jobs/", "/api/health")


class _QuietAccess(logging.Filter):
    """Drop access-log lines for the UI's own polling so the log stays readable."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in QUIET_PATHS)


def install() -> None:
    root = logging.getLogger()
    if any(isinstance(h, RingHandler) for h in root.handlers):
        return
    h = RingHandler(level=logging.INFO)
    root.addHandler(h)
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").addFilter(_QuietAccess())
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "dfir"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        if not any(isinstance(x, RingHandler) for x in lg.handlers) and not lg.propagate:
            lg.addHandler(h)


def tail(after: int = 0, limit: int = 300, level: str | None = None) -> list[dict]:
    with _lock:
        items = [r for r in _buf if r["seq"] > after and (not level or r["level"] == level)]
    return items[-limit:]
