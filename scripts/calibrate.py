#!/usr/bin/env python3
"""Calibrate and evaluate the verdict on independent public data (never on the user's test images).

    cd backend
    uv run python ../scripts/calibrate.py download   # ~2.7 GB: 1 random row-group per OpenFake shard
    uv run python ../scripts/calibrate.py score      # run SPAI / SBI / TruFor on every image (GPU, ~1 h)
    uv run python ../scripts/calibrate.py fit        # fit the stack on 'validation', report on 'test'

Data: OpenFake (ComplexDataLab, CC BY-NC 4.0). Its *validation* split calibrates; its *test* split —
whose generators and real-image sources are disjoint from training/validation — is only used to
measure accuracy. A leak guard drops any image whose SHA-256 or perceptual hash matches demo/ or
any case marked as a test set.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import random
import sys
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "0"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from dfir.core.config import MODELS_DIR  # noqa: E402

DATA = MODELS_DIR / "calibration_data"
REPO = "ComplexDataLab/OpenFake"
PER_SPLIT = {"validation": 320, "test": 320}  # per class
MAX_SIDE = 2048  # matches what the analyzers see after their own resize


def leak_guard_hashes() -> tuple[set[str], list[str]]:
    """SHA-256 + PDQ of everything that must never be used for calibration (user test images)."""
    import numpy as np
    import pdqhash
    from PIL import Image
    shas, pdqs = set(), []
    for p in (ROOT / "demo").rglob("*"):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            b = p.read_bytes()
            shas.add(hashlib.sha256(b).hexdigest())
            try:
                h, _ = pdqhash.compute(np.asarray(Image.open(io.BytesIO(b)).convert("RGB")))
                pdqs.append(np.packbits(h.astype(np.uint8)).tobytes().hex())
            except Exception:  # noqa: BLE001
                pass
    return shas, pdqs


def _pdq_hex(im) -> str:
    import numpy as np
    import pdqhash
    h, _ = pdqhash.compute(np.asarray(im.convert("RGB")))
    return np.packbits(h.astype(np.uint8)).tobytes().hex()


def _ham(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def download(seed: int = 7) -> None:
    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, HfFileSystem
    from PIL import Image
    rng = random.Random(seed)
    fs = HfFileSystem()
    names = [f.path for f in HfApi().list_repo_tree(REPO, repo_type="dataset", path_in_repo="core")]
    bad_sha, bad_pdq = leak_guard_hashes()
    for split, per_class in PER_SPLIT.items():
        out = DATA / split
        out.mkdir(parents=True, exist_ok=True)
        manifest = out / "manifest.csv"
        rows = []
        shards = sorted(n for n in names if n.split("/")[-1].startswith(split))
        for fn in shards:
            with fs.open(f"datasets/{REPO}/{fn}", "rb") as f:
                pf = pq.ParquetFile(f)
                rg = rng.randrange(pf.metadata.num_row_groups)
                t = time.time()
                tab = pf.read_row_group(rg, columns=["image", "label", "model", "type", "image_sha256"])
                print(f"{split}: {fn} row-group {rg}: {tab.num_rows} rows in {time.time() - t:.0f}s", flush=True)
            for r in tab.to_pylist():
                rows.append(r)
        by = {"real": [r for r in rows if r["label"] == "real"], "fake": [r for r in rows if r["label"] == "fake"]}
        kept = []
        for label, items in by.items():
            rng.shuffle(items)
            n = 0
            for r in items:
                if n >= per_class:
                    break
                b = r["image"]["bytes"]
                sha = hashlib.sha256(b).hexdigest()
                try:
                    im = Image.open(io.BytesIO(b))
                    im.load()
                except Exception:  # noqa: BLE001
                    continue
                pdq = _pdq_hex(im)
                if sha in bad_sha or any(_ham(pdq, x) <= 31 for x in bad_pdq):
                    print(f"  leak guard: skipped an image matching the user's test set ({sha[:12]})")
                    continue
                if max(im.size) > MAX_SIDE:  # keep files manageable; analyzers would resize anyway
                    s = MAX_SIDE / max(im.size)
                    im = im.convert("RGB").resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=95)
                    b = buf.getvalue()
                ext = ".png" if b[:4] == b"\x89PNG" else ".jpg"
                name = f"{label}_{sha[:16]}{ext}"
                (out / name).write_bytes(b)
                kept.append({"file": name, "label": label, "model": r.get("model") or "", "type": r.get("type") or "",
                             "sha256_original": r.get("image_sha256") or sha})
                n += 1
            print(f"{split}: kept {n} {label}")
        with manifest.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(kept[0]))
            w.writeheader()
            w.writerows(kept)


def score() -> None:
    import numpy as np
    from PIL import Image

    from dfir.analyzers.research_detectors import sbi_faces, spai_score, trufor_run
    for split in PER_SPLIT:
        d = DATA / split
        out = d / "scores.jsonl"
        done = set()
        if out.exists():
            done = {json.loads(l)["file"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
        rows = list(csv.DictReader((d / "manifest.csv").open(encoding="utf-8")))
        t0 = time.time()
        n_tru = sum(1 for l in (out.read_text(encoding="utf-8").splitlines() if out.exists() else [])
                    if l.strip() and '"trufor"' in l)
        with out.open("a", encoding="utf-8") as fh:
            for i, r in enumerate(rows):
                if r["file"] in done:
                    continue
                rgb = np.asarray(Image.open(d / r["file"]).convert("RGB"))
                rec = {"file": r["file"], "label": r["label"], "model": r["model"], "spai": spai_score(rgb)}
                faces = sbi_faces(rgb)
                rec["sbi"] = max((f["score"] for f in faces), default=None)
                rec["n_faces"] = len(faces)
                if r["label"] == "real" and n_tru < 160:  # TruFor is only needed for its false-alarm rate on real photos
                    rec["trufor"] = trufor_run(rgb)["score"]
                    n_tru += 1
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                if i % 20 == 0:
                    el = time.time() - t0
                    print(f"{split}: {i + 1}/{len(rows)}  {el / (i + 1 - len(done) or 1):.1f}s/img", flush=True)


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _feats(r: dict) -> list[float]:
    return [_logit(r["spai"]), _logit(r["sbi"]) if r.get("sbi") is not None else 0.0,
            1.0 if r.get("sbi") is not None else 0.0]


def fit() -> None:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    load = lambda s: [json.loads(l) for l in (DATA / s / "scores.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]  # noqa: E731
    cal, test = load("validation"), load("test")
    X = np.array([_feats(r) for r in cal])
    y = np.array([1 if r["label"] == "fake" else 0 for r in cal])
    clf = LogisticRegression(C=1.0, class_weight="balanced", max_iter=1000).fit(X, y)
    Xt = np.array([_feats(r) for r in test])
    yt = np.array([1 if r["label"] == "fake" else 0 for r in test])
    pt = clf.predict_proba(Xt)[:, 1]
    raw_spai = np.array([r["spai"] for r in test])

    def ece(p, y, bins=10):
        e = 0.0
        for b in range(bins):
            m = (p >= b / bins) & (p < (b + 1) / bins)
            if m.any():
                e += m.mean() * abs(p[m].mean() - y[m].mean())
        return float(e)

    report = {
        "n": int(len(yt)),
        "accuracy": float(((pt >= 0.5) == yt).mean()),
        "auc": float(roc_auc_score(yt, pt)),
        "ece": ece(pt, yt),
        "real_correct": float((pt[yt == 0] < 0.5).mean()),
        "fake_correct": float((pt[yt == 1] >= 0.5).mean()),
        "spai_alone_auc": float(roc_auc_score(yt, raw_spai)),
        "spai_alone_accuracy": float(((raw_spai >= 0.5) == yt).mean()),
        "spai_alone_ece": ece(raw_spai, yt),
    }
    per_model: dict[str, list[int]] = {}
    for r, p in zip(test, pt):
        if r["label"] == "fake":
            per_model.setdefault(r["model"] or "?", []).append(int(p >= 0.5))
    report["fake_recall_by_generator"] = {k: round(sum(v) / len(v), 3) for k, v in
                                          sorted(per_model.items(), key=lambda kv: -len(kv[1])) if len(v) >= 5}
    reals = [r for r in cal + test if r["label"] == "real"]
    tq = sorted(r["trufor"] for r in reals if r.get("trufor") is not None)
    sb = sorted(r["sbi"] for r in reals if r.get("sbi") is not None)
    q = lambda arr, x: arr[min(len(arr) - 1, int(x * len(arr)))] if arr else None  # noqa: E731
    out = {
        "version": 1, "fitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": f"{REPO} (CC BY-NC 4.0): calibration = validation split, evaluation = test split",
        "ai_head": {"features": ["spai_logit", "sbi_logit", "has_face"], "coef": clf.coef_[0].tolist(),
                    "intercept": float(clf.intercept_[0]), "n": int(len(y))},
        "eval": report,
        "trufor_real_quantiles": {"q95": q(tq, 0.95), "q99": q(tq, 0.99), "n": len(tq),
                                  "table": [q(tq, i / 100) for i in range(0, 101, 2)]},
        "sbi_real_faces": {"n": len(sb), "fpr_at_0.5": (sum(1 for v in sb if v >= 0.5) / len(sb)) if sb else None,
                           "q95": q(sb, 0.95), "table": [q(sb, i / 100) for i in range(0, 101, 2)]},
    }
    # evaluate the *full* verdict (AI head + face-swap head) exactly as the app computes it
    from dfir.analyzers.verdict import _percentile
    table = out["sbi_real_faces"]["table"]
    pf = []
    for r, pa in zip(test, pt):
        pe = 0.0
        if r.get("sbi") is not None and table:
            pe = 0.95 * max(0.0, (_percentile(table, r["sbi"]) - 0.90) / 0.10)
        pf.append(1 - (1 - pa) * (1 - pe))
    pf = np.array(pf)
    out["verdict_eval"] = {
        "n": int(len(yt)), "accuracy": float(((pf >= 0.5) == yt).mean()), "auc": float(roc_auc_score(yt, pf)),
        "real_correct": float((pf[yt == 0] < 0.5).mean()), "fake_correct": float((pf[yt == 1] >= 0.5).mean()),
        "uncertain_rate": float(((pf >= 0.35) & (pf < 0.65)).mean()),
        "accuracy_when_not_uncertain": float((((pf >= 0.5) == yt)[(pf < 0.35) | (pf >= 0.65)]).mean())
        if ((pf < 0.35) | (pf >= 0.65)).any() else None,
    }
    (MODELS_DIR / "calibration.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    {"download": download, "score": score, "fit": fit}.get(cmd, lambda: print(__doc__))()
