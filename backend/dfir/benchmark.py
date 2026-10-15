"""Benchmarks: run the full pipeline on a labelled set of files and compare verdicts with the truth.

Labels come from file names (``fake_*``, ``real_*``, ``*-fake.png`` …) or are passed explicitly.
Benchmark images are *test* data: they are never used for calibration (see scripts/calibrate.py).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

from .core.config import DATA_DIR
from .core.db import session, vault_for
from .core.models import Case, Media
from .pipeline import analyze_media, ingest_media, media_explanation

BENCH_DIR = DATA_DIR / "benchmarks"
FAKE_RE = re.compile(r"(^|[_\-\s.])(fake|deepfake|ai|generated|synthetic|manipulated)([_\-\s.]|$)", re.I)
REAL_RE = re.compile(r"(^|[_\-\s.])(real|authentic|original|genuine)([_\-\s.]|$)", re.I)


def label_from_name(name: str) -> str | None:
    stem = Path(name).stem
    if FAKE_RE.search(stem):
        return "fake"
    if REAL_RE.search(stem):
        return "real"
    return None


def create(name: str, files: list[tuple[str, bytes, str | None]], visible_cases: bool = False) -> dict:
    """files: [(filename, bytes, label|None)]. Creates one case per file. Returns the benchmark record."""
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    bid = "bench_" + uuid.uuid4().hex[:10]
    items = []
    with session() as s:
        for fn, data, label in files:
            label = label or label_from_name(fn)
            c = Case(title=f"Test image: {fn}" + (f" (labelled {label})" if label else ""),
                     status="open" if visible_cases else "lab",
                     description=f"Created by benchmark {bid}. Ground-truth label from {'file name' if label else 'n/a'}: "
                                 f"{label or 'unknown'}. Test data — never used for calibration.")
            s.add(c)
            s.commit()
            vault_for(c.id).custody.append("case.opened", actor="investigator", details={"benchmark": bid, "label": label})
            m = ingest_media(s, c.id, data, fn, acquisition={"method": "benchmark upload", "benchmark": bid,
                                                             "original_filename": fn})
            items.append({"case_id": c.id, "media_id": m.id, "filename": fn, "truth": label})
    rec = {"id": bid, "name": name, "created_at": datetime.now(timezone.utc).isoformat(), "items": items}
    (BENCH_DIR / f"{bid}.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


def run(bid: str, progress=lambda f, m: None) -> None:
    rec = load(bid)
    n = len(rec["items"])
    for i, it in enumerate(rec["items"]):
        progress(i / max(1, n), f"{i + 1}/{n}: {it['filename']}")
        analyze_media(it["case_id"], it["media_id"], lambda f, m, i=i: progress((i + f) / max(1, n), f"{i + 1}/{n}: {m}"))
    progress(1.0, "Benchmark complete")


def load(bid: str) -> dict:
    return json.loads((BENCH_DIR / f"{bid}.json").read_text(encoding="utf-8"))


def list_all() -> list[dict]:
    if not BENCH_DIR.exists():
        return []
    out = []
    for p in sorted(BENCH_DIR.glob("bench_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        r = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": r["id"], "name": r["name"], "created_at": r["created_at"], "n": len(r["items"])})
    return out


def results(bid: str) -> dict:
    rec = load(bid)
    rows = []
    with session() as s:
        for it in rec["items"]:
            m = s.get(Media, it["media_id"])
            v = None
            if m and m.fingerprints:
                v = media_explanation(s, it["case_id"], it["media_id"]).get("verdict")
            thumb = None
            if m:
                from .core.models import Artifact
                a = s.exec(select(Artifact).where(Artifact.media_id == m.id, Artifact.kind == "thumb")).first()
                thumb = a.sha256 if a else None
            pred = None
            if v and v.get("available"):
                pred = "fake" if v["p_fake"] >= 0.5 else "real"
            rows.append({**it, "thumb": thumb, "analysed": bool(v and v.get("available")),
                         "verdict": {k: v.get(k) for k in ("label", "headline", "p_fake", "p_ai", "p_edit", "confidence",
                                                           "kind", "summary")} if v and v.get("available") else None,
                         "detectors": v.get("detectors") if v and v.get("available") else None,
                         "pred": pred, "correct": (pred == it["truth"]) if pred and it["truth"] else None})
    scored = [r for r in rows if r["correct"] is not None]
    metrics = {"n_labelled": len(scored), "n_total": len(rows),
               "n_analysed": sum(1 for r in rows if r["analysed"])}
    if scored:
        metrics["accuracy"] = sum(r["correct"] for r in scored) / len(scored)
        cm = {"fake_as_fake": 0, "fake_as_real": 0, "real_as_real": 0, "real_as_fake": 0}
        for r in scored:
            cm[f"{r['truth']}_as_{r['pred']}"] += 1
        metrics["confusion"] = cm
        metrics["uncertain"] = sum(1 for r in scored if r["verdict"]["label"] == "uncertain")
        pos = [r["verdict"]["p_fake"] for r in scored if r["truth"] == "fake"]
        neg = [r["verdict"]["p_fake"] for r in scored if r["truth"] == "real"]
        if pos and neg:
            pairs = [(1.0 if p > q else 0.5 if p == q else 0.0) for p in pos for q in neg]
            metrics["auc"] = sum(pairs) / len(pairs)
        per_class = min(len(pos), len(neg))
        metrics["warning"] = (None if per_class >= 200 else
                              f"Only {len(pos)} fake and {len(neg)} real labelled images: this shows how individual "
                              f"images are handled but cannot measure accuracy reliably (aim for 200+ per class from "
                              f"sources the detectors never saw).")
    return {**{k: rec[k] for k in ("id", "name", "created_at")}, "rows": rows, "metrics": metrics}
