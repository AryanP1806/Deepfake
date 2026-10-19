"""Device / camera source report for one media file.

Everything here is reported with the strength it really has:
  * metadata that names a camera, software, owner or time is *declared in the file* — editable, copyable, and usually
    stripped by social platforms. It is a lead, never proof of where the file came from;
  * a phone MODEL is shared by millions of devices and never identifies one device;
  * the only technique that can tie an image to a physical camera is sensor-noise (PRNU) comparison against reference
    photos from that camera — see forensics/prnu.py for its limits.
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone

import numpy as np
from PIL import Image
from sqlmodel import Session, select

from ..analyzers.metadata import EDITING_SOFTWARE, GENERATOR_SOFTWARE, _exif_dict, _xmp
from ..core.config import DATA_DIR
from ..core.db import case_dir, load_claims, vault_for
from ..core.models import Location, Media

VERSIONISH = re.compile(r"^[\dA-Za-z]?[\d.]+( \(.*\))?$")  # "15.6.1", "ver.1.0", "1.2.3 (Build...)"
IDENTITY_FIELDS = ("Artist", "Copyright", "CameraOwnerName", "OwnerName", "BodySerialNumber", "LensSerialNumber",
                   "ImageUniqueID", "HostComputer", "XPAuthor", "UserComment")

LIMITS = [
    "Everything in the file's metadata is declared by the file and can be edited or copied from another photo.",
    "A phone or camera model is shared by millions of devices: it never identifies one device.",
    "WhatsApp, Instagram, X, Facebook and most other platforms remove or rewrite metadata when media is uploaded, so "
    "missing metadata is normal and proves nothing either way.",
    "Only a sensor-noise (PRNU) comparison against photos from a specific suspected camera can link an image to a physical "
    "device, and resizing or social-media processing usually destroys that signal.",
]


def _parse_exif_dt(v: str | None, offset: str | None = None) -> str | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.strptime(s[:19], fmt)
            return d.isoformat() + (offset or "")
        except ValueError:
            continue
    return s


def _xmp_values(xmp: str, tag: str) -> list[str]:
    out = re.findall(rf'{tag}="([^"]*)"', xmp) + re.findall(rf"<{tag}>([^<]*)</{tag}>", xmp)
    return [o.strip() for o in out if o.strip()]


def _embedded_thumbnail(raw: bytes) -> Image.Image | None:
    """The small preview that cameras write into EXIF. Editors rarely update it, so it can reveal the original."""
    m = re.search(rb"\xff\xe1(..)Exif\x00\x00", raw, re.S)
    if not m:
        return None
    seg_len = int.from_bytes(m.group(1), "big")
    seg = raw[m.start() + 4: m.start() + 2 + seg_len]
    i = seg.find(b"\xff\xd8\xff", 10)
    if i < 0:
        return None
    j = seg.rfind(b"\xff\xd9")
    try:
        return Image.open(io.BytesIO(seg[i: j + 2 if j > i else None])).convert("L")
    except Exception:  # noqa: BLE001
        return None


def _thumb_similarity(thumb: Image.Image, main: Image.Image) -> float:
    a = np.asarray(thumb.resize((32, 32)), dtype=np.float32).ravel()
    b = np.asarray(main.convert("L").resize((32, 32)), dtype=np.float32).ravel()
    a, b = a - a.mean(), b - b.mean()
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float((a * b).sum() / d) if d else 0.0


def _icc_description(im: Image.Image) -> str | None:
    prof = im.info.get("icc_profile")
    if not prof:
        return None
    try:
        from PIL import ImageCms
        return ImageCms.getOpenProfile(io.BytesIO(prof)).profile.profile_description
    except Exception:  # noqa: BLE001
        return "present (description unreadable)"


def _platform_hints(w: int, h: int, has_meta: bool, jpeg_q: int | None) -> list[dict]:
    """Image sizes characteristic of platform re-processing. Hints only: many other files share these sizes."""
    longest, shortest = max(w, h), min(w, h)
    hints = []
    rules = [(1600, "WhatsApp-style (long side 1600 px)"), (1080, "Instagram-style (1080 px wide)"),
             (2048, "Facebook-style (long side 2048 px)"), (1280, "Telegram/messenger-style (long side 1280 px)")]
    for size, label in rules:
        if longest == size or (size == 1080 and w == 1080):
            hints.append({"label": label, "why": f"The image is {w}×{h}px"
                          + ("" if has_meta else " and has no camera metadata") + (f", JPEG quality ≈ {jpeg_q}" if jpeg_q else "")})
    return hints


def device_report(s: Session, case_id: str, m: Media) -> dict:
    claims = load_claims(s, case_id, "decomposition", m.id)
    by_cat = lambda c: [x for x in claims if x.category == c]  # noqa: E731
    rep: dict = {"media_id": m.id, "label": m.label, "modality": m.modality, "limits": LIMITS}
    locs = [l for l in s.exec(select(Location).where(Location.case_id == case_id)).all()
            if m.id in (l.media_ids or []) or m.location_id == l.id]
    outside = [{"label": f"Page shows it was published ({l.platform or l.domain})", "value": l.published_at.isoformat(),
                "source": l.published_source or "page", "url": l.url} for l in locs if l.published_at]
    outside += [{"label": "First captured by this app", "value": l.first_captured_at.isoformat(), "source": "this app's clock",
                 "url": l.url} for l in locs if l.first_captured_at]

    if m.modality != "image":
        meta = [c for c in by_cat("metadata") + by_cat("encoding")]
        dev = next((c.data for c in meta if c.data.get("make") or c.data.get("model")), None)
        ct = next((c.data.get("creation_time") for c in meta if c.data.get("creation_time")), None)
        enc = next((c.data.get("encoder") for c in meta if c.data.get("encoder")), None)
        rep["device"] = _device_block(dev["make"] if dev else None, dev["model"] if dev else None, None, None, None)
        rep["timestamps"] = ([{"label": "Container creation_time", "value": ct, "timezone": None,
                               "source": "video/audio container (set by the last program that wrote the file)"}] if ct else [])
        rep["software"] = ([{"field": "Container encoder", "value": enc,
                             "kind": "processing" if str(enc).lower().startswith("lavf") else "other"}] if enc else [])
        rep["inventory"] = [{"group": "Container tags", "present": bool(dev or ct or enc),
                             "detail": "device, creation time and encoder tags" if (dev or ct or enc) else "none found"}]
        rep["consistency"], rep["platform_hints"], rep["identity_fields"], rep["edit_history"] = [], [], [], []
        rep["outside_the_file"] = outside
        rep["summary"] = _summary(rep)
        rep["prnu"] = load_prnu(case_id, m.id)
        return rep

    path = vault_for(case_id).path(m.sha256)
    raw = path.read_bytes()
    im = Image.open(path)
    exif = _exif_dict(im)
    xmp = _xmp(raw)
    has_meta = bool(exif or xmp)
    jpeg_q = (m.fingerprints or {}).get("jpeg_q")

    # --- device
    software_raw = str(exif.get("Software", "") or "")
    make, model = exif.get("Make"), exif.get("Model")
    firmware = software_raw if (software_raw and VERSIONISH.match(software_raw.strip()) and (make or model)) else None
    rep["device"] = _device_block(make, model, exif.get("LensModel"), exif.get("BodySerialNumber") or exif.get("SerialNumber"), firmware)

    # --- software / editing chain
    software = []
    creator_tool = ((re.search(r'CreatorTool[=>"]+([^"<]+)', xmp) or [None, ""])[1] if xmp else "").strip()
    for field, val in (("EXIF Software", software_raw), ("XMP CreatorTool", creator_tool)):
        if not val or val == firmware:
            continue
        kind = "generator" if GENERATOR_SOFTWARE.search(val) else "editing" if EDITING_SOFTWARE.search(val) else "other"
        software.append({"field": field, "value": val, "kind": kind})
    rep["software"] = software
    agents = _xmp_values(xmp, "stEvt:softwareAgent")
    actions = _xmp_values(xmp, "stEvt:action")
    rep["edit_history"] = [{"agent": a, "action": actions[i] if i < len(actions) else None} for i, a in enumerate(agents)]

    # --- identity-like fields
    rep["identity_fields"] = [{"field": k, "value": str(exif[k])[:120]} for k in IDENTITY_FIELDS if exif.get(k)] + \
        [{"field": "XMP creator", "value": v} for v in _xmp_values(xmp, "dc:creator")[:2]] + \
        [{"field": "XMP rights", "value": v} for v in _xmp_values(xmp, "xmpRights:Marked")[:1]]

    # --- timestamps (all declared in the file)
    off = exif.get("OffsetTimeOriginal") or exif.get("OffsetTime")
    ts = []
    for label, key in (("Taken (DateTimeOriginal)", "DateTimeOriginal"), ("Digitised", "DateTimeDigitized"),
                       ("Last modified (DateTime)", "DateTime")):
        if exif.get(key):
            sub = exif.get({"DateTimeOriginal": "SubsecTimeOriginal", "DateTime": "SubsecTime"}.get(key, ""), "")
            ts.append({"label": label, "value": _parse_exif_dt(exif[key], off) + (f".{sub}" if sub and key != "DateTimeDigitized" else ""),
                       "timezone": off or "not recorded", "source": "EXIF (declared by the file)"})
    for label, tag in (("XMP CreateDate", "xmp:CreateDate"), ("XMP ModifyDate", "xmp:ModifyDate"), ("XMP MetadataDate", "xmp:MetadataDate")):
        for v in _xmp_values(xmp, tag)[:1]:
            ts.append({"label": label, "value": v, "timezone": "see value", "source": "XMP (declared by the file)"})
    rep["timestamps"] = ts

    # --- consistency checks
    flags = []

    def flag(fid, sev, text):
        flags.append({"id": fid, "severity": sev, "text": text})

    t_orig, t_mod = exif.get("DateTimeOriginal"), exif.get("DateTime")
    if t_orig and t_mod and t_orig != t_mod:
        try:
            gap = abs((datetime.strptime(t_mod[:19], "%Y:%m:%d %H:%M:%S") - datetime.strptime(t_orig[:19], "%Y:%m:%d %H:%M:%S")).total_seconds())
            if gap > 60:
                flag("modified_after_capture", "warn", f"The file was re-saved {_dur(gap)} after the stated capture time — typical of editing, "
                     "though some phones and apps also update this field.")
        except ValueError:
            flag("bad_timestamp", "warn", "A timestamp field is malformed.")
    pub = [datetime.fromisoformat(o["value"]) for o in outside if o["label"].startswith("Page shows")]
    if t_orig and pub:
        try:
            cap = datetime.strptime(t_orig[:19], "%Y:%m:%d %H:%M:%S")
            earliest = min(p.replace(tzinfo=None) if p.tzinfo is None else p.astimezone(timezone.utc).replace(tzinfo=None) for p in pub)
            if cap > earliest and (cap - earliest).total_seconds() > 24 * 3600:  # allow for time-zone ambiguity
                flag("capture_after_publication", "high", "The file claims it was taken AFTER the page says it was published: "
                     "the embedded time is wrong or has been changed.")
        except ValueError:
            pass
    if any(x["kind"] == "editing" for x in software):
        flag("editing_software", "info", "Software that edits images was the last to write this file, so it was processed after capture "
             "(this is common and not by itself manipulation of the content).")
    if any(x["kind"] == "generator" for x in software):
        flag("generator_software", "warn", "A field names an AI generation tool (see Generator attribution).")
    if make and not exif.get("LensModel") and not exif.get("ExposureTime") and not exif.get("FNumber"):
        flag("thin_camera_metadata", "info", "A camera make/model is named but the usual exposure details are missing, so the "
             "metadata may have been partly stripped or written by another program.")
    pxw = exif.get("PixelXDimension") or exif.get("ExifImageWidth")  # the tag is named differently across Pillow versions
    pxh = exif.get("PixelYDimension") or exif.get("ExifImageHeight")
    if pxw and pxh and (int(pxw), int(pxh)) != im.size and (int(pxw), int(pxh)) != im.size[::-1]:
        flag("size_mismatch", "warn", f"The metadata records {pxw}×{pxh} but the image is {im.size[0]}×{im.size[1]}: it was "
             "resized or cropped after the metadata was written.")
    thumb = _embedded_thumbnail(raw)
    if thumb is not None:
        a1, a2 = thumb.size[0] / thumb.size[1], im.size[0] / im.size[1]
        sim = _thumb_similarity(thumb, im)
        if abs(a1 - a2) / a2 > 0.03 and abs(a1 - 1 / a2) / (1 / a2) > 0.03:
            flag("thumbnail_aspect", "warn", "The preview embedded by the camera has a different shape from the image: the image was "
                 "cropped after capture (editors rarely update the embedded preview).")
        elif sim < 0.75:
            flag("thumbnail_content", "high", f"The embedded camera preview does not match the image content (similarity {sim:.2f}): "
                 "the picture was changed after the preview was created.")
        else:
            flag("thumbnail_ok", "ok", "The embedded camera preview matches the image (no sign it was changed after capture, "
                 "within what this simple check can see).")
        rep["thumbnail_similarity"] = round(sim, 3)

    # --- what survived
    icc = _icc_description(im)
    inv = [
        {"group": "EXIF", "present": bool(exif), "detail": f"{len(exif)} fields" if exif else "none"},
        {"group": "Camera details (make/model/lens/exposure)", "present": bool(make or model or exif.get("ExposureTime")),
         "detail": ", ".join(filter(None, [make, model])) or ("exposure data only" if exif.get("ExposureTime") else "none")},
        {"group": "GPS location", "present": any(k.startswith("GPS.") for k in exif), "detail": "present" if any(k.startswith("GPS.") for k in exif) else "none"},
        {"group": "XMP (editing history, creator tool)", "present": bool(xmp), "detail": f"{len(xmp)} bytes" if xmp else "none"},
        {"group": "Colour profile (ICC)", "present": bool(icc), "detail": icc or "none"},
        {"group": "Embedded camera preview", "present": thumb is not None, "detail": f"{thumb.size[0]}×{thumb.size[1]}" if thumb else "none"},
        {"group": "Content credentials (C2PA)", "present": any(c.status.value == "Verified" for c in by_cat("c2pa")) ,
         "detail": "see Generator attribution" if by_cat("c2pa") else "none"},
        {"group": "Text comments / PNG text", "present": bool(im.info.get("comment") or any(isinstance(v, str) for k, v in im.info.items() if k not in ("icc_profile", "exif", "xmp", "dpi"))),
         "detail": "present" if im.info.get("comment") else "none"},
    ]
    rep["inventory"] = inv
    rep["consistency"] = flags
    rep["platform_hints"] = _platform_hints(im.size[0], im.size[1], has_meta, jpeg_q)
    rep["image"] = {"size": list(im.size), "format": im.format, "jpeg_quality_estimate": jpeg_q}
    rep["outside_the_file"] = outside
    rep["summary"] = _summary(rep, has_meta)
    rep["prnu"] = load_prnu(case_id, m.id)
    return rep


def _dur(sec: float) -> str:
    return f"{sec / 86400:.0f} day(s)" if sec >= 172800 else f"{sec / 3600:.1f} hour(s)" if sec >= 3600 else f"{sec / 60:.0f} minute(s)"


def _device_block(make, model, lens, serial, firmware) -> dict:
    claimed = {k: v for k, v in {"make": make, "model": model, "lens": lens, "serial": serial, "firmware": firmware}.items() if v}
    if not (make or model):
        return {"claimed": None, "statement": "The file does not name a camera or phone.", "tier": "none",
                "note": "This is normal for social-media copies and for AI-generated images. It does not mean the media is real or fake."}
    name = " ".join(filter(None, [str(make or ""), str(model or "")])).strip()
    return {"claimed": claimed, "tier": "declared in the file (editable)",
            "statement": f"The file says it was taken with: {name}.",
            "note": ("A phone or camera MODEL is not a unique device — millions share it. " +
                     ("A serial number is present: it can be checked against the owner's device, but it is also editable. " if serial else "") +
                     "Treat this as a lead to compare against the suspected device, not as a finding.")}


def _summary(rep: dict, has_meta: bool = True) -> str:
    d = rep["device"]
    parts = []
    if d.get("claimed"):
        parts.append(d["statement"] + " (declared in the file — not proof)")
    else:
        parts.append("No camera or phone is named in the file." + ("" if has_meta else " The file has no metadata at all, which is typical after upload to a social platform."))
    if any(x["kind"] == "generator" for x in rep.get("software", [])):
        parts.append("A field names an AI generation tool.")
    elif any(x["kind"] == "editing" for x in rep.get("software", [])):
        parts.append("It was last written by editing software.")
    high = [f for f in rep.get("consistency", []) if f["severity"] in ("high", "warn")]
    if high:
        parts.append(f"{len(high)} consistency warning(s) — see below.")
    return " ".join(parts)


# ----------------------------------------------------------------------------------------------- PRNU results store
def _prnu_dir(case_id):
    d = case_dir(case_id) / "prnu"
    d.mkdir(exist_ok=True)
    return d


def load_prnu(case_id: str, media_id: str) -> list[dict]:
    d = case_dir(case_id) / "prnu"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob(f"{media_id}__*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


def save_prnu(case_id: str, media_id: str, key: str, result: dict) -> None:
    (_prnu_dir(case_id) / f"{media_id}__{key}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


def all_prnu(case_id: str) -> list[dict]:
    d = case_dir(case_id) / "prnu"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.exists() else []


__all__ = ["device_report", "save_prnu", "load_prnu", "all_prnu", "DATA_DIR"]
