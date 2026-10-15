"""Face detection and simple face-region consistency statistics."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..core import model_store
from ..core.claims import NotRun, Polarity, Status, Strength
from .base import Analyzer, Ctx, register
from .image_forensics import _prep, ela

_detector_cache: dict = {}


def detect_faces(rgb: np.ndarray) -> list[dict] | None:
    """Return faces [{box:[x,y,w,h], score, landmarks}] or None if the detector is not installed."""
    path, sha = model_store.verified_path("yunet", "face_detection_yunet_2023mar.onnx")
    if path is None:
        return None
    h, w = rgb.shape[:2]
    det = _detector_cache.get("yunet")
    if det is None:
        det = cv2.FaceDetectorYN.create(str(path), "", (w, h), 0.7, 0.3, 5000)
        _detector_cache["yunet"] = det
    det.setInputSize((w, h))
    _, faces = det.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    out = []
    for f in faces if faces is not None else []:
        x, y, fw, fh = [int(v) for v in f[:4]]
        out.append({"box": [max(0, x), max(0, y), fw, fh], "score": float(f[-1]),
                    "landmarks": [[float(f[4 + 2 * i]), float(f[5 + 2 * i])] for i in range(5)]})
    return out


def face_model_sha() -> str:
    p, sha = model_store.verified_path("yunet", "face_detection_yunet_2023mar.onnx")
    return sha if p else ""


@register
class FaceAnalyzer(Analyzer):
    order = 50
    name = "faces"
    version = "1.0"
    modalities = ("image",)
    title = "Face detection & face-region consistency"

    def available(self):
        p, reason = model_store.verified_path("yunet", "face_detection_yunet_2023mar.onnx")
        if p is None:
            return NotRun(component=self.name, reason=reason,
                          how_to_enable="Run: uv run python ../scripts/setup_models.py yunet")
        return None

    def run(self, ctx: Ctx):
        rgb = _prep(ctx)
        faces = detect_faces(rgb) or []
        ctx.shared["faces"] = faces
        if not faces:
            ctx.claim("faces", "No faces detected", Status.UNKNOWN, model_sha=face_model_sha(),
                      limitations="Small, profile or occluded faces may be missed.")
            return
        ela_map = ela(rgb)
        g = rgb.astype(np.float32)
        overlay = Image.fromarray(rgb).copy()
        draw = ImageDraw.Draw(overlay)
        stats = []
        for i, f in enumerate(faces):
            x, y, w, h = f["box"]
            face = g[y:y + h, x:x + w]
            if face.size == 0:
                continue
            pad = int(0.35 * max(w, h))
            y0, y1, x0, x1 = max(0, y - pad), min(rgb.shape[0], y + h + pad), max(0, x - pad), min(rgb.shape[1], x + w + pad)
            ring_mask = np.ones((y1 - y0, x1 - x0), bool)
            ring_mask[y - y0:y - y0 + h, x - x0:x - x0 + w] = False
            surround = g[y0:y1, x0:x1][ring_mask]
            # colour statistics: face vs surrounding skin/background (blending mismatch)
            col_diff = float(np.linalg.norm(face.reshape(-1, 3).std(0) - surround.reshape(-1, 3).std(0)))
            ela_face = float(ela_map[y:y + h, x:x + w].mean())
            ela_ring = float(ela_map[y0:y1, x0:x1][ring_mask].mean()) + 1e-3
            sharp_face = float(cv2.Laplacian(cv2.cvtColor(rgb[y:y + h, x:x + w], cv2.COLOR_RGB2GRAY), cv2.CV_32F).var())
            sharp_ring = float(cv2.Laplacian(cv2.cvtColor(rgb[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY), cv2.CV_32F).var()) + 1e-3
            s = {"face": i, "box": f["box"], "det_score": f["score"], "ela_ratio": ela_face / ela_ring,
                 "sharpness_ratio": sharp_face / sharp_ring, "color_std_diff": col_diff}
            stats.append(s)
            flagged = s["ela_ratio"] > 2.0 or s["ela_ratio"] < 0.4 or s["sharpness_ratio"] < 0.25
            draw.rectangle([x, y, x + w, y + h], outline=(255, 80, 80) if flagged else (80, 220, 120), width=3)
            draw.text((x + 4, y + 4), f"face {i}", fill=(255, 255, 255))
        ctx.shared["face_stats"] = stats
        art = ctx.artifact("faces", f"{len(faces)} face(s) detected", overlay, data={"faces": stats})
        for s in stats:
            odd = []
            if s["ela_ratio"] > 2.0 or s["ela_ratio"] < 0.4:
                odd.append(f"face recompresses differently from its surroundings (ELA ratio {s['ela_ratio']:.2f})")
            if s["sharpness_ratio"] < 0.25:
                odd.append(f"face is much blurrier than its surroundings (sharpness ratio {s['sharpness_ratio']:.2f})")
            ctx.claim("faces",
                      f"Face {s['face']}: " + ("; ".join(odd) if odd else "no blending/sharpness inconsistency found by "
                                                                       "simple statistics"),
                      Status.INFERRED, strength=Strength.WEAK,
                      polarity=Polarity.SUPPORTS_MANIPULATION if odd else Polarity.NEUTRAL,
                      why="Face-swap and face-reenactment pipelines blend a generated face into a host frame, which can "
                          "leave mismatched compression, sharpness or colour at the face boundary.",
                      limitations="Depth of field, beauty filters and lighting produce the same statistics. Not a "
                                  "face-swap classifier.",
                      evidence=[art.sha256], model_sha=face_model_sha(), data=s)
