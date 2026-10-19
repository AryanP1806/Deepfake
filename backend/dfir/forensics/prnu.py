"""Sensor-noise (PRNU) comparison — an ADVANCED RESEARCH feature with hard limits.

Every camera sensor leaves a faint, fixed multiplicative noise pattern (PRNU) in its photos. If several photos
known to come from one camera are available, their average noise pattern (the "fingerprint") can be tested against
a questioned image: a strong, sharply peaked correlation means the image is consistent with that same sensor.

What this can and cannot say (these limits are returned with every result):
  * It tests a SPECIFIC suspected camera against reference photos. It never says which camera made an image.
  * A phone MODEL is not a device: two phones of one model have different fingerprints, and one fingerprint is
    one device — but only when the reference photos really came from that device.
  * The pattern is destroyed or weakened by resizing, cropping, heavy JPEG compression and social-media processing
    (WhatsApp, Instagram, ...). Images must have the same pixel size and orientation as the references.
  * AI-generated images carry no sensor pattern. A "no match" proves nothing about authenticity.
  * This is a simplified implementation of the standard method (wavelet denoising → maximum-likelihood fingerprint →
    peak-to-correlation-energy). It has been validated on synthetic sensors, not on a camera test set.

PCE (peak-to-correlation energy) > 60 is the conventional decision threshold (≈1 in 100,000 false matches for
unrelated sensors in the literature); values below ~30 are indistinguishable from chance.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from scipy.ndimage import uniform_filter

MAX_SIDE = 2048  # centre crop used for the FFT (same crop for fingerprint and test image)
PCE_MATCH = 60.0
PCE_NOISE = 30.0
MIN_REFERENCES = 5
SIGMA0 = 5.0  # assumed noise level (0-255 scale) of the wavelet denoiser


def load_gray(path: Path | str) -> np.ndarray:
    """Luma plane as float32 in 0-255. EXIF orientation is applied so references and test image line up."""
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    a = np.asarray(im, dtype=np.float32)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def _crop(a: np.ndarray) -> np.ndarray:
    h, w = a.shape
    ch, cw = min(h, MAX_SIDE), min(w, MAX_SIDE)
    y, x = (h - ch) // 2, (w - cw) // 2
    return a[y:y + ch, x:x + cw]


def _zero_mean(w: np.ndarray) -> np.ndarray:
    """Remove row and column means separately inside each of the four 2x2 (colour-filter-array) phase subsets.

    Cameras of the same design share row/column and CFA-phase structure, and so do JPEG encoders. Left in the residual these
    'non-unique artefacts' make DIFFERENT cameras correlate: on simulated sensors that share a Bayer pattern the impostor
    PCE was ~250 (far above the 60 threshold) until this step was made phase-aware (Chen et al., 2008)."""
    out = w.astype(np.float32, copy=True)
    for py in (0, 1):
        for px in (0, 1):
            sub = out[py::2, px::2]
            sub -= sub.mean(axis=0, keepdims=True)
            sub -= sub.mean(axis=1, keepdims=True)
    return out


def noise_residual(gray: np.ndarray) -> np.ndarray:
    """Noise residual = image − wavelet-denoised image (db8, 4 levels, Mihcak local-variance Wiener shrinkage)."""
    import pywt
    h, w = gray.shape
    coeffs = pywt.wavedec2(gray, "db8", level=4)
    out = [coeffs[0]]
    for detail in coeffs[1:]:
        shrunk = []
        for d in detail:
            v = d * d
            var = np.minimum.reduce([uniform_filter(v, size=s, mode="reflect") for s in (3, 5, 7, 9)]) - SIGMA0 ** 2
            var = np.maximum(var, 0.0)
            shrunk.append(d * var / (var + SIGMA0 ** 2))
        out.append(tuple(shrunk))
    den = pywt.waverec2(out, "db8")[:h, :w]
    return _zero_mean((gray - den).astype(np.float32))


def estimate_fingerprint(grays: list[np.ndarray]) -> np.ndarray:
    """Maximum-likelihood estimate K = Σ W_i·I_i / Σ I_i² over reference images of identical size."""
    num = np.zeros_like(grays[0], dtype=np.float64)
    den = np.zeros_like(grays[0], dtype=np.float64)
    for g in grays:
        num += noise_residual(g).astype(np.float64) * g
        den += g.astype(np.float64) ** 2
    return _zero_mean((num / np.maximum(den, 1.0)).astype(np.float32))


def _pce(a: np.ndarray, b: np.ndarray) -> tuple[float, float, tuple[int, int]]:
    """Normalised correlation at zero shift, PCE of the best (circular) shift, and where that peak is."""
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    ncc0 = float((a * b).sum() / denom)
    c = np.fft.ifft2(np.fft.fft2(a) * np.conj(np.fft.fft2(b))).real / denom
    h, w = c.shape
    py, px = np.unravel_index(int(np.argmax(c)), c.shape)
    peak = float(c[py, px])
    mask = np.ones_like(c, bool)
    ys, xs = (np.arange(py - 5, py + 6) % h)[:, None], (np.arange(px - 5, px + 6) % w)[None, :]
    mask[ys, xs] = False
    energy = float(np.mean(c[mask] ** 2)) or 1e-18
    dy, dx = (py if py <= h // 2 else py - h), (px if px <= w // 2 else px - w)
    return peak * abs(peak) / energy, ncc0, (int(dy), int(dx))


LIMITS = [
    "This tests a specific suspected camera against reference photos; it never identifies a camera on its own.",
    "A camera MODEL is not a device. Only reference photos from the suspected physical device can support a match.",
    "Resizing, cropping, heavy JPEG compression and social-media processing weaken or destroy the pattern.",
    "AI-generated images have no sensor pattern. A non-match says nothing about whether media is real.",
]


def verdict_from(pce: float, n_refs: int, shift: tuple[int, int], pair: bool = False) -> dict:
    other = "the other file" if pair else "the reference camera"
    if pce >= PCE_MATCH:
        status, text = "consistent", (f"The image's sensor noise is consistent with {other} (strong peak). "
                                      "This supports, but does not prove, that it came from the same sensor.")
    elif pce >= PCE_NOISE:
        status, text = "inconclusive", "A weak correlation — not distinguishable from chance. No conclusion."
    else:
        status, text = "not_consistent", (f"No sensor-noise correlation with {other}. They may come from different cameras, may have been resized or "
                                          "re-compressed (which destroys the pattern), or may not be camera images at all. This is NOT evidence that they differ.")
    return {"status": status, "pce": round(pce, 1), "n_references": n_refs, "shift": list(shift), "summary": text,
            "thresholds": {"match": PCE_MATCH, "noise": PCE_NOISE}}


def compare_to_fingerprint(k: np.ndarray, test: np.ndarray, n_refs: int) -> dict:
    """Test one questioned image (grayscale, same pixel size as the fingerprint) against a fingerprint."""
    t = _crop(test)
    kc = k if k.shape == t.shape else _crop(k)
    pce, ncc, shift = _pce(noise_residual(t), kc * t)
    return {**verdict_from(pce, n_refs, shift), "ncc": round(ncc, 4), "method": "wavelet(db8,4) MLE fingerprint + PCE"}


def compare_to_references(reference_paths: list[Path | str], test_path: Path | str) -> dict:
    """Test one questioned image against a camera fingerprint built from reference photos."""
    test = load_gray(test_path)
    grays, skipped = [], []
    for p in reference_paths:
        g = load_gray(p)
        (grays if g.shape == test.shape else skipped).append(g if g.shape == test.shape else Path(p).name)
    base = {"limits": LIMITS, "skipped_references": skipped}
    if test.shape[0] < 256 or test.shape[1] < 256:
        return {**base, "status": "not_comparable", "summary": "The image is too small for a sensor-noise comparison (under 256 px)."}
    if len(grays) < MIN_REFERENCES:
        return {**base, "status": "not_comparable", "n_references": len(grays),
                "summary": (f"Only {len(grays)} reference photo(s) have exactly the same pixel size as the image "
                            f"({test.shape[1]}×{test.shape[0]}); at least {MIN_REFERENCES} are needed "
                            f"(more is better; 20+ gives a reliable fingerprint). Resized or cropped photos cannot be used.")}
    k = _crop(estimate_fingerprint([_crop(g) for g in grays]))
    return {**base, **compare_to_fingerprint(k, test, len(grays))}


LOO_SIDE = 1024  # the coherence check uses a centre crop of this size (memory)
LOO_MAX_REFS = 12


def reference_coherence(grays: list[np.ndarray]) -> dict:
    """Do the reference photos really share one sensor pattern? Leave-one-out: each reference is tested against a
    fingerprint built from the OTHERS. If the references came from different cameras (or are copies of one picture) this
    fails, and a fingerprint built from them must not be trusted."""
    sub = [g[: LOO_SIDE, : LOO_SIDE] if min(g.shape) >= LOO_SIDE else g for g in grays[:LOO_MAX_REFS]]
    sub = [_crop(g) for g in sub]
    nums, dens, resid = [], [], []
    for g in sub:
        w = noise_residual(g)
        resid.append(w)
        nums.append((w * g).astype(np.float32))
        dens.append((g.astype(np.float32)) ** 2)
    num, den = sum(n.astype(np.float64) for n in nums), sum(d.astype(np.float64) for d in dens)
    pces = []
    for i, g in enumerate(sub):
        k_i = _zero_mean(((num - nums[i]) / np.maximum(den - dens[i], 1.0)).astype(np.float32))
        pces.append(_pce(resid[i], k_i * g)[0])
    arr = np.array(pces)
    return {"n_tested": len(pces), "median_pce": round(float(np.median(arr)), 1), "min_pce": round(float(arr.min()), 1),
            "fraction_consistent": round(float((arr >= PCE_MATCH).mean()), 2), "crop": [int(sub[0].shape[0]), int(sub[0].shape[1])],
            "per_reference_pce": [round(float(v), 1) for v in arr], "outliers": [int(i) for i in np.nonzero(arr < PCE_NOISE)[0]]}


def compare_pair(path_a: Path | str, path_b: Path | str) -> dict:
    """Do two questioned images share a sensor pattern? (residual vs residual; weaker than using references)."""
    a, b = load_gray(path_a), load_gray(path_b)
    if a.shape != b.shape:
        return {"status": "not_comparable", "summary": "The images have different pixel sizes, so their noise cannot be compared.",
                "limits": LIMITS}
    if min(a.shape) < 256:
        return {"status": "not_comparable", "summary": "Too small for a sensor-noise comparison.", "limits": LIMITS}
    pce, ncc, shift = _pce(noise_residual(_crop(a)), noise_residual(_crop(b)))
    return {**verdict_from(pce, 2, shift, pair=True), "ncc": round(ncc, 4), "method": "residual–residual PCE", "limits": LIMITS,
            "summary_note": "Residual-to-residual comparison is weaker than comparing against a fingerprint from many photos."}
