"""Module 6 — provenance analysis: EXIF, C2PA, AI watermarks and generator metadata.

This module does not re-measure anything: it consolidates what the existing metadata / C2PA / watermark analyzers recorded
(as claims) and adds the cross-checks the device-source report already knows (embedded preview vs image, size mismatch,
timestamp order). Its job is to say, in one place, WHAT IS DECLARED, WHAT IS CRYPTOGRAPHICALLY VERIFIED, and WHAT WAS NOT CHECKED.

Attribution policy:
  * A capture device is never attributed from metadata: EXIF names a model, anyone can edit it, and platforms strip it.
  * A generator FAMILY is named only through `reasoning.generator` tiers; an exact model/version is repeated only when a signed
    C2PA manifest states it, and is then reported as "stated by the signer", never as established fact about the pixels.
"""
from __future__ import annotations

import re

from PIL import Image

from ...core.claims import Claim, Status, Strength
from ..base import Evidence, FCtx, Finding, ForensicModule, ModuleResult, Requirement
from ..registry import register


def _by(claims: list[Claim], *cats: str) -> list[Claim]:
    return [c for c in claims if c.category in cats]


@register
class ProvenanceModule(ForensicModule):
    id = "provenance"
    title = "Provenance: EXIF, C2PA, watermarks, generator metadata"
    question = "What does the file itself declare about where it came from, and what of that is cryptographically verified?"
    version = "1.0"
    modalities = ("image", "video", "audio")
    requirements = [Requirement(kind="python", name="c2pa", optional=True, how_to_enable="uv sync --extra provenance   (adds C2PA / Content Credentials checking)"),
                    Requirement(kind="python", name="imwatermark", optional=True, how_to_enable="uv sync --extra provenance   (adds open invisible-watermark decoding)")]
    library_versions = ("PIL", "c2pa")
    order = 5
    limitations = [
        "Everything in EXIF/XMP is declared by the file and can be added, edited, copied or removed by anyone.",
        "Missing metadata is normal: WhatsApp, Instagram, X, Facebook and most platforms strip it. Absence is Unknown, never evidence of authenticity or of manipulation.",
        "A C2PA signature proves who signed a record and that the file is unchanged since; the signer's statements are only as honest as the signer.",
        "Proprietary watermarks (SynthID and others) have no public detector and were not checked.",
        "A phone or camera model identifies a model shared by millions of devices, never one device.",
    ]

    def analyse(self, fx: FCtx) -> ModuleResult:
        from ...reasoning.generator import generator_attribution
        res = ModuleResult(module=self.id, title=self.title, version=self.version)
        F, ev = res.findings, res.evidence
        claims = fx.claims()

        def cref(c: Claim) -> str:
            eid = f"clm_{c.id}"
            if not any(e.id == eid for e in ev):
                ev.append(Evidence(id=eid, kind="claim", label=c.statement[:120], claim_id=c.id))
            return eid

        # ---------------------------------------------------------------- EXIF / container metadata
        meta = _by(claims, "metadata", "encoding")
        dev = next((c for c in meta if c.data.get("make") or c.data.get("model")), None)
        if dev:
            mk, md = dev.data.get("make") or "", dev.data.get("model") or ""
            name = f"{mk} {md}".strip()
            F.append(Finding(id="exif.device", title="Declared capture device", kind="observation", value=name,
                             statement=f"The file declares it was captured by: {name}.",
                             why="Read directly from the file's metadata.",
                             caution="Declared by the file, not validated. A model is shared by millions of devices; metadata can be copied from another photo. "
                                     "Only a PRNU comparison against the suspected device can test this.", evidence=[cref(dev)], data=dev.data, emit_claim=False))
        else:
            F.append(Finding(id="exif.device", title="Declared capture device", kind="unknown",
                             statement="No capture-device metadata is present.",
                             caution="Normal for media shared through social platforms. Not evidence either way."))
        for c in meta:
            d = c.data or {}
            if d.get("field") and d.get("value"):
                F.append(Finding(id=f"exif.software.{len(F)}", title=f"{d['field']}", kind="observation", value=d["value"],
                                 statement=c.statement, caution=c.limitations, evidence=[cref(c)], emit_claim=False))
            elif d.get("datetime"):
                F.append(Finding(id="exif.time", title="Embedded timestamp", kind="observation", value=d["datetime"], statement=c.statement,
                                 caution=c.limitations, evidence=[cref(c)], emit_claim=False))
            elif d.get("creation_time"):
                F.append(Finding(id="container.time", title="Container creation time", kind="observation", value=d["creation_time"], statement=c.statement,
                                 caution=c.limitations, evidence=[cref(c)], emit_claim=False))
            elif d.get("encoder"):
                F.append(Finding(id="container.encoder", title="Container encoder tag", kind="observation", value=d["encoder"], statement=c.statement,
                                 caution=c.limitations, evidence=[cref(c)], emit_claim=False))
        if fx.modality == "image":
            self._consistency(fx, F)

        # ---------------------------------------------------------------- C2PA
        c2 = _by(claims, "c2pa")
        c2_req = next((r for r in self.check_requirements() if r.name == "c2pa"), None)
        if c2:
            for c in c2:
                valid = c.status == Status.VERIFIED
                F.append(Finding(id=f"c2pa.{len(F)}", title="Content Credentials (C2PA)", kind="observation" if valid else "unknown",
                                 statement=c.statement, why=c.why, caution=c.limitations, evidence=[cref(c)], data=c.data, emit_claim=False))
        elif c2_req is not None and not c2_req.ok:
            F.append(Finding(id="c2pa.unavailable", title="Content Credentials (C2PA)", kind="unknown",
                             statement="C2PA / Content Credentials were not checked (the c2pa library is not installed).",
                             caution="Install with: uv sync --extra provenance"))
        else:
            F.append(Finding(id="c2pa.none", title="Content Credentials (C2PA)", kind="unknown",
                             statement="No C2PA manifest was found in the file.",
                             caution="Most cameras and generators do not sign, and platforms strip manifests. Absence is not evidence of anything."))

        # ---------------------------------------------------------------- watermarks and generator metadata
        wm = _by(claims, "watermark")
        decoded = [c for c in wm if c.data.get("generator_family")]
        for c in decoded:
            strong = c.status == Status.VERIFIED and c.strength in (Strength.STRONG, Strength.MODERATE)
            F.append(Finding(id=f"watermark.{len(F)}", title="Open invisible watermark", kind="supported" if strong else "hypothesis",
                             confidence=0.7 if strong else 0.3, statement=c.statement, why=c.why,
                             caution=c.limitations or "Open reference watermarks can be embedded by anyone and are destroyed by resizing and recompression.",
                             evidence=[cref(c)], data=c.data, emit_claim=False))
        if not decoded:
            checked = next((c for c in wm if "No open" in c.statement or "skipped" in c.statement or "not run" in c.statement), None)
            F.append(Finding(id="watermark.open", title="Open invisible watermark", kind="unknown",
                             statement=(checked.statement if checked else "No open invisible watermark was decoded."),
                             caution="Only the public Stable-Diffusion reference watermarks can be decoded; most generators use none or a private one.",
                             evidence=[cref(checked)] if checked else []))
        F.append(Finding(id="watermark.closed", title="Proprietary watermarks (SynthID and others)", kind="unknown",
                         statement="Proprietary watermarks were not checked: no public detector exists.",
                         caution="Use the vendor's own verification tool where one is offered. A negative result there would also not prove authenticity."))
        gm = [c for c in _by(claims, "provenance") if c.data.get("generator_family") or c.data.get("digitalSourceType") or c.data.get("parameters")]
        for c in gm:
            F.append(Finding(id=f"generator_meta.{len(F)}", title="Generator metadata", kind="observation", statement=c.statement, why=c.why,
                             caution=c.limitations or "Metadata is declared, not proven, and easy to forge or strip.", evidence=[cref(c)], data=c.data, emit_claim=False))

        # ---------------------------------------------------------------- attribution (policy-limited)
        att = generator_attribution(claims)
        res.versions["attribution_tiers"] = "established / supported / suggested (reasoning.generator)"
        fam = att.get("possible_family")
        top = next((c for c in att.get("candidates", []) if c["family"] == fam), None) if fam else None
        if fam and top:
            tier = top["tier"]
            kinds = ", ".join(top["kinds"])
            if tier == "established":
                F.append(Finding(id="attribution.generator", title="Generator family", kind="supported", confidence=0.75,
                                 statement=f"Generator family: {fam} (basis: {kinds}). Exact model and version are not asserted.",
                                 why="A signed record, or three independent kinds of evidence, point to the same family.",
                                 caution="Concerns HOW the media was made, not who made or posted it. A signer's statement is only as honest as the signer.",
                                 data={"family": fam, "tier": tier, "kinds": top["kinds"]}))
            else:
                F.append(Finding(id="attribution.generator", title="Generator family", kind="hypothesis", confidence=0.4 if tier == "supported" else 0.2,
                                 statement=f"Possible generator family: {fam} — {tier} only (basis: {kinds}). Not enough validated evidence to attribute.",
                                 caution="Forgeable declared metadata or a single statistical signal. Do not name a generator on this basis.",
                                 data={"family": fam, "tier": tier, "kinds": top["kinds"]}))
        else:
            F.append(Finding(id="attribution.generator", title="Generator family", kind="unknown",
                             statement="No generator family can be attributed from provenance evidence in the file.",
                             caution="No signal is not a finding that the media is real."))
        F.append(Finding(id="attribution.device", title="Capture device", kind="unknown",
                         statement="Exact device attribution is not made from metadata (see the sensor-noise module for the only test that can link an image to a physical camera).",
                         caution="A phone model is not a device."))
        verified = [f for f in F if f.kind in ("observation", "supported") and f.id.startswith(("c2pa", "watermark", "generator_meta"))]
        res.summary = ("Provenance data found: " + "; ".join(f.title for f in verified[:4]) + "." if verified else
                       "No provenance data (C2PA, watermark, generator metadata) was found in the file. This is normal for shared media and proves nothing either way.")
        res.status = "ok"
        return res

    @staticmethod
    def _consistency(fx: FCtx, F: list[Finding]) -> None:
        """Cross-checks inside the file (reusing the device-source report's helpers)."""
        from ...analyzers.metadata import _exif_dict
        from ...reasoning.source import _embedded_thumbnail, _thumb_similarity
        try:
            raw = fx.path.read_bytes()
            im = Image.open(fx.path)
            exif = _exif_dict(im)
        except Exception:  # noqa: BLE001
            return
        pxw = exif.get("PixelXDimension") or exif.get("ExifImageWidth")
        pxh = exif.get("PixelYDimension") or exif.get("ExifImageHeight")
        if pxw and pxh and (int(pxw), int(pxh)) != im.size and (int(pxw), int(pxh)) != im.size[::-1]:
            F.append(Finding(id="exif.size_mismatch", title="Metadata size vs image size", kind="observation", value=f"{pxw}×{pxh} vs {im.size[0]}×{im.size[1]}",
                             statement=f"The metadata records {pxw}×{pxh} but the image is {im.size[0]}×{im.size[1]}: it was resized or cropped after the metadata was written.",
                             caution="Common when an editor or app re-saves a photo and keeps its EXIF."))
        thumb = _embedded_thumbnail(raw)
        if thumb is not None:
            sim = _thumb_similarity(thumb, im)
            a1, a2 = thumb.size[0] / thumb.size[1], im.size[0] / im.size[1]
            if abs(a1 - a2) / a2 > 0.03 and abs(a1 - 1 / a2) / (1 / a2) > 0.03:
                F.append(Finding(id="exif.thumb_aspect", title="Embedded preview", kind="observation", value=round(sim, 2),
                                 statement="The preview embedded by the camera has a different shape from the image: it was cropped after capture.",
                                 caution="Editors rarely update the embedded preview, so this is a reasonably reliable sign of a later crop — not of a content change."))
            elif sim < 0.75:
                F.append(Finding(id="exif.thumb_content", title="Embedded preview", kind="observation", value=round(sim, 2),
                                 statement=f"The embedded preview does not match the image content (similarity {sim:.2f}): the picture changed after the preview was written.",
                                 caution="Check by eye before relying on it; heavy filters also lower the similarity."))
            else:
                F.append(Finding(id="exif.thumb_ok", title="Embedded preview", kind="observation", value=round(sim, 2),
                                 statement="The embedded camera preview matches the image.",
                                 caution="A simple low-resolution comparison; it cannot see small local edits."))
        t0, t1 = exif.get("DateTimeOriginal"), exif.get("DateTime")
        if t0 and t1 and t0 != t1:
            F.append(Finding(id="exif.resaved", title="Timestamps", kind="observation", value=f"{t0} → {t1}",
                             statement=f"The file was last modified ({t1}) after its stated capture time ({t0}).",
                             caution="Phones and apps also rewrite this field on ordinary saves."))
