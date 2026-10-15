"""Container / EXIF / XMP / quantisation-table metadata analysis."""
from __future__ import annotations

import hashlib
import re

import numpy as np
from PIL import ExifTags, Image

from ..core.claims import Polarity, Status, Strength
from .base import Analyzer, Ctx, register
from .media_io import ffprobe, ffprobe_bin

# IJG standard luminance quantisation table (quality 50), natural order.
_STD_LUMA = np.array([
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99], dtype=float)

EDITING_SOFTWARE = re.compile(
    r"photoshop|lightroom|gimp|affinity|pixelmator|snapseed|picsart|facetune|canva|faceapp|remini|"
    r"capcut|kinemaster|inshot|premiere|after effects|davinci|final cut|vn video|meitu|b612|faceswap|reface|"
    r"deepfacelab|facefusion|roop", re.I)
GENERATOR_SOFTWARE = re.compile(
    r"stable diffusion|automatic1111|comfyui|invokeai|fooocus|midjourney|dall[-·]?e|firefly|imagen|"
    r"flux|leonardo|ideogram|runway|sora|kling|pika|luma|hailuo|veo|novelai|nightcafe|playground|krea|"
    r"gemini|grok|aurora|openai|bing image creator|designer", re.I)


def estimate_jpeg_quality(qtable: list[int]) -> tuple[int, float]:
    """Estimate IJG quality from a luminance table. Returns (quality, residual).

    A large residual means the encoder did not use IJG-scaled tables — itself a software
    fingerprint (many cameras, Photoshop and some platforms use custom tables).
    """
    q = np.array(qtable[:64], dtype=float)
    best = (0, 1e9)
    for quality in range(1, 101):
        scale = 5000 / quality if quality < 50 else 200 - 2 * quality
        t = np.clip(np.floor((_STD_LUMA * scale + 50) / 100), 1, 255)
        err = float(np.mean(np.abs(t - q)))
        if err < best[1]:
            best = (quality, err)
    return best


def _exif_dict(im: Image.Image) -> dict:
    out = {}
    try:
        exif = im.getexif()
    except Exception:  # noqa: BLE001
        return out
    for k, v in exif.items():
        out[ExifTags.TAGS.get(k, str(k))] = v
    for ifd_id in (ExifTags.IFD.Exif, ExifTags.IFD.GPSInfo):
        try:
            sub = exif.get_ifd(ifd_id)
        except Exception:  # noqa: BLE001
            continue
        for k, v in sub.items():
            name = (ExifTags.GPSTAGS if ifd_id == ExifTags.IFD.GPSInfo else ExifTags.TAGS).get(k, str(k))
            out[("GPS." if ifd_id == ExifTags.IFD.GPSInfo else "") + name] = v
    clean = {}
    for k, v in out.items():
        if isinstance(v, bytes):
            v = v[:64].hex() if not v[:64].isascii() else v[:200].decode("ascii", "ignore")
        clean[k] = v if isinstance(v, (int, float, str)) else str(v)
    return clean


def _xmp(raw: bytes) -> str:
    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", raw, re.S)
    return m.group(0).decode("utf-8", "ignore") if m else ""


