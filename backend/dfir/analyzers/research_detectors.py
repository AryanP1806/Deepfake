"""Research-grade detectors (vendored code in backend/third_party, weights pinned in models.lock.json).

* SPAI   — is the picture AI-generated? (any-resolution spectral detector, CVPR 2025, Apache-2.0)
* TruFor — has part of the picture been edited? integrity score + localisation map (CVPR 2023, research licence)
* SBI    — is a face swapped / blended in? per-face score (CVPR 2022, research licence)

All three run on ``ctx.shared["content_rgb"]`` — the embedded picture when the file is a screenshot.
Raw scores are recorded as evidence; ``verdict.py`` turns them into calibrated probabilities.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..core import model_store
from ..core.claims import NotRun, Polarity, Status, Strength
from ..core.config import BACKEND_ROOT
from ..core.gpu import models
from .base import Analyzer, Ctx, register
from .faces import detect_faces
from .media_io import load_image

TP = BACKEND_ROOT / "third_party"


def _content(ctx: Ctx) -> np.ndarray:
    if "content_rgb" not in ctx.shared:
        ctx.shared["content_rgb"] = np.asarray(load_image(ctx.path).convert("RGB"))
    return ctx.shared["content_rgb"]


def _need(key: str, filename: str) -> NotRun | None:
    p, reason = model_store.verified_path(key, filename)
    if p is None:
        return NotRun(component=key, reason=reason,
                      how_to_enable=f"uv run python ../scripts/setup_models.py {key}")
    try:
        import torch  # noqa: F401
    except ImportError:
        return NotRun(component=key, reason="PyTorch not installed", how_to_enable="uv sync --extra ml --extra detectors")
    return None


def _sha(key: str, filename: str) -> str:
    p, sha = model_store.verified_path(key, filename)
    return sha if p else ""


# --------------------------------------------------------------------------- SPAI
def _load_spai(device: str):
    import torch
    root = TP / "spai"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from spai.config import get_config
    from spai.models import build_cls_model
    cfg = get_config({"cfg": str(root / "configs" / "spai.yaml"), "batch_size": 1, "opts": ()})
    m = build_cls_model(cfg)
    ck = torch.load(model_store.model_path("spai", "spai.pth"), map_location="cpu", weights_only=False)
    m.load_state_dict(ck["model"], strict=True)
    return m.float().eval().to(device)


def spai_score(rgb: np.ndarray) -> float:
    import torch
    m, dev = models.get("spai", _load_spai, est_mb=1500)
    a = rgb.astype(np.float32) / 255.0
    h, w = a.shape[:2]
    if max(h, w) > 2048:  # bound VRAM; SPAI is any-resolution but memory grows with patches
        s = 2048 / max(h, w)
        a = cv2.resize(a, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        h, w = a.shape[:2]
    if h < 224 or w < 224:
        ph, pw = max(0, 224 - h), max(0, 224 - w)
        a = np.pad(a, ((ph // 2, ph - ph // 2), (pw // 2, pw - pw // 2), (0, 0)))
    x = torch.from_numpy(a.transpose(2, 0, 1).copy()).unsqueeze(0).to(dev)

    def fwd():
        with torch.no_grad():
            return torch.sigmoid(m([x], 64)).item()
    return float(models.run("spai", fwd))


@register
class SPAIDetector(Analyzer):
    name = "spai"
    version = "1.0"
    modalities = ("image",)
    title = "SPAI — AI-generated image detector"
    order = 61

    def available(self):
        return _need("spai", "spai.pth")

    def run(self, ctx: Ctx):
        rgb = _content(ctx)
        score = spai_score(rgb)
        ctx.shared["spai_score"] = score
        small = min(rgb.shape[:2]) < 256
        hi, lo = score >= 0.5, score <= 0.1
        ctx.claim("detector",
                  f"SPAI (AI-generated image detector): {'looks AI-generated' if hi else 'looks like a camera photo' if lo else 'unclear'} "
                  f"(model output {score:.2f})",
                  Status.INFERRED, strength=Strength.MODERATE if (score >= 0.9 or score <= 0.05) else Strength.WEAK,
                  polarity=Polarity.SUPPORTS_MANIPULATION if hi else Polarity.SUPPORTS_AUTHENTIC if lo else Polarity.NEUTRAL,
                  model_sha=_sha("spai", "spai.pth"),
                  why="SPAI learns the frequency spectrum of real camera photos and flags images whose spectrum it "
                      "cannot reconstruct — typical of image generators.",
                  limitations=model_store.SPECS["spai"].blind_spots + (" Very small image." if small else ""),
                  confirm="Agreement from an independent detector family or provenance data.",
                  data={"score": score, "model": "SPAI", "analysed": "embedded picture" if "content_box" in ctx.shared else "whole image",
                        "head": "ai_generated"})


# --------------------------------------------------------------------------- TruFor
def _load_trufor(device: str):
    import torch
    root = TP / "TruFor" / "TruFor_train_test"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from lib.config import config
    from lib.utils import get_model
    cfg = config.clone()
    cfg.defrost()
    cfg.merge_from_file(str(root / "lib" / "config" / "trufor_ph3.yaml"))
    cfg.MODEL.PRETRAINED = ""
    cfg.freeze()
    m = get_model(cfg)
    ck = torch.load(model_store.MODELS_DIR / "trufor" / "weights" / "trufor.pth.tar", map_location="cpu",
                    weights_only=False)
    m.load_state_dict(ck["state_dict"])
    return m.float().eval().to(device)


def trufor_run(rgb: np.ndarray) -> dict:
    import torch
    m, dev = models.get("trufor", _load_trufor, est_mb=1200)
    h, w = rgb.shape[:2]
    a = rgb
    if max(h, w) > 1280:
        s = 1280 / max(h, w)
        a = cv2.resize(rgb, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    x = torch.tensor(a.transpose(2, 0, 1), dtype=torch.float32)[None].to(dev) / 256.0

    def fwd():
        with torch.no_grad():
            pred, conf, det, _ = m(x)
        loc = torch.softmax(pred[0], 0)[1].cpu().numpy()
        cmap = torch.sigmoid(conf[0, 0]).cpu().numpy() if conf is not None else None
        return {"score": float(torch.sigmoid(det).item()), "map": loc, "conf": cmap}
    return models.run("trufor", fwd)


@register
class TruForDetector(Analyzer):
    name = "trufor"
    version = "1.0"
    modalities = ("image",)
    title = "TruFor — edit / tampering detector"
    order = 62

    def available(self):
        p = model_store.MODELS_DIR / "trufor" / "weights" / "trufor.pth.tar"
        if not p.exists():
            return NotRun(component=self.name, reason="TruFor weights not installed",
                          how_to_enable="uv run python ../scripts/setup_models.py trufor")
        return _need("trufor", "TruFor_weights.zip")

    def run(self, ctx: Ctx):
        rgb = _content(ctx)
        r = trufor_run(rgb)
        score = r["score"]
        ctx.shared["trufor_score"] = score
        loc = r["map"]
        frac = float((loc > 0.5).mean())
        heat = cv2.applyColorMap((np.clip(loc, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        heat = cv2.resize(heat, (rgb.shape[1], rgb.shape[0]))
        overlay = (0.55 * rgb + 0.45 * cv2.cvtColor(heat, cv2.COLOR_BGR2RGB)).astype(np.uint8)
        art = ctx.artifact("trufor_map", "Where edits are suspected (TruFor localisation)", Image.fromarray(overlay),
                           data={"score": score, "suspect_area": frac})
        ctx.artifact("trufor_heat", "TruFor localisation heatmap", Image.fromarray(cv2.cvtColor(heat, cv2.COLOR_BGR2RGB)),
                     data={"score": score})
        hi = score >= 0.5
        ctx.claim("tamper",
                  f"TruFor (edit detector): {'signs of local editing' if hi else 'no clear sign of local editing'} "
                  f"(integrity model output {score:.2f}; {frac:.0%} of the picture highlighted)",
                  Status.INFERRED, strength=Strength.MODERATE if score >= 0.8 else Strength.WEAK,
                  polarity=Polarity.SUPPORTS_MANIPULATION if hi else Polarity.NEUTRAL,
                  model_sha=_sha("trufor", "TruFor_weights.zip"),
                  why="TruFor combines the image with its camera-noise fingerprint (Noiseprint++) to find regions whose "
                      "noise does not match the rest — the trace of splicing, copy-move or inpainting.",
                  limitations=model_store.SPECS["trufor"].blind_spots,
                  evidence=[art.sha256], data={"score": score, "suspect_area": frac, "model": "TruFor", "head": "edited"})


# --------------------------------------------------------------------------- SBI
def _load_sbi(device: str):
    import torch
    from efficientnet_pytorch import EfficientNet
    net = EfficientNet.from_name("efficientnet-b4", num_classes=2)
    sd = torch.load(model_store.model_path("sbi", "FFc23.tar"), map_location="cpu", weights_only=False)["model"]
    net.load_state_dict({k.replace("net.", "", 1): v for k, v in sd.items()})
    return net.float().eval().to(device)


def sbi_faces(rgb: np.ndarray) -> list[dict]:
    """Per-face fakeness following the SBI test-time crop (bbox + 1/4 margin, 380 px, /255)."""
    import torch
    faces = detect_faces(rgb) or []
    if not faces:
        return []
    m, dev = models.get("sbi", _load_sbi, est_mb=500)
    H, W = rgb.shape[:2]
    out = []
    for f in faces:
        x, y, w, h = f["box"]
        if min(w, h) < 24:
            continue
        x0, y0 = int(max(0, x - w / 4)), int(max(0, y - h / 4))
        x1, y1 = int(min(W, x + w + w / 4)), int(min(H, y + h + h / 4))
        crop = cv2.resize(rgb[y0:y1, x0:x1], (380, 380), interpolation=cv2.INTER_LINEAR)
        t = torch.tensor(crop.transpose(2, 0, 1))[None].float().to(dev) / 255.0

        def fwd():
            with torch.no_grad():
                return float(m(t).softmax(1)[0, 1].item())
        out.append({"box": [x, y, w, h], "score": models.run("sbi", fwd), "face_px": int(min(w, h))})
    return out


@register
class SBIDetector(Analyzer):
    name = "sbi"
    version = "1.0"
    modalities = ("image",)
    title = "SBI — face-swap detector"
    order = 63

    def available(self):
        if model_store.verified_path("yunet", "face_detection_yunet_2023mar.onnx")[0] is None:
            return NotRun(component=self.name, reason="face detector (YuNet) not installed",
                          how_to_enable="uv run python ../scripts/setup_models.py yunet")
        return _need("sbi", "FFc23.tar")

    def run(self, ctx: Ctx):
        rgb = _content(ctx)
        faces = sbi_faces(rgb)
        ctx.shared["sbi_faces"] = faces
        if not faces:
            ctx.claim("faceswap", "No usable face found for the face-swap check", Status.UNKNOWN,
                      limitations="Needs a visible face of at least ~24 px.")
            return
        top = max(faces, key=lambda f: f["score"])
        hi = top["score"] >= 0.5
        ctx.claim("faceswap",
                  f"SBI (face-swap detector): {'a face shows blending traces typical of face swaps' if hi else 'no face-swap blending traces'} "
                  f"(highest face output {top['score']:.2f} across {len(faces)} face(s))",
                  Status.INFERRED, strength=Strength.MODERATE if top["score"] >= 0.85 else Strength.WEAK,
                  polarity=Polarity.SUPPORTS_MANIPULATION if hi else Polarity.NEUTRAL,
                  model_sha=_sha("sbi", "FFc23.tar"),
                  why="SBI was trained to spot the boundary left when one face is blended onto another — without "
                      "ever seeing a specific deepfake tool, so it also reacts to unseen face-swap methods.",
                  limitations=model_store.SPECS["sbi"].blind_spots + " Faces generated entirely by AI (no blending) "
                              "can also score high.",
                  data={"faces": faces, "max_score": top["score"], "model": "SBI (FF++ c23)", "head": "edited"})
