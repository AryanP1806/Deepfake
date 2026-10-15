"""Hash-chained, append-only chain-of-custody ledger (one JSONL file per case).

Every entry contains the SHA-256 of the previous entry, so editing or deleting any
line breaks verification of all later lines.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import host_info

IST = timezone(timedelta(hours=5, minutes=30))
GENESIS = "0" * 64
_lock = threading.Lock()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ts_pair(dt: datetime | None = None) -> dict[str, str]:
    dt = dt or now_utc()
    return {"utc": dt.astimezone(timezone.utc).isoformat(), "ist": dt.astimezone(IST).isoformat()}


def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class CustodyLog:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> tuple[str, int]:
        if not self.path.exists():
            return GENESIS, 0
        last, n = GENESIS, 0
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = json.loads(line)["entry_hash"]
                    n += 1
        return last, n

    def append(self, action: str, *, actor: str = "system", tool: str | None = None,
               item_sha256: str | None = None, details: dict[str, Any] | None = None) -> dict:
        with _lock:
            prev, n = self._last_hash()
            entry = {
                "seq": n + 1,
                "time": ts_pair(),
                "actor": actor,
                "action": action,
                "tool": tool or host_info()["app"],
                "host": host_info()["hostname"],
                "item_sha256": item_sha256,
                "details": details or {},
                "prev_hash": prev,
            }
            entry["entry_hash"] = _entry_hash(entry)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]


def verify_chain(lines: list[str]) -> tuple[bool, list[str]]:
    """Verify a custody log. Returns (ok, problems)."""
    problems: list[str] = []
    prev = GENESIS
    for i, line in enumerate(l for l in lines if l.strip()):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {i + 1}: not valid JSON")
            return False, problems
        if e.get("prev_hash") != prev:
            problems.append(f"seq {e.get('seq')}: prev_hash mismatch (chain broken)")
        if _entry_hash(e) != e.get("entry_hash"):
            problems.append(f"seq {e.get('seq')}: entry content altered (hash mismatch)")
        prev = e.get("entry_hash")
    return not problems, problems
