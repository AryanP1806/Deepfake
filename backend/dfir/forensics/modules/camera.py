"""Module 2 — camera-processing fingerprints: colour-filter-array (demosaicing) traces, sharpening halos, noise
behaviour (denoising), lateral chromatic aberration and JPEG quantisation.

These describe the PROCESSING CHAIN the pixels went through. They can show that a file is *consistent with* a camera-like
chain or that it lacks traces such a chain usually leaves. They cannot name a camera, and every trace is destroyed by
resizing, heavy compression and screenshots, so a missing trace is mostly Unknown, not evidence of anything.
"""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, JpegImagePlugin
from scipy import ndimage as ndi

from ..base import Evidence, FCtx, Finding, ForensicModule, ModuleResult
from ..registry import register

TILE = 64
STD_LUMA = np.array([16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
                     18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99])
STD_CHROMA = np.array([17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99, 24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99] + [99] * 32)
CFA_Z = 3.5  # a tile shows the checkerboard variance pattern of demosaicing when |z| exceeds this


# ------------------------------------------------------------------------------------------------------ CFA
def cfa_tiles(rgb: np.ndarray, tile: int = TILE) -> dict:
    """Interpolated green samples are predicted almost perfectly by their neighbours; sensed samples are not. That makes the
    cross-Laplacian residual of the green channel alternate in a checkerboard. Per tile: z-score of that alternation and its
    sign (which of the two phases carries the sensed samples). A real demosaicing trace gives one consistent sign everywhere."""
    g = rgb[..., 1].astype(np.float32)
    lap = np.abs(g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4 * g[1:-1, 1:-1])
    h, w = lap.shape
    ty, tx = h // tile, w // tile
    z = np.zeros((ty, tx), np.float32)
    ok = np.zeros((ty, tx), bool)
    yy, xx = np.indices((tile, tile))
    chk = np.where((yy + xx) % 2 == 0, 1.0, -1.0).astype(np.float32)
    for i in range(ty):
        for j in range(tx):
            m = lap[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile]
            mu = float(m.mean())
            if mu < 0.35 or float(g[i * tile + 1:(i + 1) * tile + 1, j * tile + 1:(j + 1) * tile + 1].std()) < 2.0:  # flat/saturated: no information
                continue
            cv = float(m.std()) / mu
            s = float((chk * m).sum() / m.sum())
            z[i, j] = s * np.sqrt(m.size) / max(cv, 1e-3)
            ok[i, j] = True
    return {"z": z, "ok": ok}


def cfa_summary(t: dict) -> dict:
    z, ok = t["z"], t["ok"]
    n = int(ok.sum())
    if n < 6:
        return {"assessable": False, "n_tiles": n}
    sig = ok & (np.abs(z) > CFA_Z)
    pos, neg = int((sig & (z > 0)).sum()), int((sig & (z < 0)).sum())
    frac = float(sig.sum()) / n
    agree = max(pos, neg) / max(1, pos + neg)
    dom = 1.0 if pos >= neg else -1.0
    present = frac >= 0.30 and agree >= 0.90
    return {"assessable": True, "n_tiles": n, "frac_significant": round(frac, 3), "sign_agreement": round(agree, 3),
            "present": present, "dominant_sign": dom, "median_abs_z": round(float(np.median(np.abs(z[ok]))), 2)}


def cfa_inconsistent_tiles(t: dict, summ: dict) -> np.ndarray:
    """Tiles that break the global demosaicing pattern: the opposite phase, or no pattern where it is otherwise strong."""
    z, ok = t["z"], t["ok"]
    if not summ.get("present"):
        return np.zeros_like(ok)
    opposite = ok & (z * summ["dominant_sign"] < -CFA_Z)
    absent = ok & (np.abs(z) < 1.0)
    return opposite | (absent & (summ["frac_significant"] > 0.6))


