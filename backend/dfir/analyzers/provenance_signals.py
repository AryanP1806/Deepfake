"""Non-C2PA provenance signals: IPTC DigitalSourceType, generator parameter chunks,
open invisible watermarks, and explicit "cannot check" records for closed watermarks."""
from __future__ import annotations

import json
import math
import re

import numpy as np

from ..core.claims import NotRun, Polarity, Status, Strength
from .base import Analyzer, Ctx, register
from .metadata import GENERATOR_SOFTWARE

try:
    from imwatermark import WatermarkDecoder  # type: ignore
except Exception:  # noqa: BLE001
    WatermarkDecoder = None

# Fixed 48-bit message embedded by the Stability AI SDXL reference pipeline (generative-models repo).
SDXL_WM_BITS = [int(b) for b in bin(0b101100111110110010010000011110111011000110011110)[2:]]
SD1_WM_TEXT = b"StableDiffusionV1"  # embedded by the CompVis SD v1 txt2img reference script

def chance_agreement(k: int, n: int) -> float:
    """Probability that >= k of n random bits agree with a fixed message by chance."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


A1111_RE = re.compile(r"Steps:\s*\d+.*Sampler:", re.S)


def _parse_a1111(text: str) -> dict:
    out = {"raw": text[:3000]}
    m = re.search(r"\n?Steps:(.*)$", text, re.S)
    if m:
        out["prompt"] = text[: m.start()].split("Negative prompt:")[0].strip()[:1000]
        for part in re.findall(r"([A-Za-z][A-Za-z0-9 _\-]+):\s*([^,]+)", "Steps:" + m.group(1)):
            out[part[0].strip()] = part[1].strip()
    return out


@register
class ProvenanceSignalsAnalyzer(Analyzer):
    order = 30
    name = "provenance_signals"
    version = "1.0"
    modalities = ("image",)
    title = "Generator metadata & open watermarks"

    def run(self, ctx: Ctx):
        xmp: str = ctx.shared.get("xmp", "")
        png_text: dict = ctx.shared.get("png_text", {})
        exif: dict = ctx.shared.get("exif", {})
        found_any = False

        # 1. IPTC DigitalSourceType in XMP (unsigned declaration)
        m = re.search(r"DigitalSourceType[^>]*?(?:>|=\")\s*(?:https?://cv\.iptc\.org/newscodes/digitalsourcetype/)?"
                      r"([A-Za-z]+)", xmp or "")
        if m:
            found_any = True
            key = m.group(1)
            ai = "Algorithmic" in key or "Synthetic" in key
            ctx.claim("provenance", f"XMP declares IPTC DigitalSourceType = {key}", Status.VERIFIED,
                      strength=Strength.MODERATE if ai else Strength.WEAK,
                      polarity=Polarity.SUPPORTS_MANIPULATION if ai else Polarity.NEUTRAL,
                      why="Generators that follow IPTC guidance (e.g. Midjourney, Firefly, Meta AI) write this field.",
                      limitations="Unsigned metadata: it can be added or removed by anyone.",
                      data={"digitalSourceType": key, "generator_family": "declared AI source" if ai else None})

        # 2. Generator parameter chunks (A1111/Forge, ComfyUI, InvokeAI, NovelAI, Fooocus)
        candidates = dict(png_text)
        for k in ("UserComment", "ImageDescription", "XPComment"):
            if k in exif:
                candidates[f"EXIF.{k}"] = str(exif[k])
        for key, val in candidates.items():
            val = str(val)
            fam, detail = None, {}
            if key == "parameters" or A1111_RE.search(val):
                fam, detail = "Stable Diffusion (AUTOMATIC1111/Forge-style parameters)", _parse_a1111(val)
            elif key in ("prompt", "workflow") and val.strip().startswith("{"):
                fam = "Stable Diffusion / diffusion model via ComfyUI workflow"
                try:
                    wf = json.loads(val)
                    ckpts = sorted({str(n.get("inputs", {}).get("ckpt_name") or n.get("inputs", {}).get("unet_name"))
                                    for n in (wf.values() if isinstance(wf, dict) else [])
                                    if isinstance(n, dict) and (n.get("inputs", {}).get("ckpt_name") or
                                                                n.get("inputs", {}).get("unet_name"))})
                    detail = {"checkpoints": ckpts, "nodes": len(wf) if isinstance(wf, dict) else None}
                except json.JSONDecodeError:
                    detail = {"raw": val[:1000]}
            elif key in ("invokeai_metadata", "sd-metadata", "dream"):
                fam, detail = "InvokeAI", {"raw": val[:1500]}
            elif key in ("fooocus_scheme",) or "fooocus" in val.lower()[:200]:
                fam, detail = "Fooocus (SDXL-based)", {"raw": val[:1500]}
            elif "novelai" in val.lower() or (key == "Software" and "NovelAI" in val):
                fam, detail = "NovelAI", {"raw": val[:1500]}
            elif key in ("Software", "Source") and GENERATOR_SOFTWARE.search(val):
                fam, detail = val, {}
            if fam:
                found_any = True
                model = detail.get("Model") or detail.get("checkpoints")
                ctx.claim("provenance", f"Embedded generation parameters identify the tool family: {fam}"
                          + (f"; model/checkpoint: {model}" if model else ""), Status.VERIFIED,
                          strength=Strength.STRONG, polarity=Polarity.SUPPORTS_MANIPULATION,
                          why=f"The '{key}' metadata contains generator settings (prompt, sampler, seed, model) "
                              "written by the generation software itself.",
                          limitations="Can in principle be copied onto another image, but natural photographs essentially "
                                      "never carry sampler/seed parameters.",
                          confirm="Re-generate with the same seed/model to reproduce (where the model is available).",
                          data={"chunk": key, "generator_family": fam, **{k: v for k, v in detail.items() if k != "raw"},
                                "raw_excerpt": detail.get("raw", "")[:500]})

        # 3. Open invisible watermarks (DWT-DCT, Stable Diffusion reference pipelines)
        if WatermarkDecoder is None:
            ctx.claim("watermark", "Open invisible-watermark check not run (library missing)", Status.UNKNOWN)
        else:
            import cv2
            bgr = cv2.imread(str(ctx.path))
            if bgr is not None and min(bgr.shape[:2]) >= 256:
                hit = None
                try:
                    bits = WatermarkDecoder("bits", 48).decode(bgr, "dwtDct")
                    agree = int(np.sum(np.array(bits[:48]) == np.array(SDXL_WM_BITS)))
                    if agree >= 40:  # chance probability ~1e-6 or lower
                        hit = ("Stability AI SDXL reference-pipeline watermark", agree / 48,
                               chance_agreement(agree, 48))
                except Exception:  # noqa: BLE001
                    pass
                try:
                    raw = WatermarkDecoder("bytes", len(SD1_WM_TEXT) * 8).decode(bgr, "dwtDct")
                    if raw == SD1_WM_TEXT:
                        hit = ("Stable Diffusion v1 reference-script watermark ('StableDiffusionV1')", 1.0,
                               chance_agreement(136, 136))
                except Exception:  # noqa: BLE001
                    pass
                if hit:
                    found_any = True
                    ctx.claim("watermark", f"Decoded open invisible watermark: {hit[0]} (bit agreement {hit[1]:.0%}; "
                              f"probability of this agreement by chance ≈ {hit[2]:.1e})",
                              Status.VERIFIED, strength=Strength.STRONG, polarity=Polarity.SUPPORTS_MANIPULATION,
                              why="The DWT-DCT watermark payload used by Stability AI's reference code was recovered. "
                                  "Agreement at this level is extremely unlikely to occur by chance.",
                              limitations="Only images made with pipelines that kept the default watermarker carry it; "
                                          "many community tools disable it.",
                              data={"watermark": hit[0], "bit_accuracy": hit[1], "chance_probability": hit[2],
                                    "generator_family": "Stable Diffusion (Stability AI reference pipeline)"})
                else:
                    ctx.claim("watermark", "No open (DWT-DCT) Stable Diffusion watermark recovered", Status.UNKNOWN,
                              limitations="Absence does not indicate authenticity: the watermark is optional, fragile to "
                                          "resizing/recompression, and most generators use other or no watermarks.")
            else:
                ctx.claim("watermark", "Open watermark check skipped (image smaller than 256 px)", Status.UNKNOWN)

        if not found_any:
            ctx.claim("provenance", "No generator metadata or declared AI source type found", Status.UNKNOWN,
                      limitations="Absence does NOT indicate a camera origin: platforms strip metadata and most "
                                  "generators can be configured to write none.")


@register
class ClosedWatermarks(Analyzer):
    """Explicit record that proprietary watermarks could not be checked."""

    order = 35
    name = "closed_watermarks"
    version = "1.0"
    title = "Proprietary watermarks (SynthID etc.)"

    def available(self):
        return NotRun(component=self.name,
                      reason="Google SynthID and other proprietary watermarks have no public detector; not checked. "
                             "Absence of a result here says nothing about whether such a watermark is present.",
                      how_to_enable="Use the vendor's own verification portal where one is offered, with consent.")