@register
class MetadataAnalyzer(Analyzer):
    order = 10
    name = "metadata"
    version = "1.0"
    title = "Metadata (EXIF / XMP / container)"

    def run(self, ctx: Ctx):
        if ctx.media.modality == "image":
            self._image(ctx)
        else:
            self._av(ctx)

    # ------------------------------------------------------------------ image
    def _image(self, ctx: Ctx):
        raw = ctx.path.read_bytes()
        im = Image.open(ctx.path)
        exif = _exif_dict(im)
        xmp = _xmp(raw)
        info = {k: str(v)[:2000] for k, v in im.info.items() if k not in ("exif", "icc_profile", "xmp")}
        ctx.shared["exif"] = exif
        ctx.shared["xmp"] = xmp
        ctx.shared["png_text"] = {k: v for k, v in im.info.items() if isinstance(v, str)}
        details = {"format": im.format, "mode": im.mode, "size": im.size, "exif": exif, "info": info,
                   "has_xmp": bool(xmp), "has_icc": "icc_profile" in im.info}

        make, model = exif.get("Make"), exif.get("Model")
        software = str(exif.get("Software", "") or "")
        creator_tool = (re.search(r'CreatorTool[=>"]+([^"<]+)', xmp) or [None, ""])[1] if xmp else ""
        if make or model:
            ctx.claim("metadata", f"File metadata names a capture device: {make or ''} {model or ''}".strip(),
                      Status.VERIFIED, strength=Strength.WEAK, polarity=Polarity.NEUTRAL,
                      why="The EXIF Make/Model fields are present in the file as received.",
                      limitations="EXIF is trivially editable and can be copied from another file. This verifies the "
                                  "field's presence, not that this device captured the content.",
                      confirm="Compare against originals from the claimed device (sensor-noise/PRNU match).",
                      data={"make": make, "model": model, "serial": exif.get("BodySerialNumber"),
                            "lens": exif.get("LensModel")})
        for label, val in (("Software", software), ("XMP CreatorTool", creator_tool)):
            if not val:
                continue
            if GENERATOR_SOFTWARE.search(val):
                ctx.claim("metadata", f"{label} field names an AI generation tool: “{val}”", Status.VERIFIED,
                          strength=Strength.MODERATE, polarity=Polarity.SUPPORTS_MANIPULATION,
                          why="Generation tools often write their name into the file's software metadata.",
                          limitations="Metadata can be added or removed by anyone; presence is a strong hint, "
                                      "not proof of generation.",
                          confirm="Corroborate with C2PA, watermark or detector evidence.",
                          data={"field": label, "value": val})
            elif EDITING_SOFTWARE.search(val):
                ctx.claim("metadata", f"{label} field shows processing by editing software: “{val}”", Status.VERIFIED,
                          strength=Strength.WEAK, polarity=Polarity.NEUTRAL,
                          why="The file was last written by an editing/beautify application.",
                          limitations="Editing software is used for ordinary edits too; this shows processing, not "
                                      "synthetic generation.",
                          data={"field": label, "value": val})
        dt = exif.get("DateTimeOriginal") or exif.get("DateTime")
        if dt:
            ctx.claim("metadata", f"Embedded capture/modify timestamp: {dt} (offset {exif.get('OffsetTimeOriginal', 'not recorded')})",
                      Status.VERIFIED, strength=Strength.WEAK,
                      why="Timestamp field read directly from EXIF.",
                      limitations="Device clocks can be wrong and EXIF dates are editable; timezone is often absent.",
                      data={"datetime": str(dt), "offset": exif.get("OffsetTimeOriginal")})
        if any(k.startswith("GPS.") for k in exif):
            ctx.claim("metadata", "GPS location fields are present in the file metadata", Status.VERIFIED,
                      strength=Strength.WEAK, data={k: v for k, v in exif.items() if k.startswith("GPS.")},
                      limitations="Location metadata is editable; treat as a lead requiring corroboration.")
        if not exif and not xmp:
            ctx.claim("metadata", "No EXIF or XMP metadata present", Status.UNKNOWN,
                      why="The file carries no camera or editing metadata.",
                      limitations="Absence does NOT indicate authenticity or manipulation: WhatsApp, Instagram, X, "
                                  "Facebook and most platforms strip metadata on upload, and generators often write none.",
                      confirm="Obtain the earliest available copy (ideally the uploader's original file).")

        # JPEG quantisation tables → quality + encoder family fingerprint
        q = getattr(im, "quantization", None)
        if q:
            luma = list(q.get(0, []))
            quality, resid = estimate_jpeg_quality(luma)
            qhash = hashlib.sha256(str(sorted((k, list(v)) for k, v in q.items())).encode()).hexdigest()[:16]
            std = resid < 1.0
            details["jpeg"] = {"quality_estimate": quality, "ijg_residual": round(resid, 2), "tables": len(q),
                               "qtable_hash": qhash, "progressive": bool(im.info.get("progressive"))}
            ctx.shared["qtable_hash"] = qhash
            ctx.claim("encoding",
                      f"JPEG encoded at approx. quality {quality} with "
                      f"{'standard libjpeg-style' if std else 'non-standard (custom)'} quantisation tables",
                      Status.VERIFIED, strength=Strength.WEAK,
                      why="Quantisation tables are read directly from the JPEG header. Custom tables identify encoder "
                          "families (camera firmware, Photoshop, platform re-encoders).",
                      limitations="Recompression replaces the tables, so this reflects the last encoder only.",
                      data=details["jpeg"])
        ctx.shared["metadata_details"] = details
        ctx.artifact("metadata", "Raw metadata dump", __import__("json").dumps(details, indent=2, default=str).encode(),
                     mime="application/json")

    # ------------------------------------------------------------------ video / audio
    def _av(self, ctx: Ctx):
        if not ffprobe_bin():
            from ..core.claims import NotRun
            return NotRun(component=self.name, reason="ffprobe not found on PATH",
                          how_to_enable="Install FFmpeg and add it to PATH")
        p = ffprobe(ctx.path)
        fmt = p.get("format", {})
        tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
        stream_tags = [{k.lower(): v for k, v in (s.get("tags") or {}).items()} for s in p.get("streams", [])]
        ctx.shared["ffprobe"] = p
        enc = tags.get("encoder", "")
        details = {"format": fmt.get("format_name"), "duration": fmt.get("duration"), "bit_rate": fmt.get("bit_rate"),
                   "tags": tags, "streams": [{"type": s.get("codec_type"), "codec": s.get("codec_name"),
                                              "profile": s.get("profile"), "w": s.get("width"), "h": s.get("height"),
                                              "fps": s.get("avg_frame_rate"), "sr": s.get("sample_rate"),
                                              "tags": t} for s, t in zip(p.get("streams", []), stream_tags)]}
        ctx.shared["encoder_signature"] = "|".join(filter(None, [fmt.get("format_name"), enc] +
                                                          [s.get("codec_name", "") for s in p.get("streams", [])]))
        if enc:
            processed = enc.lower().startswith("lavf")
            ctx.claim("encoding", f"Container encoder tag: “{enc}”", Status.VERIFIED, strength=Strength.WEAK,
                      why=("'Lavf' means the file was written by FFmpeg/libavformat — used by most editing apps, "
                           "converters, screen recorders and many generation pipelines." if processed else
                           "Encoder tag read directly from the container."),
                      limitations="Shows the last tool that wrote the container, not the content's origin.",
                      data={"encoder": enc})
        make = tags.get("com.apple.quicktime.make") or tags.get("make")
        model = tags.get("com.apple.quicktime.model") or tags.get("model")
        if make or model:
            ctx.claim("metadata", f"Container names a capture device: {make or ''} {model or ''}".strip(),
                      Status.VERIFIED, strength=Strength.WEAK, data={"make": make, "model": model},
                      limitations="Container tags are editable; device must be confirmed independently.")
        ct = tags.get("creation_time") or next((t.get("creation_time") for t in stream_tags if t.get("creation_time")), None)
        if ct:
            ctx.claim("metadata", f"Container creation_time: {ct}", Status.VERIFIED, strength=Strength.WEAK,
                      data={"creation_time": ct},
                      limitations="Set by the last muxer; often the time of re-encoding, not capture. "
                                  "1970/1904 epochs mean 'unset'.")
        handlers = sorted({t.get("handler_name") for t in stream_tags if t.get("handler_name")})
        if handlers:
            details["handlers"] = handlers
        if not (make or model or ct or enc):
            ctx.claim("metadata", "No device, creation-time or encoder tags in container", Status.UNKNOWN,
                      limitations="Absence does not indicate authenticity — platforms rewrite containers on upload.")
        ctx.shared["metadata_details"] = details
        ctx.artifact("metadata", "Raw ffprobe dump", __import__("json").dumps(details, indent=2, default=str).encode(),
                     mime="application/json")
