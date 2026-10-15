"""Content-addressed, write-once evidence vault.

Files are stored at ``<case>/vault/<sha256>`` and marked read-only. Each stored item
gets a sidecar acquisition record and a custody entry.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from .custody import CustodyLog, ts_pair


def hash_bytes(data: bytes) -> dict[str, str]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "md5": hashlib.md5(data).hexdigest(),
            "sha1": hashlib.sha1(data).hexdigest()}


def hash_file(path: Path) -> dict[str, str]:
    s256, m5, s1 = hashlib.sha256(), hashlib.md5(), hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            s256.update(chunk)
            m5.update(chunk)
            s1.update(chunk)
    return {"sha256": s256.hexdigest(), "md5": m5.hexdigest(), "sha1": s1.hexdigest()}


class Vault:
    def __init__(self, case_dir: Path):
        self.root = case_dir / "vault"
        self.meta = case_dir / "vault_meta"
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta.mkdir(parents=True, exist_ok=True)
        self.custody = CustodyLog(case_dir / "custody_log.jsonl")

    def path(self, sha256: str) -> Path:
        return self.root / sha256

    def exists(self, sha256: str) -> bool:
        return self.path(sha256).exists()

    def record(self, sha256: str) -> dict | None:
        p = self.meta / f"{sha256}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def store(self, data: bytes, *, kind: str, filename: str, mime: str | None = None,
              acquisition: dict[str, Any] | None = None, actor: str = "system",
              tool: str | None = None) -> dict:
        """Store bytes. Idempotent: storing identical bytes again only adds a custody entry."""
        hashes = hash_bytes(data)
        sha = hashes["sha256"]
        dest = self.path(sha)
        new = not dest.exists()
        if new:
            dest.write_bytes(data)
            os.chmod(dest, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            rec = {
                **hashes,
                "size": len(data),
                "kind": kind,  # original | capture_screenshot | capture_html | artifact | download | export
                "filename": filename,
                "mime": mime,
                "stored_at": ts_pair(),
                "acquisition": acquisition or {},
            }
            (self.meta / f"{sha}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            rec = self.record(sha) or {**hashes}
        self.custody.append(
            "vault.store" if new else "vault.store_duplicate",
            actor=actor, tool=tool, item_sha256=sha,
            details={"kind": kind, "filename": filename, "size": len(data),
                     "md5": hashes["md5"], **({"acquisition": acquisition} if acquisition else {})},
        )
        return rec

    def store_file(self, src: Path, **kw) -> dict:
        return self.store(src.read_bytes(), filename=kw.pop("filename", src.name), **kw)

    def verify(self, sha256: str) -> bool:
        p = self.path(sha256)
        return p.exists() and hash_file(p)["sha256"] == sha256

    def all_records(self) -> list[dict]:
        recs = [json.loads(p.read_text(encoding="utf-8")) for p in self.meta.glob("*.json")]
        return sorted(recs, key=lambda r: (r.get("stored_at") or {}).get("utc", ""))