# ------------------------------------------------------------------------------------------------------ sharpening / edges
def edge_profiles(gray: np.ndarray, half: int = 6, min_step: float = 35.0, max_edges: int = 4000) -> np.ndarray:
    """Normalised step-edge profiles (0 on the dark shoulder, 1 on the bright shoulder), horizontal and vertical edges."""
    profs = []
    for a in (gray, gray.T):
        d = np.abs(a[:, 2:] - a[:, :-2])
        pk = (d[:, 1:-1] >= d[:, :-2]) & (d[:, 1:-1] > d[:, 2:]) & (d[:, 1:-1] > min_step * 0.7)
        rr, cc = np.nonzero(pk)
        cc = cc + 2  # centre index in a
        keep = (cc >= half + 1) & (cc < a.shape[1] - half - 1)
        rr, cc = rr[keep], cc[keep]
        if len(rr) > max_edges:
            sel = np.random.default_rng(0).choice(len(rr), max_edges, replace=False)
            rr, cc = rr[sel], cc[sel]
        idx = cc[:, None] + np.arange(-half, half + 1)[None, :]
        w = a[rr[:, None], idx]
        lo, hi = w[:, :3].mean(1), w[:, -3:].mean(1)
        step = hi - lo
        flat = (np.abs(w[:, :3] - lo[:, None]).max(1) < 0.12 * np.abs(step) + 2) & (np.abs(w[:, -3:] - hi[:, None]).max(1) < 0.12 * np.abs(step) + 2)
        good = (np.abs(step) >= min_step) & flat
        w, lo, step = w[good], lo[good], step[good]
        p = (w - lo[:, None]) / step[:, None]
        p[step < 0] = p[step < 0]  # (normalisation by the signed step already orients every edge dark -> bright)
        profs.append(p)
    return np.concatenate(profs, axis=0) if profs else np.zeros((0, 2 * half + 1))


def edge_stats(gray: np.ndarray) -> dict:
    p = edge_profiles(gray)
    if len(p) < 150:
        return {"assessable": False, "n_edges": int(len(p))}
    # per-edge texture makes individual maxima biased upwards; the AVERAGE profile keeps only the systematic halo
    pm = p.mean(0)
    halo = (max(0.0, float(pm.max()) - 1.0) + max(0.0, -float(pm.min()))) / 2
    # 10-90 % rise width in pixels by linear interpolation along each profile
    xs = np.arange(p.shape[1])
    widths = []
    for q in p[:600]:
        try:
            a, b = np.argmax(q >= 0.1), np.argmax(q >= 0.9)
            widths.append(max(0.0, float(b - a)))
        except ValueError:
            pass
    return {"assessable": True, "n_edges": int(len(p)), "halo_median": round(float(halo), 4),
            "rise_width_px": round(float(np.median(widths)) if widths else 0.0, 2)}


# ------------------------------------------------------------------------------------------------------ noise / denoising
def noise_profile(gray: np.ndarray, block: int = 16) -> dict:
    """Noise level in the flattest blocks, and how it depends on brightness (shot noise grows with signal; denoised,
    synthetic or heavily tone-mapped images often do not)."""
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    r = np.abs(cv2.filter2D(gray, -1, k, borderType=cv2.BORDER_REFLECT))
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    grad = np.hypot(gx, gy)
    h, w = gray.shape
    hb, wb = h // block, w // block
    if hb * wb < 40:
        return {"assessable": False}
    cut = lambda a: a[: hb * block, : wb * block].reshape(hb, block, wb, block).mean(axis=(1, 3))  # noqa: E731
    sigma = cut(r) * np.sqrt(np.pi / 2) / 6.0
    g, mean = cut(grad), cut(gray)
    flat = g <= np.percentile(g, 20)
    flat &= (mean > 12) & (mean < 243)
    if flat.sum() < 15:
        return {"assessable": False}
    s, m = sigma[flat], mean[flat]
    rr = float(np.corrcoef(m, s ** 2)[0, 1]) if s.std() > 0 and m.std() > 0 else 0.0
    return {"assessable": True, "sigma_median": round(float(np.median(s)), 3), "sigma_p90": round(float(np.percentile(s, 90)), 3),
            "n_blocks": int(flat.sum()), "variance_brightness_corr": round(rr, 3)}


