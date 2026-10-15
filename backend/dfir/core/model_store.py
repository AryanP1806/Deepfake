"""Registry of downloadable model weights, pinned in ``models.lock.json``.

Weights are only fetched by ``scripts/setup_models.py`` (run with the user's approval).
On first download the SHA-256 is recorded (trust-on-first-use); later loads verify it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import MODELS_DIR, PROJECT_ROOT

LOCK_FILE = PROJECT_ROOT / "models.lock.json"


@dataclass
class ModelSpec:
    key: str
    title: str
    files: dict[str, str]  # local filename -> URL
    licence: str
    size_note: str
    trained_on: str
    blind_spots: str
    used_by: str
    extra: dict = field(default_factory=dict)


SPECS: dict[str, ModelSpec] = {
    "yunet": ModelSpec(
        key="yunet", title="OpenCV YuNet face detector (2023mar)",
        files={"face_detection_yunet_2023mar.onnx":
               "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"},
        licence="MIT", size_note="~230 KB", trained_on="WIDER FACE",
        blind_spots="Small/profile/occluded faces; not a manipulation detector.", used_by="faces"),
    "ufd": ModelSpec(
        key="ufd", title="UniversalFakeDetect linear probe (Ojha et al., CVPR 2023) on CLIP ViT-L/14",
        files={"ufd_fc_weights.pth":
               "https://github.com/WisconsinAIVision/UniversalFakeDetect/raw/main/pretrained_weights/fc_weights.pth"},
        licence="See upstream repository", size_note="probe ~4 KB + CLIP ViT-L/14 ~1.7 GB (open_clip, OpenAI weights)",
        trained_on="ProGAN real/fake (ForenSynths); generalises partially to other GANs and diffusion models",
        blind_spots="Lower accuracy on recent diffusion models, heavy recompression, screenshots and small images; "
                    "uncalibrated — outputs are not probabilities.",
        used_by="detectors_local", extra={"clip_arch": "ViT-L-14-quickgelu", "clip_pretrained": "openai"}),
    "spai": ModelSpec(
        key="spai", title="SPAI — any-resolution spectral AI-generated image detector (CVPR 2025)",
        files={"spai.pth": "gdrive:1vvXmZqs6TVJdj8iF1oJ4L_fcgdQrp_YI"},
        licence="Apache-2.0", size_note="~400 MB (ViT-B/16)",
        trained_on="Real photos vs latent-diffusion images; evaluated on 13 generators incl. Flux, Midjourney v5/v6.1, "
                   "DALL-E 3, SD3, Firefly (avg AUC 91)",
        blind_spots="Weaker on SD3/Flux and on derivative images (screenshots, memes, photos of screens); "
                    "does not detect local edits or face swaps.",
        used_by="spai", extra={"code": "third_party/spai", "commit": "8ff7b3b6779b4fcb43cf313471d9cb1c62d129a4"}),
    "trufor": ModelSpec(
        key="trufor", title="TruFor — forgery detection & localisation (CVPR 2023)",
        files={"TruFor_weights.zip": "https://www.grip.unina.it/download/prog/TruFor/TruFor_weights.zip"},
        licence="GRIP research licence — informational / non-profit use only", size_note="~250 MB",
        trained_on="Splicing, copy-move and inpainting manipulations on camera images (Noiseprint++ + RGB transformer)",
        blind_spots="Fully AI-generated images may look 'consistent' (no splice boundary); heavy recompression and "
                    "resizing reduce sensitivity.",
        used_by="trufor", extra={"code": "third_party/TruFor", "commit": "ae54475df6f41a491d7615100feb19263dec13f7",
                                 "unzip": True}),
    "sbi": ModelSpec(
        key="sbi", title="SBI — Self-Blended Images face-forgery detector (CVPR 2022), FF++ c23 weights",
        files={"FFc23.tar": "gdrive:1X0-NYT8KPursLZZdxduRQju6E52hauV0"},
        licence="Research use only (commercial use requires a licence from the authors)", size_note="~150 MB (EfficientNet-B4)",
        trained_on="Real FaceForensics++ faces with synthetic self-blending (never saw a real deepfake method), "
                   "so it generalises to unseen face-swap techniques",
        blind_spots="Needs a reasonably sized, frontal face; fully generated faces (no blending boundary) and tiny or "
                    "heavily compressed faces are harder.",
        used_by="sbi", extra={"code": "third_party/SelfBlendedImages", "commit": "d6d9a351f32334ae9a3a250872c05836e0dd0f2c"}),
}


def lock() -> dict:
    if LOCK_FILE.exists():
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    return {}


def write_lock(data: dict) -> None:
    LOCK_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


_SHA_CACHE: dict[tuple[str, int, int], str] = {}


def sha256_file(p: Path) -> str:
    """SHA-256 of a file, cached by (path, size, mtime) — weight files are large and checked often."""
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)
    if key in _SHA_CACHE:
        return _SHA_CACHE[key]
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def model_path(key: str, filename: str) -> Path:
    return MODELS_DIR / key / filename


def status(key: str) -> dict:
    spec = SPECS[key]
    lk = lock().get(key, {})
    files = {}
    for fn in spec.files:
        p = model_path(key, fn)
        files[fn] = {"present": p.exists(), "pinned_sha256": lk.get("files", {}).get(fn)}
    return {"key": key, "title": spec.title, "installed": all(f["present"] for f in files.values()),
            "files": files, "licence": spec.licence, "trained_on": spec.trained_on,
            "blind_spots": spec.blind_spots, "size": spec.size_note}


def verified_path(key: str, filename: str) -> tuple[Path | None, str]:
    """Return (path, sha) if present and matching the lock; (None, reason) otherwise."""
    p = model_path(key, filename)
    if not p.exists():
        return None, f"model file {key}/{filename} not installed"
    pinned = lock().get(key, {}).get("files", {}).get(filename)
    actual = sha256_file(p)
    if pinned and pinned != actual:
        return None, f"model file {key}/{filename} does not match pinned SHA-256 (possible tampering)"
    return p, actual
