"""Shared CLIP ViT-L/14 backend (used by UniversalFakeDetect and genealogy embeddings)."""
from __future__ import annotations

import os
import threading

import numpy as np
from PIL import Image

from ..core import model_store

_lock = threading.Lock()
_state: dict = {}


def runtime_info() -> dict:
    """Device/precision of the loaded model (empty if not loaded yet)."""
    from ..core.gpu import models
    if not any(m["key"] == "clip" for m in models.status()):
        return {"loaded": False}
    return {"loaded": True, "device": _state.get("device"), "precision": "fp16" if _state.get("half") else "fp32"}


def clip_status() -> tuple[bool, str]:
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
    except Exception:  # noqa: BLE001
        return False, "torch/open_clip not installed (uv sync --extra ml)"
    marker = model_store.MODELS_DIR / "ufd" / "clip_ready"
    if not marker.exists():
        return False, "CLIP ViT-L/14 weights not downloaded (run scripts/setup_models.py ufd)"
    return True, ""


def _build(dev: str) -> dict:
    import open_clip
    import torch
    spec = model_store.SPECS["ufd"]
    model, _, preprocess = open_clip.create_model_and_transforms(
        spec.extra["clip_arch"], pretrained=spec.extra["clip_pretrained"],
        cache_dir=str(model_store.MODELS_DIR / "ufd" / "clip"))
    # FP32 by default: on Turing TU117 GPUs (e.g. Quadro T2000) FP16 matmuls are ~4x slower than FP32.
    half = dev == "cuda" and os.environ.get("DFIR_CLIP_FP16") == "1"
    model = (model.half() if half else model.float()).eval().to(dev)
    return {"model": model, "preprocess": preprocess, "device": dev, "torch": torch, "half": half}


def _load() -> dict:
    """CLIP via the shared GPU model manager (evicted when other detectors need the VRAM)."""
    from ..core.gpu import models
    st, _dev = models.get("clip", _build, est_mb=1750)
    _state.clear()
    _state.update(st)
    return st


def embed(images: list[Image.Image], *, crops: int = 1) -> np.ndarray:
    """L2-normalised CLIP image embeddings, shape (n, 768). ``crops`` > 1 averages a 5-crop."""
    st = _load()
    torch = st["torch"]
    feats = []
    for im in images:
        im = im.convert("RGB")
        views = [im] if crops == 1 else _five_crop(im)
        x = torch.stack([st["preprocess"](v) for v in views]).to(st["device"])
        if st["half"]:
            x = x.half()
        with torch.no_grad():
            f = st["model"].encode_image(x).float()
        f = f / f.norm(dim=-1, keepdim=True)
        feats.append(f.cpu().numpy())
    return np.stack([f.mean(0) if crops > 1 else f[0] for f in feats])


def raw_features(images: list[Image.Image]) -> list[np.ndarray]:
    """Un-normalised features per 5-crop view (for linear probes trained on raw CLIP features)."""
    st = _load()
    torch = st["torch"]
    out = []
    for im in images:
        x = torch.stack([_ufd_view(v) for v in _five_crop(im.convert("RGB"))]).to(st["device"])
        if st["half"]:
            x = x.half()
        with torch.no_grad():
            out.append(st["model"].encode_image(x).float().cpu().numpy())
    return out


def _five_crop(im: Image.Image, size: int = 224) -> list[Image.Image]:
    w, h = im.size
    if min(w, h) < size:
        s = size / min(w, h)
        im = im.resize((max(size, round(w * s)), max(size, round(h * s))), Image.BICUBIC)
        w, h = im.size
    # UFD was trained on ~256px images with a 224 centre crop; downscale large inputs to keep scale comparable
    if min(w, h) > 512:
        s = 512 / min(w, h)
        im = im.resize((round(w * s), round(h * s)), Image.BICUBIC)
        w, h = im.size
    boxes = [((w - size) // 2, (h - size) // 2), (0, 0), (w - size, 0), (0, h - size), (w - size, h - size)]
    return [im.crop((x, y, x + size, y + size)) for x, y in boxes]


_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def _ufd_view(im: Image.Image):
    import torch
    a = (np.asarray(im, dtype=np.float32) / 255.0 - _MEAN) / _STD
    return torch.from_numpy(a.transpose(2, 0, 1).copy())
