"""Local learned detectors. Outputs are treated as uncalibrated evidence, never verdicts."""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..core import model_store
from ..core.claims import NotRun, Polarity, Status, Strength
from .base import Analyzer, Ctx, register
from .clip_backend import clip_status, raw_features
from .media_io import load_image

_probe: dict = {}


def ufd_available() -> tuple[bool, str]:
    ok, reason = clip_status()
    if not ok:
        return ok, reason
    p, reason = model_store.verified_path("ufd", "ufd_fc_weights.pth")
    return (p is not None), ("" if p else reason)


def ufd_scores(images: list[Image.Image]) -> tuple[list[float], list[float], str]:
    """Return (sigmoid score per image, 5-crop spread per image, probe sha)."""
    import torch
    if "w" not in _probe:
        p, sha = model_store.verified_path("ufd", "ufd_fc_weights.pth")
        sd = torch.load(p, map_location="cpu", weights_only=True)
        _probe.update(w=sd["weight"].float().numpy(), b=sd["bias"].float().numpy(), sha=sha)
    scores, spreads = [], []
    for feats in raw_features(images):
        logits = feats @ _probe["w"].T + _probe["b"]
        probs = 1 / (1 + np.exp(-logits.ravel()))
        scores.append(float(np.mean(probs)))
        spreads.append(float(np.std(probs)))
    return scores, spreads, _probe["sha"]


def score_to_evidence(score: float, spread: float) -> tuple[Strength, Polarity, str]:
    if spread > 0.25:
        return Strength.WEAK, Polarity.NEUTRAL, "inconsistent across crops (inconclusive)"
    if score >= 0.9:
        return Strength.MODERATE, Polarity.SUPPORTS_MANIPULATION, "strongly resembles its synthetic training class"
    if score >= 0.7:
        return Strength.WEAK, Polarity.SUPPORTS_MANIPULATION, "leans toward its synthetic training class"
    if score <= 0.1:
        return Strength.WEAK, Polarity.SUPPORTS_AUTHENTIC, "resembles its real-photo training class"
    return Strength.WEAK, Polarity.NEUTRAL, "falls between classes (inconclusive)"


@register
class UniversalFakeDetect(Analyzer):
    order = 60
    name = "ufd_clip"
    version = "1.0"
    modalities = ("image",)
    title = "UniversalFakeDetect (CLIP linear probe)"

    def available(self):
        import os
        if os.environ.get("DFIR_ENABLE_UFD") != "1":
            return NotRun(component=self.name,
                          reason="Superseded by SPAI (UFD is a 2023 GAN-era model with low accuracy on modern generators); "
                                 "disabled to keep GPU memory for stronger detectors.",
                          how_to_enable="Set DFIR_ENABLE_UFD=1 to run it as an extra signal")
        ok, reason = ufd_available()
        if not ok:
            return NotRun(component=self.name, reason=reason,
                          how_to_enable="uv sync --extra ml, then: uv run python ../scripts/setup_models.py ufd")
        return None

    def run(self, ctx: Ctx):
        rgb = ctx.shared.get("content_rgb")
        im = Image.fromarray(rgb) if rgb is not None else load_image(ctx.path).convert("RGB")
        (score,), (spread,), sha = ufd_scores([im])
        strength, pol, desc = score_to_evidence(score, spread)
        small = min(im.size) < 256
        ctx.claim("detector",
                  f"UniversalFakeDetect: image {desc} (uncalibrated model output {score:.2f}, 5-crop spread {spread:.2f})",
                  Status.INFERRED, strength=strength, polarity=pol, model_sha=sha,
                  why="A linear probe on CLIP features separates real photos from generated images it was trained on.",
                  limitations=model_store.SPECS["ufd"].blind_spots + (" Image is smaller than 256 px — reliability is "
                                                                      "further reduced." if small else ""),
                  confirm="Corroborate with an independent detector family (e.g. a commercial API with consent) or "
                          "provenance evidence.",
                  data={"score": score, "spread": spread, "model": "UniversalFakeDetect ViT-L/14", "small_input": small})
