"""Screenshot / post-capture detection: find the actual picture inside a screenshot of a social-media
post (UI chrome, captions, letterboxing) so detectors analyse the picture, not the app around it.

Why: AI-image detectors read subtle pixel statistics. Flat UI areas and rendered text dominate those
statistics and push detectors toward "real". On the user's Modi example SPAI scored the whole
screenshot 0.00 and the embedded picture 0.9997.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..core.claims import Status, Strength
from .base import Analyzer, Ctx, register
from .media_io import load_image


def _ui_colors(rgb: np.ndarray) -> np.ndarray:
    """Dominant colours along the outer border (likely app background / letterbox colours)."""
    h, w = rgb.shape[:2]
    b = max(2, min(h, w) // 100)
    border = np.concatenate([rgb[:b].reshape(-1, 3), rgb[-b:].reshape(-1, 3), rgb[:, :b].reshape(-1, 3),
                             rgb[:, -b:].reshape(-1, 3)]).astype(np.int32)
    q = (border // 12) * 12 + 6
    keys, counts = np.unique(q, axis=0, return_counts=True)
    order = np.argsort(-counts)
    total = counts.sum()
    return np.array([keys[i] for i in order[:4] if counts[i] / total > 0.08])


def find_content_box(rgb: np.ndarray) -> tuple[tuple[int, int, int, int] | None, dict]:
    """Return (box (x0, y0, x1, y1) or None, diagnostics). None means: use the whole image."""
    h, w = rgb.shape[:2]
    info: dict = {"size": [w, h]}
    if min(h, w) < 200:
        return None, {**info, "reason": "too small"}
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    mu = cv2.blur(gray, (5, 5))
    std = np.sqrt(np.maximum(cv2.blur(gray * gray, (5, 5)) - mu * mu, 0))
    ui = _ui_colors(rgb)
    info["ui_colors"] = ui.tolist()
    not_ui = np.ones((h, w), bool)
    for c in ui:
        not_ui &= np.abs(rgb.astype(np.int32) - c).max(axis=2) > 22
    mask = (not_ui | (std > 10)).astype(np.uint8)
    k = max(3, min(h, w) // 120) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))       # drop thin text strokes
    big = max(9, min(h, w) // 25) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((big, big), np.uint8))  # fill smooth photo areas
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return None, {**info, "reason": "no content region"}
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh, area = stats[i]
    frac = (bw * bh) / (w * h)
    info.update(box=[int(x), int(y), int(x + bw), int(y + bh)], box_frac=round(float(frac), 3),
                fill=round(float(area / max(1, bw * bh)), 3))
    if frac > 0.9 or frac < 0.15 or area / max(1, bw * bh) < 0.6:
        return None, {**info, "reason": "picture fills the frame (not a screenshot)" if frac > 0.9 else "no clear picture region"}
    outside = np.ones((h, w), bool)
    outside[y:y + bh, x:x + bw] = False
    flat_outside = float((std[outside] < 6).mean())
    info["flat_outside"] = round(flat_outside, 3)
    if flat_outside < 0.6:
        return None, {**info, "reason": "surroundings are photographic, not UI"}
    pad = 2  # trim anti-aliased edge
    return (int(x + pad), int(y + pad), int(x + bw - pad), int(y + bh - pad)), {**info, "reason": "screenshot"}


@register
class ContentRegion(Analyzer):
    name = "content_region"
    version = "1.0"
    modalities = ("image",)
    title = "Screenshot detection (find the embedded picture)"
    order = 8

    def run(self, ctx: Ctx):
        im = load_image(ctx.path).convert("RGB")
        rgb = np.asarray(im)
        box, info = find_content_box(rgb)
        ctx.shared["content_info"] = info
        if box is None:
            ctx.shared["content_rgb"] = rgb
            return None
        x0, y0, x1, y1 = box
        ctx.shared["content_rgb"] = rgb[y0:y1, x0:x1].copy()
        ctx.shared["content_box"] = box
        ov = im.copy()
        d = ImageDraw.Draw(ov)
        d.rectangle(box, outline=(80, 200, 255), width=max(2, min(im.size) // 150))
        art = ctx.artifact("content_box", "Embedded picture found in screenshot", ov,
                           data={"box": list(box), "size": list(im.size)})
        ctx.claim("screenshot",
                  f"This looks like a screenshot of a post: the picture occupies {info['box_frac']:.0%} of the file "
                  f"and is surrounded by app/caption areas. AI-image detectors analysed the embedded picture "
                  f"(box {x0},{y0}–{x1},{y1}), not the app around it.",
                  Status.INFERRED, strength=Strength.WEAK,
                  why="Detectors read fine pixel statistics; flat UI areas and rendered text would dominate them.",
                  limitations="A screenshot is a re-rendered copy: resizing and re-compression weaken every detector. "
                              "The original upload, if it can be found, is better evidence.",
                  confirm="Find and analyse the original image file (see Propagation).",
                  evidence=[art.sha256], data={"box": list(box), **{k: v for k, v in info.items() if k != "ui_colors"}})
