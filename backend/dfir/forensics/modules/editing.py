"""Module 3 — editing and recompression analysis: ELA, noise residuals, resampling, JPEG grid / double compression,
copy-move, and a localisation map that only flags regions where INDEPENDENT cues agree.

Reuses the existing pixel-forensics functions (``ela``, ``noise_residual``) and the demosaicing tiles of the
camera-processing module, so the same measurements are not made twice.

No single cue here is evidence of manipulation: ordinary recompression, resizing and platform processing trigger every one
of them. The module therefore (a) reports measurements as observations, (b) turns them into a *lead* only when at least two
independent cue families overlap in the same place, and (c) states the measured false-positive rate of that rule.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from ...analyzers.image_forensics import ela, noise_residual
from ..base import Evidence, FCtx, Finding, ForensicModule, ModuleResult
from ..registry import register
from . import camera as cam

TILE = 64
RS_TILE = 128
Z_CUE = 2.5


def _rz(v: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """Robust z-score (median / MAD)."""
    x = v[mask] if mask is not None else v.ravel()
    med = np.median(x)
    mad = 1.4826 * np.median(np.abs(x - med)) + 1e-9
    return (v - med) / mad


def _tile_mean(a: np.ndarray, tile: int) -> np.ndarray:
    h, w = a.shape[:2]
    ty, tx = h // tile, w // tile
    return a[: ty * tile, : tx * tile].reshape(ty, tile, tx, tile).mean(axis=(1, 3))


def _unexplained(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    """z-score of log(y) after removing its dependence on texture log(x): what is left is unusual for the local texture."""
    ly, lx = np.log(y + 0.5), np.log(x + 0.5)
    ok = np.isfinite(ly) & np.isfinite(lx)
    if ok.sum() < 8 or lx.std() < 1e-6:
        return _rz(ly)
    lo, hi = np.percentile(lx, [10, 90])
    sel = ok & (lx >= lo) & (lx <= hi)
    b, a = np.polyfit(lx[sel], ly[sel], 1) if sel.sum() >= 6 else (0.0, np.median(ly))
    return _rz(ly - (a + b * lx))


# ------------------------------------------------------------------------------------------------ level cues
def level_cues(rgb: np.ndarray) -> dict:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    e = _tile_mean(ela(rgb), TILE)
    nres = _tile_mean(np.abs(noise_residual(rgb)), TILE)
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    tex = _tile_mean(np.hypot(gx, gy), TILE)
    return {"ela": _unexplained(e, tex), "noise": _unexplained(nres, tex), "ela_raw": e, "noise_raw": nres, "texture": tex}


# ------------------------------------------------------------------------------------------------ resampling
def _periodic_prominence(v: np.ndarray) -> float:
    """Prominence of the strongest spectral peak of a 1-D variance sequence. The slow scene-dependent trend is removed first,
    and the JPEG 8-pixel grid frequencies and the Nyquist peak of demosaicing are ignored."""
    n = len(v)
    v = v - ndi.uniform_filter1d(v, size=max(5, n // 16), mode="nearest")
    s = np.abs(np.fft.rfft(v * np.hanning(n)))[: n // 2 + 1]
    k = np.arange(len(s))
    keep = (k >= max(4, n // 64)) & (k <= n // 2 - 3)
    for m in range(1, 5):  # JPEG block frequencies m/8 of the sampling rate
        keep &= np.abs(k - m * n / 8) > 1.6
    x = s[keep]
    if len(x) < 10:
        return 0.0
    med = np.median(x)
    mad = 1.4826 * np.median(np.abs(x - med)) + 1e-9
    return float((x.max() - med) / mad)


def resample_tile_scores(gray: np.ndarray, tile: int = RS_TILE) -> np.ndarray:
    """Interpolation makes the variance of the second derivative periodic (Gallagher 2005). Per tile: the SMALLER of the
    horizontal and vertical prominences, because a resize leaves the same kind of peak in both directions while noise
    rarely does."""
    d2x = np.abs(gray[:, 2:] + gray[:, :-2] - 2 * gray[:, 1:-1])
    d2y = np.abs(gray[2:, :] + gray[:-2, :] - 2 * gray[1:-1, :])
    d2x, d2y = np.minimum(d2x, np.percentile(d2x, 99)), np.minimum(d2y, np.percentile(d2y, 99))  # strong edges would dominate
    h, w = gray.shape
    ty, tx = (h - 2) // tile, (w - 2) // tile
    out = np.zeros((ty, tx), np.float32)
    for i in range(ty):
        for j in range(tx):
            px = d2x[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile].mean(axis=0)
            py = d2y[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile].mean(axis=1)
            out[i, j] = min(_periodic_prominence(px), _periodic_prominence(py))
    return out


def resample_global_score(gray: np.ndarray) -> float:
    return float(resample_tile_scores(gray, tile=min(gray.shape[0], gray.shape[1], 512) - 2).max())


CFA_WEAK_Z = 2.0  # a tile whose demosaicing trace is this many robust-sigmas weaker than the rest of the image
RS_GLOBAL_Z = 10.0  # whole-image prominence at which interpolation periodicity is declared (tile-level scores did not separate resized regions)


# ------------------------------------------------------------------------------------------------ JPEG grid and double compression
def grid_offset(gray: np.ndarray) -> tuple[tuple[int, int], float]:
    """Offset of the 8x8 block grid and how strongly the grid shows. (0, 0) = aligned with the image origin."""
    out, strength = [], []
    for a in (gray, gray.T):
        d = np.abs(np.diff(a, axis=1)).mean(axis=0)
        n = (len(d) // 8) * 8
        e = d[:n].reshape(-1, 8).mean(axis=0)
        k = int(np.argmax(e))
        others = np.delete(e, k)
        strength.append(float(e[k] / (others.mean() + 1e-6)))
        out.append((k - 7) % 8)
    return (out[0], out[1]), float(min(strength))


def grid_tile_offsets(gray: np.ndarray, tile: int = 128) -> tuple[np.ndarray, np.ndarray]:
    h, w = gray.shape
    ty, tx = h // tile, w // tile
    off = np.zeros((ty, tx, 2), np.int8)
    st = np.zeros((ty, tx), np.float32)
    for i in range(ty):
        for j in range(tx):
            o, s = grid_offset(gray[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile])
            off[i, j], st[i, j] = o, s
    return off, st


def double_jpeg_score(gray: np.ndarray, qtable: list[int] | None) -> dict:
    """Double quantisation leaves periodic gaps in the histogram of low-frequency DCT coefficients. Re-computes the 8x8 DCT
    on the decoded pixels (aligned to the file's block grid) and looks for periodicity in the histogram of the quantised values."""
    if not qtable:
        return {"assessable": False}
    h, w = gray.shape
    h8, w8 = h // 8 * 8, w // 8 * 8
    blocks = gray[:h8, :w8].reshape(h8 // 8, 8, w8 // 8, 8).transpose(0, 2, 1, 3).reshape(-1, 8, 8) - 128.0
    if len(blocks) < 400:
        return {"assessable": False}
    dct = np.stack([cv2.dct(b) for b in blocks[:20000]])
    best, where = 0.0, None
    for (u, v) in ((0, 1), (1, 0), (1, 1), (0, 2), (2, 0)):
        q = qtable[v * 8 + u] if len(qtable) >= 64 else 0
        if q < 2:
            continue
        idx = np.rint(dct[:, v, u] / q).astype(int)
        idx = idx[np.abs(idx) <= 40]
        if len(idx) < 3000:
            continue
        hist = np.bincount(idx + 40, minlength=81).astype(float)
        base = ndi.median_filter(hist, size=7, mode="nearest")
        res = (hist - base)[8:-8]
        res[len(res) // 2 - 1:len(res) // 2 + 2] = 0  # the zero bin dominates and is not periodic information
        if np.abs(res).sum() == 0:
            continue
        spec = np.abs(np.fft.rfft(res))
        spec[:2] = 0
        score = float(spec.max() / (np.median(spec) + 1e-6))
        if score > best:
            best, where = score, (u, v)
    return {"assessable": best > 0, "score": round(best, 2), "coef": where}


DQ_Z = 9.0


# ------------------------------------------------------------------------------------------------ copy-move
def copy_move(gray8: np.ndarray, min_dist: int = 48, ratio: float = 0.6, min_inliers: int = 10) -> list[dict]:
    sift = cv2.SIFT_create(nfeatures=3000)
    kp, des = sift.detectAndCompute(gray8, None)
    if des is None or len(kp) < 30:
        return []
    pts = np.array([k.pt for k in kp], np.float32)
    bf = cv2.BFMatcher(cv2.NORM_L2)
    nn = bf.knnMatch(des, des, k=3)
    a, b = [], []
    for i, m in enumerate(nn):
        if len(m) < 3:
            continue
        cand, second = m[1], m[2]  # m[0] is the keypoint itself
        if cand.distance < ratio * second.distance and np.linalg.norm(pts[i] - pts[cand.trainIdx]) >= min_dist:
            a.append(pts[i])
            b.append(pts[cand.trainIdx])
    clusters: list[dict] = []
    A, B = np.array(a, np.float32), np.array(b, np.float32)
    for _ in range(3):
        if len(A) < min_inliers:
            break
        M, inl = cv2.estimateAffinePartial2D(A, B, method=cv2.RANSAC, ransacReprojThreshold=3.0, maxIters=2000)
        if M is None or inl is None or int(inl.sum()) < min_inliers:
            break
        inl = inl.ravel().astype(bool)
        s = float(np.hypot(M[0, 0], M[1, 0]))
        rot = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
        if abs(s - 1) < 0.02 and abs(rot) < 1 and np.hypot(M[0, 2], M[1, 2]) < min_dist:  # (near-)identity: not a copy
            A, B = A[~inl], B[~inl]
            continue
        src, dst = A[inl], B[inl]
        box = lambda p: [int(p[:, 0].min()), int(p[:, 1].min()), int(p[:, 0].max()), int(p[:, 1].max())]  # noqa: E731
        clusters.append({"n_matches": int(inl.sum()), "scale": round(s, 3), "rotation_deg": round(rot, 1), "source_box": box(src), "copy_box": box(dst)})
        A, B = A[~inl], B[~inl]
    # a copy and its source match each other in both directions: keep one entry per pair of regions
    uniq: list[dict] = []
    for c in sorted(clusters, key=lambda c: -c["n_matches"]):
        if not any(c["source_box"] == u["copy_box"] and c["copy_box"] == u["source_box"] or
                   (_iou(c["source_box"], u["copy_box"]) > 0.5 and _iou(c["copy_box"], u["source_box"]) > 0.5) for u in uniq):
            uniq.append(c)
    return uniq


def _iou(a, b) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


# ------------------------------------------------------------------------------------------------ fusion
def fuse(cues: dict[str, np.ndarray], min_cues: int = 2, min_tiles: int = 2) -> tuple[np.ndarray, list[dict]]:
    """cues: family -> boolean tile mask (all on the same grid). Returns (agreement count, regions)."""
    shape = next(iter(cues.values())).shape
    count = np.zeros(shape, np.int8)
    for m in cues.values():
        count += m.astype(np.int8)
    lab, n = ndi.label(count >= min_cues, structure=np.ones((3, 3)))
    regions = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(lab == i)
        if len(ys) < min_tiles:
            continue
        fams = sorted(f for f, m in cues.items() if m[ys, xs].any())
        regions.append({"tile_box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1], "n_tiles": int(len(ys)), "cues": fams})
    return count, regions


def _resize_mask(m: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    return cv2.resize(m.astype(np.uint8), (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)


def analyse_cues(rgb: np.ndarray, qtable: list[int] | None, is_jpeg: bool, cfa: dict | None = None) -> dict:
    """All cue maps on the 64-px tile grid plus global summaries. Pure function (used by the benchmark too)."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    lc = level_cues(rgb)
    grid = lc["ela"].shape
    cues: dict[str, np.ndarray] = {}
    cues["compression/noise level"] = (lc["ela"] > Z_CUE) | (np.abs(lc["noise"]) > Z_CUE)
    # CFA: opposite phase, or a pattern much weaker than in the rest of the image
    t = (cfa or {}).get("tiles") or cam.cfa_tiles(rgb)
    summ = (cfa or {}).get("summary") or cam.cfa_summary(t)
    if summ.get("present"):
        az = np.abs(t["z"])
        weak = t["ok"] & (_rz(az, t["ok"]) < -CFA_WEAK_Z)
        opposite = t["ok"] & (t["z"] * summ["dominant_sign"] < -cam.CFA_Z)
        cm = weak | opposite
        cues["demosaicing"] = cm if cm.shape == grid else _resize_mask(cm, grid)
    # resampling (whole-image only: tile-level scores did not separate resized regions from untouched ones in validation)
    rs_score = resample_global_score(gray)
    out = {"grid": grid, "level": lc, "cfa": summ, "cfa_tiles": t, "resampling": {"score": round(rs_score, 2), "threshold": RS_GLOBAL_Z,
                                                                                    "flag": rs_score >= RS_GLOBAL_Z}}
    # JPEG grid
    if is_jpeg:
        off, st = grid_tile_offsets(gray)
        g_off, g_str = grid_offset(gray)
        out["jpeg_grid"] = {"offset": g_off, "strength": round(g_str, 3)}
        strong = st > 1.15
        if strong.any() and g_str > 1.15:
            mism = strong & ((off[..., 0] != g_off[0]) | (off[..., 1] != g_off[1]))
            if mism.any() and mism.mean() < 0.5:
                cues["JPEG grid"] = _resize_mask(mism, grid)
        out["double_jpeg"] = double_jpeg_score(gray, qtable)
    return {**out, "cues": cues}


def real_fp_note(key: str) -> str:
    """How often this flag fires on real web photographs in the validation run (an upper bound on the false-positive rate)."""
    from ..base import validation_data
    r = ((((validation_data().get("modules") or {}).get("editing") or {}).get("metrics") or {}).get("real_photo_false_positives") or {}).get("real")
    x = (r or {}).get(f"{key}_flagged")
    if not x or not x.get("n"):
        return ""
    return f" In validation this flag fired on {x['rate']:.0%} of {x['n']} real web photographs (95 % interval {x['ci95'][0]:.0%}–{x['ci95'][1]:.0%})."


@register
class EditingModule(ForensicModule):
    id = "editing"
    title = "Editing and recompression analysis"
    question = "Is there any sign that parts of the image were processed differently from the rest, resized, recompressed or copied?"
    version = "1.0"
    modalities = ("image",)
    library_versions = ("numpy", "scipy", "cv2", "PIL")
    order = 30
    limitations = [
        "Every cue here also fires on innocent processing (screenshots, platform recompression, resizing, filters). Treat flags as leads for visual review.",
        "A clean result does not show authenticity: careful edits, fully generated images and regions processed with the whole image leave none of these traces.",
        "Localisation boxes are tile-based (64 px) and approximate.",
        "Measured on simulated edits: splices whose compression history differs from the rest of the picture are localised; splices pasted before a single "
        "JPEG save, and rescaled pastes, are largely missed. See the validation results.",
        "Thresholds were set on simulated edits; the false-positive rate on real web photos is reported in the validation results.",
    ]

    def analyse(self, fx: FCtx) -> ModuleResult:
        rgb = fx.native_rgb(1024)
        h, w = rgb.shape[:2]
        res = ModuleResult(module=self.id, title=self.title, version=self.version)
        F, ev = res.findings, res.evidence
        if min(h, w) < 192:
            F.append(Finding(id="editing.size", title="Image too small", kind="unknown",
                             statement=f"The image is {w}×{h}: too small for tile-based editing analysis (needs at least 192 px on each side)."))
            res.status = "partial"
            res.summary = F[0].statement
            return res
        js = fx.shared.get("jpeg_structure") or cam.jpeg_structure(fx.path)
        is_jpeg = js is not None
        info = analyse_cues(rgb, (js or {}).get("qtable_luma"), is_jpeg, fx.shared.get("cfa"))
        cues = dict(info["cues"])

        # ---- ELA and noise (existing measurements, reported as observations; the pixel-forensics analyzer already records claims)
        lc = info["level"]
        n_ela, n_noise = int((lc["ela"] > Z_CUE).sum()), int((np.abs(lc["noise"]) > Z_CUE).sum())
        F.append(Finding(id="ela.tiles", title="Error-level analysis", kind="observation", value=n_ela, unit="tiles",
                         statement=f"{n_ela} of {lc['ela'].size} tiles recompress unusually for their texture (after removing the effect of texture).",
                         why="Regions saved at a different quality or pasted from elsewhere recompress differently.",
                         caution="Edges, text and smooth gradients also do; compare with the heat map, not just the count.", emit_claim=False))
        F.append(Finding(id="noise.tiles", title="Noise-residual consistency", kind="observation", value=n_noise, unit="tiles",
                         statement=f"{n_noise} of {lc['noise'].size} tiles have a noise level unusual for their texture.",
                         caution="Local blur, exposure and denoising change noise without any splicing.", emit_claim=False))

        # ---- resampling
        rs = info["resampling"]
        if rs["flag"]:
            F.append(Finding(id="resampling.global", title="Resampling", kind="supported", confidence=0.5, value=rs["score"], unit="peak prominence",
                             statement=f"Interpolation periodicity is present in both directions (prominence {rs['score']:.0f}, threshold {rs['threshold']:.0f}): "
                                       "the image has been enlarged or rotated as a whole.",
                             why="Resizing by a non-integer factor leaves a periodic pattern in the second derivative of the pixels.",
                             caution="Shows the image was rescaled at some point (very common for shared images), not that its content was changed. "
                                     "Reductions and integer-factor scaling are not detectable by this test." + real_fp_note("resampling"), data=rs))
        else:
            F.append(Finding(id="resampling.none", title="Resampling", kind="observation", value=rs["score"], unit="peak prominence",
                             statement=f"No interpolation periodicity was found (prominence {rs['score']:.1f}, threshold {rs['threshold']:.0f}).",
                             caution="Reductions, integer scaling and strong compression hide it. Not evidence of an unedited image.", data=rs))

        # ---- JPEG grid / double compression
        if is_jpeg:
            jg, dq = info["jpeg_grid"], info["double_jpeg"]
            if jg["strength"] > 1.15 and jg["offset"] != (0, 0):
                F.append(Finding(id="jpeg.grid", title="JPEG block grid", kind="supported", confidence=0.45,
                                 statement=f"The JPEG block grid is offset by {jg['offset']} px from the image origin: the picture was cropped, shifted or "
                                           "placed after an earlier JPEG compression.",
                                 caution="Cropping a JPEG in an editor or app causes this; it is not by itself content manipulation.", data=jg))
            elif jg["strength"] > 1.15:
                F.append(Finding(id="jpeg.grid", title="JPEG block grid", kind="observation",
                                 statement="The JPEG block grid is aligned with the image origin.", data=jg))
            else:
                F.append(Finding(id="jpeg.grid", title="JPEG block grid", kind="unknown",
                                 statement="No clear 8×8 block grid could be measured (smooth, resized or high-quality image).", data=jg))
            if dq.get("assessable") and dq["score"] >= DQ_Z:
                F.append(Finding(id="jpeg.double", title="Double JPEG compression", kind="supported", confidence=0.5, value=dq["score"], unit="peak score",
                                 statement="The DCT histograms show periodic gaps typical of two successive JPEG compressions with different quality settings.",
                                 why="Quantising twice with different steps leaves a comb pattern in the coefficient histogram.",
                                 caution="Anything re-saved or shared through an app is compressed twice. This shows re-saving, not tampering." + real_fp_note("double_jpeg"), data=dq))
            elif dq.get("assessable"):
                F.append(Finding(id="jpeg.double", title="Double JPEG compression", kind="observation", value=dq["score"], unit="peak score",
                                 statement="No double-quantisation pattern was found.",
                                 caution="Double compression with the same or a higher final quality, or after resizing, is not detectable this way.", data=dq))
            else:
                F.append(Finding(id="jpeg.double", title="Double JPEG compression", kind="unknown",
                                 statement="The image has too few usable blocks or too fine a quantisation for the double-compression test."))
        else:
            F.append(Finding(id="jpeg.grid", title="JPEG recompression", kind="unknown",
                             statement="Not a JPEG: block-grid and double-compression checks do not apply (the file may still have been a JPEG earlier)."))

        # ---- copy-move
        cm = copy_move(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        if cm:
            F.append(Finding(id="copy_move", title="Copy-move", kind="hypothesis", confidence=0.4, value=len(cm), unit="region pair(s)",
                             statement=f"{len(cm)} pair(s) of image regions share matching local features with a consistent geometric transform "
                                       f"(strongest: {cm[0]['n_matches']} matches, scale {cm[0]['scale']}×).",
                             why="Duplicating part of a picture to hide or add something creates two regions with identical local structure.",
                             caution="Repeating patterns (windows, tiles, foliage, text, crowds) and symmetric objects match too. Review the two regions by eye." + real_fp_note("copy_move"),
                             data={"pairs": cm}))
            mask = np.zeros((rgb.shape[0] // TILE, rgb.shape[1] // TILE), bool)
            for c in cm:
                for bx in (c["source_box"], c["copy_box"]):
                    mask[bx[1] // TILE:bx[3] // TILE + 1, bx[0] // TILE:bx[2] // TILE + 1] = True
            if mask.shape == info["grid"]:
                cues["copy-move"] = mask
        else:
            F.append(Finding(id="copy_move", title="Copy-move", kind="observation",
                             statement="No duplicated regions were found by local-feature matching.",
                             caution="Re-touched, heavily transformed or textureless copies are missed."))

        # ---- localisation: only where independent cue families agree
        count, regions = fuse(cues) if cues else (np.zeros(info["grid"], np.int8), [])
        hm = _heat(rgb, count, regions)
        sha = fx.store("edit_map", "Where independent editing cues agree (red outline = candidate region)", hm)
        ev.append(Evidence(id="ev_map", kind="image", label="Editing-cue agreement map", sha256=sha, data={"regions": regions}))
        if regions:
            top = max(regions, key=lambda r: (len(r["cues"]), r["n_tiles"]))
            F.append(Finding(id="localisation", title="Candidate edited regions", kind="hypothesis", value=len(regions), unit="region(s)",
                             confidence=min(0.35, 0.12 * len(top["cues"])),
                             statement=f"{len(regions)} region(s) show at least two independent cues at once (largest: {top['n_tiles']} tiles; cues: {', '.join(top['cues'])}).",
                             why="Independent measurements (compression level, demosaicing, JPEG grid, resampling, duplication) rarely misfire at the same spot.",
                             caution="A lead for human review only. Screenshots, overlaid text/stickers and platform processing can also stack cues. Not a conclusion that anything was altered." + real_fp_note("localisation"),
                             evidence=["ev_map"], data={"regions": regions, "cue_families": sorted(cues)}))
        else:
            F.append(Finding(id="localisation", title="Candidate edited regions", kind="observation", value=0, unit="region(s)",
                             statement="No area shows two independent editing cues at once.",
                             caution="This does not show the image is unedited: fully generated images, careful edits and whole-image processing leave no local contrast.",
                             evidence=["ev_map"], data={"cue_families": sorted(cues)}))
        res.summary = F[-1].statement
        res.status = "ok"
        fx.shared["editing_regions"] = regions
        return res


def _heat(rgb: np.ndarray, count: np.ndarray, regions: list[dict]) -> Image.Image:
    base = rgb.copy()
    h, w = count.shape
    big = cv2.resize((count >= 1).astype(np.uint8) * 90, (w * TILE, h * TILE), interpolation=cv2.INTER_NEAREST)
    bh, bw = min(base.shape[0], big.shape[0]), min(base.shape[1], big.shape[1])
    tint = np.zeros_like(base)
    tint[:bh, :bw, 0] = big[:bh, :bw]
    out = np.clip(base.astype(np.int16) + tint, 0, 255).astype(np.uint8)
    for r in regions:
        x0, y0, x1, y1 = (v * TILE for v in r["tile_box"])
        cv2.rectangle(out, (x0, y0), (min(out.shape[1] - 1, x1), min(out.shape[0] - 1, y1)), (255, 40, 40), 2)
    return Image.fromarray(out)