# ------------------------------------------------------------------------------------------------------ lens: lateral CA
def lateral_ca(rgb: np.ndarray, patch: int = 64) -> dict:
    """Magnification mismatch between colour channels (red/blue vs green): shifts grow with distance from the optical
    centre. Fit shift = alpha * (position - centre); report the implied displacement at the image corner."""
    h, w, _ = rgb.shape
    if min(h, w) < 256:
        return {"assessable": False}
    g = rgb[..., 1].astype(np.float32)
    gy, gx = np.gradient(cv2.GaussianBlur(g, (0, 0), 1.0))
    energy = np.hypot(gx, gy)
    win = cv2.createHanningWindow((patch, patch), cv2.CV_32F)
    pts, sh_r, sh_b = [], [], []
    ys = np.linspace(patch, h - 2 * patch, 7).astype(int)
    xs = np.linspace(patch, w - 2 * patch, 7).astype(int)
    cand = sorted(((float(energy[y:y + patch, x:x + patch].mean()), y, x) for y in ys for x in xs), reverse=True)[:28]
    for _, y, x in cand:
        gp = g[y:y + patch, x:x + patch]
        out = []
        for ch in (0, 2):
            cp = rgb[y:y + patch, x:x + patch, ch].astype(np.float32)
            (dx, dy), resp = cv2.phaseCorrelate(gp, cp, win)
            out.append((dx, dy, resp))
        if min(o[2] for o in out) < 0.1 or max(abs(o[0]) for o in out) > 2.5 or max(abs(o[1]) for o in out) > 2.5:
            continue
        pts.append((x + patch / 2 - w / 2, y + patch / 2 - h / 2))
        sh_r.append(out[0][:2])
        sh_b.append(out[1][:2])
    if len(pts) < 8:
        return {"assessable": False, "n_patches": len(pts)}
    P = np.array(pts)
    corner = float(np.hypot(w / 2, h / 2))
    res = {"assessable": True, "n_patches": len(pts)}
    for name, S in (("red", np.array(sh_r)), ("blue", np.array(sh_b))):
        d = P.reshape(-1)
        s = S.reshape(-1)
        alpha = float((d * s).sum() / (d * d).sum())
        pred = alpha * d
        ss_tot = float(((s - s.mean()) ** 2).sum()) or 1e-9
        r2 = 1.0 - float(((s - pred) ** 2).sum()) / ss_tot
        res[name] = {"corner_shift_px": round(alpha * corner, 3), "r2": round(r2, 3)}
    res["consistent"] = bool(max(abs(res[c]["corner_shift_px"]) for c in ("red", "blue")) >= 0.3 and max(res[c]["r2"] for c in ("red", "blue")) >= 0.5)
    return res


def luma_falloff(gray: np.ndarray) -> dict:
    h, w = gray.shape
    if min(h, w) < 128:
        return {"assessable": False}
    sm = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA)
    c = float(sm[6:10, 6:10].mean())
    corners = float(np.mean([sm[:3, :3].mean(), sm[:3, -3:].mean(), sm[-3:, :3].mean(), sm[-3:, -3:].mean()]))
    return {"assessable": True, "corner_to_centre": round(corners / max(c, 1.0), 3)}


# ------------------------------------------------------------------------------------------------------ JPEG structure
def ijg_fit(table: list[int], std: np.ndarray) -> tuple[int, int, bool]:
    """Best libjpeg quality for a quantisation table: (quality, L1 error, exact)."""
    t = np.array(table[: len(std)], float)
    best = (0, 10 ** 9)
    for q in range(1, 101):
        scale = 5000 / q if q < 50 else 200 - 2 * q
        cand = np.clip(np.floor((std * scale + 50) / 100), 1, 255)
        err = int(np.abs(cand - t).sum())
        if err < best[1]:
            best = (q, err)
    return best[0], best[1], best[1] == 0


