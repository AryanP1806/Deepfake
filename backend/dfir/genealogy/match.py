"""Three-tier media matching: hash → embedding → geometric verification (+ temporal for A/V).

Geometric verification (SIFT + RANSAC similarity transform) is what lets a crop, resize,
mirror, caption bar or screen recording be recognised as the *same underlying media* and
recovers how it was transformed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageOps

from .fingerprint import hamming_hex

PDQ_MATCH = 31  # Meta's recommended PDQ near-duplicate threshold (of 256 bits)
PDQ_LOOSE = 70
WORK_SIDE = 900


@dataclass
class Geometry:
    mirrored: bool
    scale_b_to_a: float  # A-pixels per B-pixel for shared content
    rotation_deg: float
    inliers: int
    inlier_ratio: float
    cov_a: float  # fraction of A covered by B's content
    cov_b: float  # fraction of B covered by A's content
    b_in_a: list[float]  # B's frame projected into A (x0,y0,x1,y1) normalised to A
    a_in_b: list[float]  # A's frame projected into B normalised to B
    matrix: list[list[float]] = field(default_factory=list)  # B(work) -> A(work) affine


@dataclass
class MatchResult:
    matched: bool
    tier: str  # none | hash | embedding | geometric | temporal | audio
    verified: bool
    scores: dict
    geometry: Geometry | None = None
    temporal: dict | None = None


def _work(im: Image.Image) -> tuple[np.ndarray, float]:
    im = im.convert("L")
    s = min(1.0, WORK_SIDE / max(im.size))
    if s < 1:
        im = im.resize((round(im.width * s), round(im.height * s)), Image.BILINEAR)
    return np.asarray(im), s


_sift = None


def _features(gray: np.ndarray):
    global _sift
    if _sift is None:
        _sift = cv2.SIFT_create(nfeatures=3000)
    return _sift.detectAndCompute(gray, None)


def _box_cover(pts: np.ndarray, w: int, h: int) -> tuple[float, list[float]]:
    x0, y0 = pts[:, 0].min(), pts[:, 1].min()
    x1, y1 = pts[:, 0].max(), pts[:, 1].max()
    ix0, iy0, ix1, iy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    return float(inter / (w * h)), [float(x0 / w), float(y0 / h), float(x1 / w), float(y1 / h)]


def geometric_verify(a: Image.Image, b: Image.Image) -> Geometry | None:
    ga, sa = _work(a)
    best = None
    for mirrored in (False, True):
        bb = ImageOps.mirror(b) if mirrored else b
        gb, sb = _work(bb)
        ka, da = _features(ga)
        kb, db = _features(gb)
        if da is None or db is None or len(ka) < 8 or len(kb) < 8:
            continue
        pairs = cv2.BFMatcher(cv2.NORM_L2).knnMatch(db, da, k=2)
        good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.75 * n.distance]
        if len(good) < 12:
            continue
        src = np.float32([kb[m.queryIdx].pt for m in good])
        dst = np.float32([ka[m.trainIdx].pt for m in good])
        M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
        if M is None:
            continue
        n_in = int(inl.sum())
        if best is None or n_in > best[0]:
            best = (n_in, n_in / len(good), M, mirrored, gb.shape, sb)
    if best is None:
        return None
    n_in, ratio, M, mirrored, (hb, wb), sb = best
    ha, wa = ga.shape
    s_work = float(np.hypot(M[0, 0], M[1, 0]))
    rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    corners_b = np.float32([[0, 0], [wb, 0], [wb, hb], [0, hb]])
    proj_b = corners_b @ M[:, :2].T + M[:, 2]
    cov_a, b_in_a = _box_cover(proj_b, wa, ha)
    Mi = cv2.invertAffineTransform(M)
    corners_a = np.float32([[0, 0], [wa, 0], [wa, ha], [0, ha]])
    proj_a = corners_a @ Mi[:, :2].T + Mi[:, 2]
    cov_b, a_in_b = _box_cover(proj_a, wb, hb)
    # convert work-scale to original-pixel scale: A_px per B_px
    scale = s_work * sb / sa
    return Geometry(mirrored=mirrored, scale_b_to_a=scale, rotation_deg=rot, inliers=n_in, inlier_ratio=ratio,
                    cov_a=cov_a, cov_b=cov_b, b_in_a=b_in_a, a_in_b=a_in_b, matrix=M.tolist())


def is_verified(g: Geometry | None) -> bool:
    return bool(g and g.inliers >= 25 and g.inlier_ratio >= 0.25 and 0.05 < g.scale_b_to_a < 20)


def compare_images(a: Image.Image, b: Image.Image, fa: dict, fb: dict, emb_cos: float | None = None,
                   gate_cos: float | None = None) -> MatchResult:
    d = hamming_hex(fa["pdq"], fb["pdq"])
    dm = hamming_hex(fa["pdq"], fb["pdq_mirror"])
    scores = {"pdq": d, "pdq_mirror": dm, "cosine": emb_cos}
    tier = "none"
    if min(d, dm) <= PDQ_MATCH:
        tier = "hash"
    elif emb_cos is not None and emb_cos >= 0.88:
        tier = "embedding"
    # Copy discovery screens many unrelated web images: skip the slow geometric step when both the perceptual hash AND the
    # CLIP embedding say the pictures are clearly different (crops keep CLIP similarity high, so they still get through).
    if gate_cos is not None and tier == "none" and min(d, dm) > PDQ_LOOSE and emb_cos is not None and emb_cos < gate_cos:
        return MatchResult(False, "none", False, scores, None)
    # Always try geometric verification: crops and screen recordings defeat global hashes.
    g = geometric_verify(a, b)
    if g:
        scores.update(inliers=g.inliers, inlier_ratio=round(g.inlier_ratio, 3))
    if is_verified(g):
        return MatchResult(True, "geometric", True, scores, g)
    if tier != "none":
        return MatchResult(True, tier, False, scores, g)
    return MatchResult(False, "none", False, scores, g)


def compare_frame_sequences(sa: list[dict], sb: list[dict]) -> dict | None:
    """Temporal alignment of two PDQ frame sequences. Handles trims and partial reuse."""
    if not sa or not sb:
        return None
    hits, offsets, mirrored = 0, [], 0
    for fb in sb:
        best, best_t, mir = 999, None, False
        for fa in sa:
            d = hamming_hex(fa["pdq"], fb["pdq"])
            dm = hamming_hex(fa["pdq"], fb["pdq_mirror"])
            if min(d, dm) < best:
                best, best_t, mir = min(d, dm), fa["t"], dm < d
        if best <= PDQ_LOOSE:
            hits += 1
            offsets.append(round(best_t - fb["t"], 0))
            mirrored += int(mir)
    if hits == 0:
        return None
    off, n_off = Counter(offsets).most_common(1)[0]
    return {"matched_frames_b": hits, "frac_b": hits / len(sb), "offset_s": off, "offset_consistency": n_off / hits,
            "mirrored": mirrored > hits / 2}


def compare_audio(la: list, lb: list) -> dict | None:
    if not la or not lb:
        return None
    index: dict[int, list[int]] = {}
    for h, t in la:
        index.setdefault(h, []).append(t)
    deltas = Counter()
    for h, t in lb:
        for ta in index.get(h, ()):
            deltas[ta - t] += 1
    if not deltas:
        return None
    off, n = deltas.most_common(1)[0]
    return {"aligned_hashes": n, "frac_b": n / max(1, len(lb)), "offset_frames": off,
            "offset_s": off * 256 / 8000}
