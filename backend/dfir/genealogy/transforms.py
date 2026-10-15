"""Infer what transformations relate a matched pair, and which direction derivation likely ran."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps

from .match import Geometry, MatchResult


def _text_only_in(x: list[str], y: list[str]) -> list[str]:
    """Lines of OCR text in x with no fuzzy counterpart in y (OCR is noisy across re-encodes)."""
    from rapidfuzz import fuzz
    out = []
    for t in x:
        t2 = t.strip()
        if len(t2) < 4:
            continue
        if not any(fuzz.partial_ratio(t2.lower(), u.lower()) >= 80 for u in y):
            out.append(t2)
    return out


def _outside_region_stats(b: Image.Image, a_in_b: list[float]) -> dict:
    """Describe B's pixels that are NOT covered by A's content (borders, UI bars, captions or real content)."""
    g = np.asarray(b.convert("L"), dtype=np.float32)
    h, w = g.shape
    x0, y0, x1, y1 = [int(round(v * s)) for v, s in zip(a_in_b, (w, h, w, h))]
    mask = np.ones_like(g, bool)
    mask[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = False
    if mask.sum() < 0.02 * g.size:
        return {"outside_frac": float(mask.mean())}
    out = g[mask]
    edges = cv2.Canny(g.astype(np.uint8), 60, 160) > 0
    mu = cv2.blur(g, (9, 9))
    local_std = np.sqrt(np.maximum(cv2.blur(g * g, (9, 9)) - mu * mu, 0))
    return {"outside_frac": float(mask.mean()), "outside_std": float(out.std()),
            "outside_flat_frac": float((local_std[mask] < 6).mean()),
            "outside_edge_density": float(edges[mask].mean()), "inside_edge_density": float(edges[~mask].mean() + 1e-6)}


def aligned_difference(a: Image.Image, b: Image.Image, g: Geometry) -> dict:
    """Warp B onto A and find localised content differences (possible partial edits/overlays)."""
    from .match import _work
    ga, sa = _work(a)
    bb = ImageOps.mirror(b) if g.mirrored else b
    gb, sb = _work(bb)
    M = np.array(g.matrix, dtype=np.float32)
    warped = cv2.warpAffine(gb, M, (ga.shape[1], ga.shape[0]), flags=cv2.INTER_LINEAR, borderValue=0)
    valid = cv2.warpAffine(np.ones_like(gb) * 255, M, (ga.shape[1], ga.shape[0]), borderValue=0) > 0
    valid = cv2.erode(valid.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    if valid.sum() < 1000:
        return {"regions": []}
    A = cv2.GaussianBlur(ga.astype(np.float32), (5, 5), 0)
    B = cv2.GaussianBlur(warped.astype(np.float32), (5, 5), 0)
    # normalise brightness/contrast differences (filters, recompression)
    B = (B - B[valid].mean()) / (B[valid].std() + 1e-6) * A[valid].std() + A[valid].mean()
    diff = np.abs(A - B) * valid
    thr = max(35.0, float(np.percentile(diff[valid], 99)))
    mask = (diff > thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    regions = []
    area = valid.sum()
    seam = cv2.dilate((~valid).astype(np.uint8), np.ones((31, 31), np.uint8)) > 0
    seam[:15, :] = seam[-15:, :] = True  # the frame edge counts as a seam too (caption bars sit there)
    seam[:, :15] = seam[:, -15:] = True
    for i in range(1, n):
        x, y, w, h, ar = stats[i]
        comp = lab == i
        if (comp & seam).any() and max(w, h) > 3 * max(1, min(w, h)):
            continue  # elongated band along the overlap edge: crop seam / caption bar, not an interior edit
        if ar > 0.004 * area:
            regions.append({"box": [x / ga.shape[1], y / ga.shape[0], (x + w) / ga.shape[1], (y + h) / ga.shape[0]],
                            "area_frac": float(ar / area)})
    regions.sort(key=lambda r: -r["area_frac"])
    return {"regions": regions[:6], "mean_abs_diff": float(diff[valid].mean())}


def infer(a_meta: dict, b_meta: dict, res: MatchResult, a_img: Image.Image | None, b_img: Image.Image | None) -> dict:
    """Return {'transforms_b_from_a': [...], 'transforms_a_from_b': [...], 'direction_score': float, 'notes': [...]}.

    direction_score > 0 favours A as the ancestor of B. Each transform carries the evidence used.
    """
    t_ab: list[dict] = []  # transforms that turn A into B
    t_ba: list[dict] = []
    score = 0.0
    notes: list[str] = []
    g = res.geometry

    if g and res.verified:
        if g.mirrored:
            t_ab.append({"op": "mirror", "evidence": "best geometric fit required a horizontal flip"})
            t_ba.append({"op": "mirror", "evidence": "best geometric fit required a horizontal flip"})
        if abs(g.rotation_deg) > 1.5:
            t_ab.append({"op": "rotate", "degrees": round(g.rotation_deg, 1)})
            t_ba.append({"op": "rotate", "degrees": round(-g.rotation_deg, 1)})
        crop_ab = g.cov_a < 0.9  # B shows only part of A
        crop_ba = g.cov_b < 0.9  # A shows only part of B (B has extra area)
        extra_kind = None
        if crop_ba and b_img is not None:
            st = _outside_region_stats(b_img, g.a_in_b)
            ratio = st.get("outside_edge_density", 1) / st.get("inside_edge_density", 1)
            # borders, letterboxing, app chrome and caption bars are mostly flat colour; photographic content is not
            uniform = st.get("outside_std", 99) < 18 or ratio < 0.25 or st.get("outside_flat_frac", 0) > 0.55
            # captions/UI bars carry text but far less texture than photographic content
            textish = b_meta.get("ocr_extra", False) and ratio < 0.6
            extra_kind = "padding" if (uniform or textish) else "content"
            notes.append(f"B has extra area outside A's content ({st.get('outside_frac', 0):.0%}); classified as "
                         f"{'added border/caption/UI' if extra_kind == 'padding' else 'genuine image content'}")
        if crop_ab and not crop_ba:
            t_ab.append({"op": "crop", "kept_region_of_a": [round(v, 3) for v in g.b_in_a],
                         "kept_frac": round(g.cov_a, 3)})
            score += 3
        if crop_ba and not crop_ab:
            if extra_kind == "padding":
                op = "screen_capture_or_border" if b_meta.get("screen_like") else "border_or_caption_bar"
                t_ab.append({"op": op, "a_region_in_b": [round(v, 3) for v in g.a_in_b], "evidence": notes[-1]})
                score += 2.5
            else:
                t_ba.append({"op": "crop", "kept_region_of_b": [round(v, 3) for v in g.a_in_b],
                             "kept_frac": round(g.cov_b, 3)})
                score -= 3
        if crop_ab and crop_ba:
            t_ab.append({"op": "crop+reframe", "kept_region_of_a": [round(v, 3) for v in g.b_in_a]})
            t_ba.append({"op": "crop+reframe", "kept_region_of_b": [round(v, 3) for v in g.a_in_b]})
        # resolution: B pixels per A pixel for the shared content
        density_b_over_a = 1 / max(1e-6, g.scale_b_to_a)
        if density_b_over_a < 0.85:
            t_ab.append({"op": "downscale", "factor": round(density_b_over_a, 3)})
            score += 1.0
        elif density_b_over_a > 1.18:
            t_ba.append({"op": "downscale", "factor": round(1 / density_b_over_a, 3)})
            score -= 1.0
        if a_img is not None and b_img is not None:
            diff = aligned_difference(a_img, b_img, g)
            if diff["regions"]:
                big = diff["regions"][0]
                t_ab.append({"op": "localized_change", "regions_in_a": diff["regions"],
                             "evidence": "aligned pixel comparison shows localised differences beyond compression noise"})
                t_ba.append({"op": "localized_change", "regions_in_a": diff["regions"]})
                notes.append(f"{len(diff['regions'])} localised difference region(s); largest covers "
                             f"{big['area_frac']:.1%} of the overlap")
    elif res.matched:
        notes.append(f"Matched by {res.tier} only; transformations not geometrically recovered")

    # added text (OCR lines present in B but not A)
    a_txt, b_txt = a_meta.get("ocr", []), b_meta.get("ocr", [])
    only_b, only_a = _text_only_in(b_txt, a_txt), _text_only_in(a_txt, b_txt)
    if only_b:
        t_ab.append({"op": "text_overlay_added", "text": only_b[:5]})
        score += 1.5
    if only_a:
        t_ba.append({"op": "text_overlay_added", "text": only_a[:5]})
        score -= 1.5

    # encoding / format
    if a_meta.get("mime") != b_meta.get("mime"):
        t_ab.append({"op": "format_change", "from": a_meta.get("mime"), "to": b_meta.get("mime")})
        t_ba.append({"op": "format_change", "from": b_meta.get("mime"), "to": a_meta.get("mime")})
        if a_meta.get("modality") == "image" and b_meta.get("modality") == "video":
            notes.append("A still image appears inside a video (video built from the image, or the image is a frame grab)")
    qa, qb = a_meta.get("jpeg_q"), b_meta.get("jpeg_q")
    if qa and qb and abs(qa - qb) >= 5:
        if qb < qa:
            t_ab.append({"op": "recompress", "quality": qb})
            score += 0.5
        else:
            t_ba.append({"op": "recompress", "quality": qa})
            score -= 0.5
    elif a_meta.get("sha256") != b_meta.get("sha256") and a_meta.get("mime") == b_meta.get("mime"):
        t_ab.append({"op": "re-encode", "evidence": "same content, different bytes"})
        t_ba.append({"op": "re-encode", "evidence": "same content, different bytes"})

    # time (tie-breaker; publication times are only as reliable as their source)
    ta, tb = a_meta.get("time"), b_meta.get("time")
    if ta and tb and ta != tb:
        score += 1.5 if ta < tb else -1.5
        notes.append(f"{'A' if ta < tb else 'B'} was observed earlier ({min(ta, tb)})")
    return {"transforms_b_from_a": t_ab, "transforms_a_from_b": t_ba, "direction_score": score, "notes": notes}