def jpeg_structure(path: Path) -> dict | None:
    try:
        im = Image.open(path)
    except Exception:  # noqa: BLE001
        return None
    if im.format != "JPEG":
        return None
    q = getattr(im, "quantization", None) or {}
    out: dict = {"progressive": bool(im.info.get("progressive")), "jfif": "jfif" in im.info, "has_exif": bool(im.info.get("exif")),
                 "has_icc": "icc_profile" in im.info, "adobe": "adobe" in im.info, "tables": len(q)}
    try:
        out["subsampling"] = {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}.get(JpegImagePlugin.get_sampling(im), "other")
    except Exception:  # noqa: BLE001
        out["subsampling"] = "unknown"
    if 0 in q:
        lq, lerr, lex = ijg_fit(list(q[0]), STD_LUMA)
        out.update(luma_quality=lq, luma_exact_ijg=lex, luma_ijg_error=lerr)
    if 1 in q:
        cq, cerr, cex = ijg_fit(list(q[1]), STD_CHROMA)
        out.update(chroma_quality=cq, chroma_exact_ijg=cex)
    out["tables_standard"] = bool(out.get("luma_exact_ijg") and (out.get("chroma_exact_ijg", True)))
    out["qtable_luma"] = list(q[0]) if 0 in q else None
    return out


# ------------------------------------------------------------------------------------------------------ module
def _overlay_tiles(rgb: np.ndarray, vals: np.ndarray, tile: int, lo: float, hi: float) -> Image.Image:
    h, w = vals.shape
    v = np.clip((np.abs(vals) - lo) / (hi - lo), 0, 1)
    big = cv2.resize(v.astype(np.float32), (w * tile, h * tile), interpolation=cv2.INTER_NEAREST)
    col = cv2.applyColorMap((big * 255).astype(np.uint8), cv2.COLORMAP_VIRIDIS)[..., ::-1]
    base = rgb[: h * tile + 1, : w * tile + 1][: big.shape[0], : big.shape[1]]
    out = (0.45 * base + 0.55 * col[: base.shape[0], : base.shape[1]]).astype(np.uint8)
    return Image.fromarray(out)


