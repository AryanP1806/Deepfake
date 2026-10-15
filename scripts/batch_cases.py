#!/usr/bin/env python3
"""Create one case per image in a folder (labels from file names) through the running backend,
wait for the analyses, and write a Markdown report.

    cd backend && uv run python ../scripts/batch_cases.py --folder ../demo [--api http://127.0.0.1:8000]

Uses the backend's API (so the GPU models loaded there are reused). Files whose names don't carry
a label are still analysed but excluded from the accuracy numbers.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

EXCLUDE = {"victim_received_copy.jpg"}  # synthetic file belonging to the built-in demo case


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--report", default=str(Path(__file__).resolve().parents[1] / "tmp" / "benchmark_demo.md"))
    a = ap.parse_args()
    files = [p for p in sorted(Path(a.folder).iterdir())
             if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and p.name not in EXCLUDE]
    if not files:
        print("no images found")
        return 1
    print(f"Uploading {len(files)} files as cases …")
    r = httpx.post(f"{a.api}/api/lab/benchmark", params={"name": f"{Path(a.folder).name} ({len(files)} files)", "visible": True},
                   files=[("files", (p.name, p.read_bytes())) for p in files], timeout=300)
    r.raise_for_status()
    bid = r.json()["benchmark"]
    while True:
        res = httpx.get(f"{a.api}/api/lab/benchmark/{bid}", timeout=60).json()
        job = res.get("job") or {}
        print(f"  {job.get('status')} {round((job.get('progress') or 0) * 100)}% {job.get('message', '')}", flush=True)
        if job.get("status") in ("done", "error"):
            break
        time.sleep(4)
    m = res["metrics"]
    lines = [f"# Benchmark: {res['name']}", "", f"Benchmark id `{bid}` — {len(res['rows'])} images, one case each.", ""]
    if m.get("accuracy") is not None:
        lines += [f"- **Correct:** {m['accuracy']:.0%} of {m['n_labelled']} labelled images",
                  f"- **AUC:** {m['auc']:.2f}" if m.get("auc") is not None else "- AUC: n/a",
                  f"- **Confusion:** {m['confusion']}", f"- **Uncertain verdicts:** {m.get('uncertain', 0)}"]
    if m.get("warning"):
        lines += ["", f"> ⚠ {m['warning']}"]
    lines += ["", "| Image | Truth | Verdict | % fake | AI | Edit | Confidence | Correct | Case |", "|---|---|---|---|---|---|---|---|---|"]
    for row in res["rows"]:
        v = row.get("verdict") or {}
        pct = lambda x: "—" if x is None else f"{x * 100:.0f}"  # noqa: E731
        lines.append(f"| {row['filename']} | {row['truth'] or '?'} | {v.get('headline', '—')} | {pct(v.get('p_fake'))} | "
                     f"{pct(v.get('p_ai'))} | {pct(v.get('p_edit'))} | {v.get('confidence', '—')} | "
                     f"{'✓' if row['correct'] else '✗' if row['correct'] is False else ''} | {row['case_id']} |")
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nReport written to {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
