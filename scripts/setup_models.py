#!/usr/bin/env python3
"""Download optional model weights (run from backend/ with: uv run python ../scripts/setup_models.py <key> ...).

Keys:
  yunet    OpenCV YuNet face detector (~230 KB)                      — enables face analysis
  ufd      UniversalFakeDetect probe + CLIP ViT-L/14 (~1.7 GB)       — needs: uv sync --extra ml
  whisper  faster-whisper "small" (~480 MB)                          — needs: uv sync --extra asr

Every file's SHA-256 is pinned in models.lock.json on first download. Later loads refuse
files that no longer match. Nothing is downloaded without confirmation (or --yes).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "0"  # downloads are this script's job (dfir sets offline mode by default)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from dfir.core import model_store  # noqa: E402
from dfir.core.config import MODELS_DIR, ensure_dirs  # noqa: E402

SIZES = {"yunet": "~230 KB", "ufd": "~1.7 GB", "whisper": "~480 MB", "spai": "~400 MB", "trufor": "~250 MB",
         "sbi": "~150 MB"}


def confirm(msg: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input(f"{msg} [y/N] ").strip().lower() in ("y", "yes")


def fetch(url: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("gdrive:"):
        import gdown
        gdown.download(id=url.split(":", 1)[1], output=str(dest), quiet=False)
        return model_store.sha256_file(dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.replace(dest)
    return model_store.sha256_file(dest)


def pin(key: str, filename: str, sha: str, url: str) -> None:
    lock = model_store.lock()
    entry = lock.setdefault(key, {"files": {}, "sources": {}})
    old = entry["files"].get(filename)
    if old and old != sha:
        raise SystemExit(f"Refusing: {key}/{filename} differs from pinned SHA-256 {old[:16]}… (got {sha[:16]}…)")
    entry["files"][filename] = sha
    entry["sources"][filename] = url
    model_store.write_lock(lock)
    print(f"  pinned {key}/{filename} sha256={sha}")


def setup_files(key: str) -> None:
    spec = model_store.SPECS[key]
    for fn, url in spec.files.items():
        dest = model_store.model_path(key, fn)
        print(f"  downloading {url}")
        pin(key, fn, fetch(url, dest), url)
        if spec.extra.get("unzip"):
            import zipfile
            with zipfile.ZipFile(dest) as z:
                z.extractall(dest.parent)
            print(f"  extracted {fn}")


def setup_ufd() -> None:
    try:
        import open_clip  # noqa: F401
    except ImportError:
        raise SystemExit("open_clip not installed — run: uv sync --extra ml (from backend/)")
    setup_files("ufd")
    from dfir.analyzers.clip_backend import _load
    marker = MODELS_DIR / "ufd" / "clip_ready"
    marker.touch()  # _load checks nothing, but clip_status() requires the marker
    print("  fetching CLIP ViT-L/14 (OpenAI weights) via open_clip …")
    try:
        _load()
    except Exception:
        marker.unlink(missing_ok=True)
        raise
    print("  CLIP ready")


def setup_whisper() -> None:
    try:
        from faster_whisper import download_model
    except ImportError:
        raise SystemExit("faster-whisper not installed — run: uv sync --extra asr (from backend/)")
    out = MODELS_DIR / "whisper"
    download_model("small", cache_dir=str(out))
    (out / "ready").touch()
    print("  whisper small ready")


def main(argv: list[str]) -> None:
    yes = "--yes" in argv
    keys = [a for a in argv if not a.startswith("--")] or ["yunet"]
    ensure_dirs()
    for k in keys:
        if k not in SIZES:
            raise SystemExit(f"Unknown model key {k}. Choose from: {', '.join(SIZES)}")
        if not confirm(f"Download '{k}' ({SIZES[k]}) into {MODELS_DIR}?", yes):
            print(f"  skipped {k}")
            continue
        {"yunet": lambda: setup_files("yunet"), "ufd": setup_ufd, "whisper": setup_whisper,
         "spai": lambda: setup_files("spai"), "trufor": lambda: setup_files("trufor"),
         "sbi": lambda: setup_files("sbi")}[k]()


if __name__ == "__main__":
    main(sys.argv[1:])