@register
class CameraProcessingModule(ForensicModule):
    id = "camera_pipeline"
    title = "Camera-processing fingerprints"
    question = "Does the pixel data carry the traces of a camera-style processing chain (demosaicing, sharpening, sensor noise, lens, JPEG)?"
    version = "1.0"
    modalities = ("image",)
    library_versions = ("numpy", "scipy", "cv2", "PIL")
    order = 20
    limitations = [
        "These traces describe processing, not origin: a camera, a RAW converter and some image synthesis can all produce them.",
        "Resizing, screenshots, heavy JPEG compression and social-media re-encoding erase demosaicing and noise traces. A missing trace is mostly Unknown.",
        "No camera make or model can be derived from these measurements.",
        "Thresholds were set on simulated sensors; behaviour on real cameras has not been measured.",
    ]

    def analyse(self, fx: FCtx) -> ModuleResult:
        rgb = fx.native_rgb(1024)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        res = ModuleResult(module=self.id, title=self.title, version=self.version)
        F = res.findings
        ev = res.evidence

        # ---- CFA
        t = cfa_tiles(rgb)
        cs = cfa_summary(t)
        fx.shared["cfa"] = {"tiles": t, "summary": cs}
        if not cs["assessable"]:
            F.append(Finding(id="cfa.pattern", title="Colour-filter-array (demosaicing) trace", kind="unknown",
                             statement="Demosaicing trace could not be assessed: too little textured, unsaturated image area.",
                             caution="Small, flat, saturated or heavily smoothed images carry no usable signal."))
        else:
            sha = fx.store("cfa_map", "Demosaicing trace per tile (brighter = stronger checkerboard variance)", _overlay_tiles(rgb, t["z"] * t["ok"], TILE, 0, 10))
            ev.append(Evidence(id="ev_cfa", kind="image", label="CFA trace map", sha256=sha, data={"tile": TILE}))
            if cs["present"]:
                F.append(Finding(id="cfa.pattern", title="Colour-filter-array (demosaicing) trace", kind="supported", confidence=0.55,
                                 value=cs["frac_significant"], unit="of tiles",
                                 statement=f"A consistent demosaicing pattern is present in {cs['frac_significant']:.0%} of assessable tiles "
                                           f"({cs['sign_agreement']:.0%} agree on its phase).",
                                 why="Interpolated colour samples follow their neighbours more closely than sensed ones. A single consistent phase across the "
                                     "frame is what un-resampled, camera-style (or RAW-converted) pixels look like.",
                                 caution="Does not identify a camera, and does not rule out later local edits that kept the grid.", evidence=["ev_cfa"], data=cs))
            else:
                F.append(Finding(id="cfa.pattern", title="Colour-filter-array (demosaicing) trace", kind="observation",
                                 value=cs["frac_significant"], unit="of tiles",
                                 statement=f"No consistent demosaicing pattern was found ({cs['frac_significant']:.0%} of assessable tiles; phase agreement {cs['sign_agreement']:.0%}).",
                                 why="Absence is expected after resizing, screenshots, strong compression or denoising, and for images that never passed through a colour "
                                     "filter array (rendered or generated images).", caution="Absence is NOT evidence of editing or generation by itself.",
                                 evidence=["ev_cfa"], data=cs))

        # ---- sharpening and edge sharpness
        es = edge_stats(gray)
        if not es["assessable"]:
            F.append(Finding(id="sharpening.halo", title="Sharpening halos", kind="unknown",
                             statement=f"Too few clean step edges ({es.get('n_edges', 0)}) to measure edge overshoot."))
        else:
            halo = es["halo_median"]
            F.append(Finding(id="sharpening.halo", title="Sharpening halos", kind="observation", value=round(halo * 100, 2), unit="% overshoot",
                             statement=f"Average edge overshoot is {halo:.1%} of the edge step (rise width {es['rise_width_px']:.1f} px, {es['n_edges']} edges).",
                             why="Phone and camera pipelines usually add an unsharp-mask-like halo (a few percent). Plain resizing or generation tends to give less.",
                             caution="JPEG ringing alone produces 1–3 % overshoot. Not validated as a sharpening detector: in tests it did not separate sharpened from unsharpened images, so no conclusion is drawn from it.", data=es))
            # No "sharpening applied" conclusion is drawn: on simulated unsharp-mask settings the average overshoot did not separate
            # sharpened from unsharpened images (see validation_results.json), so the number is reported as a measurement only.

        # ---- noise / denoising
        npf = noise_profile(gray)
        if not npf["assessable"]:
            F.append(Finding(id="noise.behaviour", title="Noise behaviour", kind="unknown",
                             statement="Noise could not be profiled: not enough flat, well-exposed blocks."))
        else:
            r = npf["variance_brightness_corr"]
            F.append(Finding(id="noise.level", title="Noise level", kind="observation", value=npf["sigma_median"], unit="grey levels (σ)",
                             statement=f"Noise in flat areas has σ ≈ {npf['sigma_median']:.2f} grey levels (90th percentile {npf['sigma_p90']:.2f}); "
                                       f"variance and brightness correlate at r = {r:+.2f} over {npf['n_blocks']} blocks.",
                             why="Sensor shot noise grows with brightness; denoised, rendered or generated pictures are often smoother or show no such relation.",
                             caution="Compression, resizing and tone-mapping change both numbers; ISO and scene are unknown.", data=npf))
            if npf["sigma_median"] < 0.45 and abs(r) < 0.15:
                F.append(Finding(id="noise.denoise", title="Noise suppressed", kind="hypothesis", confidence=0.3,
                                 statement="Flat areas are very smooth and show no brightness-dependent noise: consistent with strong denoising, heavy compression "
                                           "or an image that never had sensor noise.",
                                 caution="Does not distinguish denoising from synthetic origin or from low-ISO capture of a smooth scene."))

        # ---- lens
        ca = lateral_ca(rgb)
        if not ca["assessable"]:
            F.append(Finding(id="lens.ca", title="Lateral chromatic aberration", kind="unknown",
                             statement="Colour-channel misregistration could not be measured: too few edge-rich patches with a reliable registration."))
        elif ca["consistent"]:
            F.append(Finding(id="lens.ca", title="Lateral chromatic aberration", kind="supported", confidence=0.45,
                             value=max(abs(ca['red']['corner_shift_px']), abs(ca['blue']['corner_shift_px'])), unit="px at corner",
                             statement=f"Red and blue are shifted from green in a radial pattern (corner shift R {ca['red']['corner_shift_px']:+.2f} px, "
                                       f"B {ca['blue']['corner_shift_px']:+.2f} px), as an uncorrected lens produces.",
                             caution="Many phones correct this in software; a correction would leave none.", data=ca))
        else:
            F.append(Finding(id="lens.ca", title="Lateral chromatic aberration", kind="observation",
                             statement="No consistent radial colour-channel shift was found.",
                             why="Either the lens/ISP corrected it, the image was resampled or chroma-subsampled, or the image has no optics.",
                             caution="Not evidence for or against any origin.", data=ca))
        vf = luma_falloff(gray)
        if vf["assessable"]:
            F.append(Finding(id="lens.vignette", title="Brightness fall-off to the corners", kind="observation", value=vf["corner_to_centre"], unit="corner/centre",
                             statement=f"Corners are {vf['corner_to_centre']:.2f}× the centre brightness.",
                             caution="Scene content dominates this number; only meaningful against flat-field shots.", data=vf))

        # ---- JPEG quantisation
        js = jpeg_structure(fx.path)
        if js is None:
            F.append(Finding(id="jpeg.tables", title="JPEG quantisation", kind="unknown",
                             statement="Not a JPEG file, so there are no quantisation tables to examine."))
        else:
            std = js["tables_standard"]
            F.append(Finding(id="jpeg.tables", title="JPEG quantisation", kind="observation", value=js.get("luma_quality"), unit="libjpeg quality",
                             statement=(f"Quantisation tables are the standard libjpeg tables at quality {js.get('luma_quality')}" if std else
                                        f"Quantisation tables are not the standard libjpeg tables (nearest quality {js.get('luma_quality')})")
                                       + f"; chroma {js.get('subsampling')}, {'progressive' if js['progressive'] else 'baseline'}.",
                             why="Software built on libjpeg (many editors, converters, platforms, Pillow) writes the standard tables; many camera and phone "
                                 "encoders, and some editors, write their own.",
                             caution="Only the LAST encoder is visible. No table library for specific devices is bundled, so a non-standard table is not a device match.",
                             data={k: v for k, v in js.items() if k != "qtable_luma"}))
            fx.shared["jpeg_structure"] = js

        # ---- overall profile (a hypothesis, deliberately weak)
        cues_for = [bool(cs.get("present")), bool(es.get("assessable") and es.get("halo_median", 0) >= 0.03),
                    bool(npf.get("assessable") and npf.get("variance_brightness_corr", 0) > 0.25), bool(ca.get("consistent"))]
        measurable = sum(1 for d in (cs, es, npf, ca) if d.get("assessable"))
        if measurable >= 2:
            n_for = sum(cues_for)
            if cs.get("present") and n_for >= 2:
                F.append(Finding(id="profile.camera_like", title="Overall processing profile", kind="hypothesis", confidence=0.4,
                                 statement=f"{n_for} of 4 measured traces are present, including a demosaicing pattern: the pixels are consistent with a "
                                           "camera-style processing chain that has not been resampled since.",
                                 caution="Consistency is not identification. Other pipelines produce the same traces, and edits that do not resample keep them."))
            elif n_for == 0:
                F.append(Finding(id="profile.camera_like", title="Overall processing profile", kind="hypothesis", confidence=0.25,
                                 statement="None of the four camera-processing traces could be found. This is what resized, re-encoded, screenshotted, rendered "
                                           "or generated pixels look like — and also what a social-media copy of a real photo looks like.",
                                 caution="This does NOT mean the image is fake. Treat it as: origin cannot be supported from the pixels."))
            else:
                F.append(Finding(id="profile.camera_like", title="Overall processing profile", kind="unknown",
                                 statement="The traces are mixed; the pixel data neither supports nor undermines a camera-style processing chain."))
        else:
            F.append(Finding(id="profile.camera_like", title="Overall processing profile", kind="unknown",
                             statement="Too few traces could be measured to describe the processing chain."))
        res.summary = next((f.statement for f in F if f.id == "profile.camera_like"), "")
        res.status = "ok"
        return res
