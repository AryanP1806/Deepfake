#!/usr/bin/env python3
"""Verify a DFIR evidence package (stdlib only — runs anywhere with Python 3.8+).

Usage:  python verify_package.py [package_dir]

Checks
  1. Every file listed in hashes.csv exists and its SHA-256 matches.
  2. The custody log is an unbroken hash chain (no edited, inserted or deleted entries).
"""
import csv
import hashlib
import json
import sys
from pathlib import Path

GENESIS = "0" * 64


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_chain(lines):
    problems, prev = [], GENESIS
    for i, line in enumerate(l for l in lines if l.strip()):
        e = json.loads(line)
        body = {k: v for k, v in e.items() if k != "entry_hash"}
        calc = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if e.get("prev_hash") != prev:
            problems.append(f"custody seq {e.get('seq')}: chain broken (prev_hash mismatch)")
        if calc != e.get("entry_hash"):
            problems.append(f"custody seq {e.get('seq')}: entry altered (hash mismatch)")
        prev = e.get("entry_hash")
    return problems


def main(root: Path) -> int:
    problems, n = [], 0
    with (root / "hashes.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            n += 1
            p = root / row["path"]
            if not p.exists():
                problems.append(f"missing: {row['path']}")
            elif sha256(p) != row["sha256"]:
                problems.append(f"hash mismatch: {row['path']}")
    log = root / "custody_log.jsonl"
    entries = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    problems += verify_chain(entries)
    print(f"Checked {n} files and {len([l for l in entries if l.strip()])} custody entries.")
    if problems:
        print("FAILED:")
        for p in problems:
            print("  -", p)
        return 1
    print("OK - all file hashes match and the custody chain is intact.")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)))
