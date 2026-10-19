"""Module 1 — PRNU sensor-noise fingerprinting for candidate-device comparison.

Tests ONE hypothesis at a time: "could this image have come from candidate device X?" against a fingerprint built from
reference photos the investigator attests came from X. It never names a device on its own, and a phone MODEL is not a device.
Reuses ``dfir.forensics.prnu`` (wavelet-denoised residual, maximum-likelihood fingerprint, peak-to-correlation energy).
"""
from __future__ import annotations

import numpy as np

from .. import prnu
from ..base import FCtx, Finding, ForensicModule, ModuleResult, Requirement, validation_for
from ..registry import register


def _confidence_from_pce(pce: float) -> float:
    """Evidence strength, before the validation cap: PCE 60 is the conventional threshold; strength grows slowly beyond it."""
    return float(min(0.9, 0.5 + 0.2 * np.log10(max(pce, 60) / 60)))


def _fpr_sentence() -> str:
    m = validation_for("prnu")
    f = (m.metrics or {}).get("false_match_at_threshold")
    if not f:
        return "The false-match rate of this implementation has not been measured."
    return (f"On {f['trials']} simulated impostor comparisons the implementation produced {f['false_matches']} false matches at PCE ≥ {f['threshold']} "
            f"(95 % upper bound {f['upper_95']:.2%}).")


@register
class SensorNoiseModule(ForensicModule):
    id = "prnu"
    title = "PRNU sensor-noise fingerprinting"
    question = "Could this image have been captured by one of the candidate cameras whose reference photos are on file?"
    version = "1.0"
    modalities = ("image",)
    requirements = [Requirement(kind="python", name="pywt", how_to_enable="uv sync   (PyWavelets is a core dependency)")]
    library_versions = ("numpy", "scipy", "pywt", "PIL")
    order = 10
    limitations = [
        "It tests a specific suspected camera; it never identifies a camera on its own.",
        "A camera model is not a device: only reference photos from the suspected physical device can support a match.",
        "The pattern is weakened or destroyed by resizing, cropping, heavy JPEG compression, screenshots and platform re-encoding; the image must have the "
        "same pixel size and orientation as the references.",
        "AI-generated images carry no sensor pattern, and a non-match proves nothing about authenticity.",
        "Validated on simulated sensors only; no real-camera test set has been run.",
    ]

    def analyse(self, fx: FCtx) -> ModuleResult:
        gray = prnu.load_gray(fx.path)
        res = ModuleResult(module=self.id, title=self.title, version=self.version)
        F = res.findings
        h, w = gray.shape
        res.versions["method"] = "wavelet(db8, 4 levels) residual, maximum-likelihood fingerprint, PCE"
        if min(h, w) < 256:
            F.append(Finding(id="prnu.size", title="Image size", kind="unknown",
                             statement=f"The image is {w}×{h}: under 256 px, too small for a sensor-noise comparison."))
            res.status, res.summary = "partial", F[0].statement
            return res
        cands = fx.candidate_devices
        if not cands:
            F.append(Finding(id="prnu.no_candidate", title="Candidate device", kind="unknown",
                             statement="No candidate-device fingerprint is on file for this case, so no sensor comparison was possible.",
                             why="A fingerprint is built from at least 5 photos (20+ is better) known to come from the suspected camera, all at the same pixel size as this image.",
                             caution="Add reference photos and save them as a candidate device."))
        same = [c for c in cands if (c["height"], c["width"]) == (h, w)]
        diff = [c for c in cands if (c["height"], c["width"]) != (h, w)]
        for c in diff:
            F.append(Finding(id=f"prnu.size_mismatch.{c['id']}", title=f"{c['name']}: pixel size", kind="unknown",
                             statement=f"Candidate '{c['name']}' was built from {c['width']}×{c['height']} photos; this image is {w}×{h}, so the two cannot be compared "
                                       "(the image was resized or cropped, or comes from a different capture mode).",
                             caution="Do not treat this as a non-match."))
        fpr = _fpr_sentence()
        any_consistent = False
        for c in same:
            r = prnu.compare_to_fingerprint(c["fingerprint"], gray, c["n_references"])
            data = {k: r[k] for k in ("status", "pce", "ncc", "shift", "thresholds", "method") if k in r}
            data.update(device_id=c["id"], n_references=c["n_references"], reference_coherent=c.get("coherent"))
            if r["status"] == "consistent":
                any_consistent = True
                conf = _confidence_from_pce(r["pce"]) * (1.0 if c.get("coherent", True) else 0.6)
                F.append(Finding(id=f"prnu.match.{c['id']}", title=f"{c['name']}: sensor-noise comparison", kind="supported", confidence=conf, value=r["pce"], unit="PCE",
                                 statement=f"The image's sensor noise is consistent with candidate device '{c['name']}' (PCE {r['pce']}, threshold {prnu.PCE_MATCH:.0f}). "
                                           "This supports, but does not prove, that they share a sensor.",
                                 why=f"{fpr} Another camera, a derived copy of a reference photo, or a stolen/forged residual can also produce a peak.",
                                 caution=("The references did not share a consistent pattern with each other, so this fingerprint is unreliable." if not c.get("coherent", True) else
                                          "Check the references really came from this camera and that none shows the same picture as this image."),
                                 data=data))
            elif r["status"] == "inconclusive":
                F.append(Finding(id=f"prnu.weak.{c['id']}", title=f"{c['name']}: sensor-noise comparison", kind="observation", value=r["pce"], unit="PCE",
                                 statement=f"A weak correlation with '{c['name']}' (PCE {r['pce']}) — indistinguishable from chance. No conclusion.", data=data))
            else:
                F.append(Finding(id=f"prnu.none.{c['id']}", title=f"{c['name']}: sensor-noise comparison", kind="observation", value=r["pce"], unit="PCE",
                                 statement=f"No sensor-noise correlation with '{c['name']}' (PCE {r['pce']}).",
                                 why="Either a different camera, or the pattern did not survive the image's processing history.",
                                 caution="NOT evidence that the image came from a different camera.", data=data))
        F.append(Finding(id="attribution.device", title="Which physical device captured this", kind="unknown",
                         statement="Exact device attribution is not made. Only a comparison against a named candidate can be reported, and a match supports — never proves — a shared sensor.",
                         caution="EXIF make/model are declared by the file and identify a model, not a device."))
        res.summary = (F[0].statement if not same else
                       "Consistent with a candidate device (supports, does not prove, a shared sensor)." if any_consistent else
                       "No candidate device matched. This does not show the image came from another camera.")
        res.status = "ok" if same else "partial"
        return res
