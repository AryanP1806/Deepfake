"""OCR of burned-in text: captions, platform watermarks, @handles, URLs."""
from __future__ import annotations

import re

import numpy as np

from ..core.claims import NotRun, Status, Strength
from .base import Analyzer, Ctx, register
from .image_forensics import _prep

try:
    from rapidocr_onnxruntime import RapidOCR  # type: ignore
    _OCR_ERR = ""
except Exception as e:  # noqa: BLE001
    RapidOCR = None
    _OCR_ERR = str(e)

_engine = None
HANDLE_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9_.]{3,30})")
URL_RE = re.compile(r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+\.(?:com|in|net|org|me|io|tv|co|app|xyz|link|ly)(?:/[^\s]*)?)",
                    re.I)
PLATFORM_MARKS = {
    "tiktok": "TikTok", "reels": "Instagram Reels", "instagram": "Instagram", "youtube": "YouTube",
    "shorts": "YouTube Shorts", "snapchat": "Snapchat", "moj": "Moj", "josh": "Josh", "sharechat": "ShareChat",
    "telegram": "Telegram", "kwai": "Kwai", "likee": "Likee", "facebook": "Facebook", "capcut": "CapCut",
    "made with ai": "AI label", "ai generated": "AI label", "ai info": "AI label",
}


def get_engine():
    global _engine
    if _engine is None and RapidOCR is not None:
        _engine = RapidOCR()
    return _engine


def ocr_image(rgb: np.ndarray) -> list[dict]:
    eng = get_engine()
    if eng is None:
        return []
    result, _ = eng(rgb[:, :, ::-1].copy())
    out = []
    for box, text, conf in result or []:
        out.append({"text": text, "conf": float(conf), "box": [[float(x), float(y)] for x, y in box]})
    return out


def extract_entities(texts: list[str]) -> dict:
    joined = " \n".join(texts)
    low = joined.lower()
    return {
        "handles": sorted({h.rstrip(".") for h in HANDLE_RE.findall(joined)}),
        "urls": sorted({u for u in URL_RE.findall(joined)}),
        "platform_marks": sorted({v for k, v in PLATFORM_MARKS.items() if k in low}),
    }


@register
class OCRAnalyzer(Analyzer):
    name = "ocr"
    version = "1.0"
    modalities = ("image", "video")
    title = "OCR (captions, watermarks, handles)"
    order = 80

    def available(self):
        if RapidOCR is None:
            return NotRun(component=self.name, reason=f"RapidOCR not available: {_OCR_ERR[:120]}",
                          how_to_enable="uv sync --extra ocr")
        return None

    def run(self, ctx: Ctx):
        lines: list[dict] = []
        if ctx.media.modality == "image":
            lines = [{**l, "t": None} for l in ocr_image(_prep(ctx))]
        else:
            frames = ctx.shared.get("frames") or []
            seen = set()
            for t, im in frames[:: max(1, len(frames) // 8)]:
                for l in ocr_image(np.asarray(im)):
                    if l["text"] not in seen:
                        seen.add(l["text"])
                        lines.append({**l, "t": t})
        lines = [l for l in lines if l["conf"] >= 0.6]
        ctx.shared["ocr_lines"] = lines
        ents = extract_entities([l["text"] for l in lines])
        ctx.shared["ocr_entities"] = ents
        ctx.artifact("ocr", f"{len(lines)} text line(s)", __import__("json").dumps(
            {"lines": lines, "entities": ents}, indent=2).encode(), mime="application/json")
        if not lines:
            ctx.claim("ocr", "No burned-in text detected", Status.UNKNOWN)
            return
        ctx.claim("ocr", f"Burned-in text read from the media ({len(lines)} line(s)): "
                  + " | ".join(l["text"] for l in lines[:6]), Status.VERIFIED, strength=Strength.WEAK,
                  why="Text overlays (captions, watermarks) were read directly from the pixels.",
                  limitations="OCR can misread stylised fonts; verify against the image.",
                  data={"lines": [l["text"] for l in lines]})
        for h in ents["handles"]:
            ctx.claim("ocr", f"Handle “@{h}” is visible in the media", Status.LEAD, strength=Strength.WEAK,
                      why="Platforms (e.g. TikTok, Reels) burn the uploader's handle into downloaded videos; reposters "
                          "also add their own handles.",
                      limitations="A visible handle shows that the name was overlaid at some stage — it may belong to "
                                  "an earlier uploader, a reposter, or be deliberately misleading. It does not show who "
                                  "made the content.",
                      confirm="Locate the account and capture its public post of this media.",
                      data={"handle": h})
        for p in ents["platform_marks"]:
            ctx.claim("ocr", f"Platform marker visible in media: {p}", Status.LEAD, strength=Strength.WEAK,
                      limitations="Indicates the media passed through that platform at some point.",
                      data={"platform_mark": p})
