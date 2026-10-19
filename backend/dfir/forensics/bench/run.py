"""Benchmark harness: measures every forensic module against ground truth and writes ``validation_results.json``.

Two kinds of data are used, and the output says which is which:

  * SIMULATED data with known truth (sensors with known fixed-pattern noise, known tampering, known rolling-shutter read-out,
    known hum / reverberation / clipping / audio-video offset). Shows each method works as designed.
  * REAL files with presumed-clean status (web photographs from the calibration set) used ONLY to measure how often the
    detectors fire on pictures nobody tampered with for this test (false-positive rate). Those photographs are not
    guaranteed unedited, so this is an upper bound on the true false-positive rate. No real labelled camera set (device
    ground truth) was available, so PRNU is NOT validated on real cameras.

Run:  uv run python ../scripts/forensics_bench.py [--quick] [--real-dir DIR] [--only module_id,...]
"""
from __future__ import annotations

import io
import json
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from .. import prnu
from ..base import FCtx, VALIDATION_FILE, tool_versions, wilson
from . import synth


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "rate": round(k / n, 4) if n else None, "ci95": [round(lo, 4), round(hi, 4)]}


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    p, n = np.asarray(pos), np.asarray(neg)
    return round(float((p[:, None] > n[None, :]).mean() + 0.5 * (p[:, None] == n[None, :]).mean()), 4)


def _log(msg: str) -> None:
    print(f"[bench {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _write(arr_or_bytes, d: Path, name: str, jpeg_q: int = 92) -> Path:
    p = d / name
    if isinstance(arr_or_bytes, bytes):
        p.write_bytes(arr_or_bytes)
    else:
        p.write_bytes(synth.to_jpeg(arr_or_bytes, jpeg_q))
    return p


def run_module(mid: str, path: Path, modality: str = "image", **kw):
    from .. import registry
    return registry.get(mid).execute(FCtx(path=path, modality=modality, media={"filename": path.name}, **kw))


# ================================================================================================ PRNU
def bench_prnu(quick: bool = False) -> dict:
    D, R, T, S = (3, 6, 3, 384) if quick else (8, 10, 6, 512)
    conds = {k: synth.TRANSFORMS[k] for k in (["none", "jpeg_q85", "resize_0.5"] if quick else list(synth.TRANSFORMS))}
    sens = [synth.Sensor(seed=i, h=S, w=S) for i in range(D)]
    fps = []
    for d, s in enumerate(sens):
        refs = [prnu.load_gray(_tmp_jpeg(s.capture(synth.scene(1000 * d + i, S, S), 7000 + 31 * d + i, jpeg_q=95))) for i in range(R)]
        fps.append(prnu._crop(prnu.estimate_fingerprint([prnu._crop(g) for g in refs])))
        _log(f"PRNU fingerprint {d + 1}/{D}")
    out = {}
    pooled_imp: list[float] = []
    for cname, tf in conds.items():
        gen, imp, not_cmp = [], [], 0
        for d, s in enumerate(sens):
            for t in range(T):
                a = tf(s.capture(synth.scene(50_000 + 97 * d + t, S, S), 90_000 + 13 * d + t, jpeg_q=None))
                if cname.startswith("resize") and a.shape[0] != S:  # oracle: the analyst upsamples back to the reference size
                    a = cv2.resize(a, (S, S), interpolation=cv2.INTER_CUBIC)
                g = _gray(a)
                if g.shape != (S, S):
                    not_cmp += 1
                    continue
                w = prnu.noise_residual(prnu._crop(g))
                tcrop = prnu._crop(g)
                for e, k in enumerate(fps):
                    pce = prnu._pce(w, k * tcrop)[0]
                    (gen if e == d else imp).append(float(pce))
        out[cname] = {"genuine_trials": len(gen), "impostor_trials": len(imp), "not_comparable": not_cmp,
                      "tpr_at_60": rate(sum(p >= prnu.PCE_MATCH for p in gen), len(gen)) if gen else None,
                      "false_match_at_60": rate(sum(p >= prnu.PCE_MATCH for p in imp), len(imp)) if imp else None,
                      "median_pce_genuine": round(float(np.median(gen)), 1) if gen else None,
                      "median_pce_impostor": round(float(np.median(imp)), 1) if imp else None, "auc": auc(gen, imp)}
        pooled_imp += imp
        _log(f"PRNU {cname}: tpr={out[cname]['tpr_at_60'] and out[cname]['tpr_at_60']['rate']} comparable={len(gen)}")
    fm = sum(p >= prnu.PCE_MATCH for p in pooled_imp)
    return {"devices": D, "refs_per_device": R, "tests_per_device": T, "image_px": S, "conditions": out,
            "false_match_at_threshold": {"threshold": prnu.PCE_MATCH, "trials": len(pooled_imp), "false_matches": int(fm), "upper_95": wilson(fm, len(pooled_imp))[1]}}


def _tmp_jpeg(b: bytes) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    f.write(b)
    f.close()
    return Path(f.name)


def _gray(a: np.ndarray) -> np.ndarray:
    return (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]).astype(np.float32)


# ================================================================================================ camera pipeline
def bench_camera(quick: bool = False, tmp: Path | None = None) -> dict:
    n = 6 if quick else 24
    tmp = tmp or Path(tempfile.mkdtemp())
    res: dict = {}
    # CFA present vs absent
    def cfa_flag(arr, q=None):
        p = _write(arr if q is None else synth.to_jpeg(arr, q), tmp, "c.jpg" if q else "c.png") if q else None
        from ..modules import camera as C
        s = C.cfa_summary(C.cfa_tiles(arr if q is None else synth.decode(synth.to_jpeg(arr, q))))
        return bool(s.get("present"))
    pos = {}
    for q in (None, 98, 95, 90, 85, 75):
        k = 0
        for i in range(n):
            a = synth.Sensor(seed=300 + i).capture(synth.scene(2000 + i), i, jpeg_q=None)
            k += cfa_flag(a, q)
        pos[f"sensor_jpeg_{q or 'none'}"] = rate(k, n)
    neg = {}
    for name, fn in {"no_cfa_generated_like": lambda i: synth.Sensor(seed=400 + i, cfa=False).capture(synth.scene(3000 + i), i, jpeg_q=None),
                     "sensor_resized_0.9": lambda i: synth.t_resize(0.9)(synth.Sensor(seed=410 + i).capture(synth.scene(3100 + i), i, jpeg_q=None)),
                     "sensor_resized_1.2": lambda i: synth.t_resize(1.2)(synth.Sensor(seed=420 + i).capture(synth.scene(3200 + i), i, jpeg_q=None)),
                     "sensor_resized_0.5": lambda i: synth.t_resize(0.5)(synth.Sensor(seed=430 + i).capture(synth.scene(3300 + i), i, jpeg_q=None))}.items():
        neg[name] = rate(sum(cfa_flag(fn(i)) for i in range(n)), n)
    res["cfa"] = {"present_when_demosaiced": pos, "present_when_not_demosaiced": neg,
                  "note": "Detection survives JPEG down to roughly quality 90-95 on simulated bilinear demosaicing; it is erased by any resizing."}
    # sharpening
    from ..modules import camera as C
    sharp = {}
    for amt in (0.0, 1.5, 3.0):
        halos = []
        for i in range(n):
            a = synth.Sensor(seed=500 + i, sharpen=amt).capture(synth.scene(4000 + i), i, jpeg_q=None)
            es = C.edge_stats(_gray(a))
            halos.append(es.get("halo_median", 0.0) if es.get("assessable") else None)
        ok = [h for h in halos if h is not None]
        sharp[f"unsharp_amount_{amt}"] = {"median_halo": round(float(np.median(ok)), 4) if ok else None, "flagged_at_0.04": rate(sum(h >= 0.04 for h in ok), len(ok)), "assessable": len(ok)}
    res["sharpening"] = sharp
    # denoising hypothesis
    den = {}
    for name, kw in {"normal_sensor": {}, "denoised_sigma_1.2": {"denoise": 1.2}, "no_sensor_noise": {"shot_gain": 0.0, "read_noise": 0.0}}.items():
        flag = 0
        for i in range(n):
            a = synth.Sensor(seed=600 + i, **kw).capture(synth.scene(5000 + i), i, jpeg_q=None)
            p = C.noise_profile(_gray(a))
            flag += bool(p.get("assessable") and p["sigma_median"] < 0.45 and abs(p["variance_brightness_corr"]) < 0.15)
        den[name] = rate(flag, n)
    res["denoising_hypothesis_flagged"] = den
    # lateral chromatic aberration: known radial magnification of red and blue
    ca = []
    for i in range(max(4, n // 2)):
        base = synth.Sensor(seed=700 + i, cfa=False).capture(synth.scene(6000 + i, 512, 512), i, jpeg_q=None)
        for alpha in (0.0, 0.0015, 0.003):
            a = base.copy()
            h, w = a.shape[:2]
            for ch in (0, 2):
                M = cv2.getRotationMatrix2D((w / 2, h / 2), 0, 1 + (alpha if ch == 0 else -alpha))
                a[..., ch] = cv2.warpAffine(base[..., ch], M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            r = C.lateral_ca(a)
            true_corner = alpha * float(np.hypot(w / 2, h / 2))
            ca.append({"alpha": alpha, "true_corner_px": round(true_corner, 3), "assessable": r["assessable"],
                       "est_red": r.get("red", {}).get("corner_shift_px"), "consistent": r.get("consistent", False)})
    by = {}
    for a in (0.0, 0.0015, 0.003):
        rows = [c for c in ca if c["alpha"] == a]
        ests = [abs(c["est_red"]) for c in rows if c["est_red"] is not None]
        by[str(a)] = {"true_corner_px": rows[0]["true_corner_px"], "flagged_consistent": rate(sum(c["consistent"] for c in rows), len(rows)),
                      "median_abs_estimate_px": round(float(np.median(ests)), 3) if ests else None}
    res["lateral_ca"] = by
    return res


# ================================================================================================ editing
def _flags(path: Path) -> dict:
    r = run_module("editing", path)
    f = {x.id: x for x in r.findings}
    return {"localisation": bool(f.get("localisation") and f["localisation"].kind == "hypothesis"),
            "resampling": "resampling.global" in f, "double_jpeg": "jpeg.double" in f and f["jpeg.double"].kind == "supported",
            "grid_shift": "jpeg.grid" in f and f["jpeg.grid"].kind == "supported", "copy_move": "copy_move" in f and f["copy_move"].kind == "hypothesis",
            "regions": (f["localisation"].data.get("regions") if f.get("localisation") else []) or []}


def _iou_regions(regions, mask: np.ndarray, tile: int = 64) -> float:
    pm = np.zeros(mask.shape, bool)
    for r in regions:
        x0, y0, x1, y1 = (v * tile for v in r["tile_box"])
        pm[y0:y1, x0:x1] = True
    return float((pm & mask).sum() / max(1, (pm | mask).sum()))


def bench_editing(quick: bool = False, tmp: Path | None = None) -> dict:
    n = 6 if quick else 24
    tmp = tmp or Path(tempfile.mkdtemp())
    rng = np.random.default_rng(5)
    res: dict = {}
    ctrl = {"localisation": 0, "resampling": 0, "double_jpeg": 0, "grid_shift": 0, "copy_move": 0}
    for q in (92, 75):
        for i in range(n):
            b = synth.Sensor(seed=800 + i).capture(synth.scene(7000 + i), i, jpeg_q=q)
            f = _flags(_write(b, tmp, "u.jpg"))
            for k in ctrl:
                ctrl[k] += f[k]
    res["untouched_single_jpeg"] = {k: rate(v, 2 * n) for k, v in ctrl.items()}
    _log("editing: untouched done")
    # tampering with known masks
    tamper: dict[str, dict] = {}
    for kind in ("splice_other_camera_raw", "splice_rescaled_1.3", "splice_double_jpeg_history"):
        hits, ious = 0, []
        for i in range(n):
            s, s2 = synth.Sensor(seed=900 + i), synth.Sensor(seed=950 + i)
            base = s.capture(synth.scene(8000 + i), i, jpeg_q=None)
            donor = s2.capture(synth.scene(8500 + i), i + 99, jpeg_q=None)
            x, y = int(rng.integers(40, 330)), int(rng.integers(40, 330))
            if kind == "splice_double_jpeg_history":
                out, m = synth.splice(synth.decode(synth.to_jpeg(base, 75)), synth.decode(synth.to_jpeg(donor, 95)), (x, y, 128, 128))
            else:
                out, m = synth.splice(base, donor, (x, y, 128, 128), rescale=1.3 if "rescaled" in kind else None)
            f = _flags(_write(out, tmp, "t.jpg", 92))
            iou = _iou_regions(f["regions"], m)
            hits += bool(f["localisation"] and iou > 0.1)
            ious.append(iou)
        tamper[kind] = {"localised": rate(hits, n), "mean_iou": round(float(np.mean(ious)), 3)}
        _log(f"editing: {kind} {tamper[kind]['localised']['rate']}")
    # copy-move
    hits = 0
    for i in range(n):
        base = synth.Sensor(seed=1000 + i).capture(synth.scene(9000 + i), i, jpeg_q=None)
        out, m = synth.copy_move(base, (60, 60, 112, 112), (300, 280))
        f = _flags(_write(out, tmp, "cm.jpg", 92))
        hits += f["copy_move"]
    tamper["copy_move_plain"] = {"detected": rate(hits, n)}
    res["tampering"] = tamper
    # global resampling
    rs = {}
    for name, fac in (("none", None), ("enlarged_1.2", 1.2), ("enlarged_1.5", 1.5), ("reduced_0.8", 0.8), ("enlarged_2.0", 2.0)):
        k = 0
        for i in range(n):
            a = synth.Sensor(seed=1100 + i, h=640, w=640).capture(synth.scene(9500 + i, 640, 640), i, jpeg_q=None)
            if fac:
                a = cv2.resize(a, None, fx=fac, fy=fac, interpolation=cv2.INTER_CUBIC if fac > 1 else cv2.INTER_AREA)
            k += _flags(_write(a, tmp, "rs.jpg", 92))["resampling"]
        rs[name] = rate(k, n)
    res["global_resampling_flagged"] = rs
    # double JPEG
    dj = {}
    for name, (q1, q2) in {"single_q92": (None, 92), "q75_then_q92": (75, 92), "q85_then_q92": (85, 92), "q95_then_q75": (95, 75), "q90_then_q90": (90, 90)}.items():
        k = 0
        for i in range(n):
            a = synth.Sensor(seed=1200 + i).capture(synth.scene(9800 + i), i, jpeg_q=None)
            if q1:
                a = synth.decode(synth.to_jpeg(a, q1))
            k += _flags(_write(a, tmp, "dj.jpg", q2))["double_jpeg"]
        dj[name] = rate(k, n)
    res["double_jpeg_flagged"] = dj
    return res


def bench_real_false_positives(real_dir: Path, n_each: int, tmp: Path | None = None) -> dict | None:
    """How often the editing and camera modules fire on REAL web photographs (and on AI-generated images)."""
    if not real_dir.exists():
        return None
    out = {}
    for label in ("real", "fake"):
        files = sorted(real_dir.glob(f"{label}_*.jpg"))[:n_each]
        if not files:
            continue
        agg = {"localisation": 0, "resampling": 0, "double_jpeg": 0, "grid_shift": 0, "copy_move": 0}
        cfa = 0
        t0 = time.time()
        for f in files:
            fl = _flags(f)
            for k in agg:
                agg[k] += fl[k]
            r = run_module("camera_pipeline", f)
            cp = r.finding("cfa.pattern")
            cfa += bool(cp and cp.kind == "supported")
        out[label] = {"n": len(files), "source": "OpenFake/LAION-style web photographs" if label == "real" else "AI-generated (OpenFake)",
                      **{f"{k}_flagged": rate(v, len(files)) for k, v in agg.items()}, "demosaicing_pattern_found": rate(cfa, len(files)),
                      "seconds_per_image": round((time.time() - t0) / len(files), 2)}
        _log(f"real-data {label}: {out[label]}")
    return out


# ================================================================================================ video
def bench_video(quick: bool = False, tmp: Path | None = None) -> dict:
    from ..modules import video as V
    from ..modules._av import decode_gray, frame_table
    tmp = tmp or Path(tempfile.mkdtemp())
    seeds = (1, 2) if quick else (1, 2, 3, 4)
    res: dict = {}
    rows = {}
    for ro in ((0.0, 0.6) if quick else (0.0, 0.3, 0.6, 0.9)):
        est, sig = [], 0
        for sd in seeds:
            p = tmp / f"rs_{ro}_{sd}.mp4"
            synth.make_video(p, seconds=5, readout=ro, seed=sd)
            fr = decode_gray(p, 320, 240)
            r = V.rolling_shutter(fr, V.global_motion(fr))
            if r.get("assessable"):
                est.append(r["readout_fraction"])
                sig += bool(r["p"] < 0.01 and abs(r["r"]) >= 0.4 and abs(r["readout_fraction"]) >= 0.1)
        rows[str(ro)] = {"true_readout": ro, "estimates": est, "flagged": rate(sig, len(seeds)),
                         "mean_estimate": round(float(np.mean(est)), 3) if est else None,
                         "ratio_estimate_to_truth": round(float(np.mean(est)) / ro, 2) if est and ro else None}
        _log(f"video rolling shutter {ro}: {rows[str(ro)]}")
    res["rolling_shutter"] = rows
    # timing
    p = tmp / "timing.mp4"
    gt = synth.make_video(p, seconds=6, readout=0.3, seed=9, drop=[20, 21, 60], duplicate=[35, 100])
    ts = V.timing_stats(np.array([t["t"] for t in frame_table(p)]))
    dup = V.duplicate_frames(decode_gray(p, 320, 240))
    res["frame_timing"] = {"dropped_groups_true": 2, "gaps_found": ts.get("n_gaps"), "gap_multiples": ts.get("gap_multiples"),
                           "duplicates_true": 2, "duplicates_found": dup.get("n_duplicates"), "constant_rate_control": None}
    p0 = tmp / "clean.mp4"
    synth.make_video(p0, seconds=6, readout=0.3, seed=10)
    t0 = V.timing_stats(np.array([t["t"] for t in frame_table(p0)]))
    d0 = V.duplicate_frames(decode_gray(p0, 320, 240))
    res["frame_timing"].update(constant_rate_control={"gaps_found": t0.get("n_gaps"), "duplicates_found": d0.get("n_duplicates")})
    # codec traces: software encoder detected / hardware-like (mpeg4) not
    p1 = tmp / "mp4v.mp4"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(p0), "-c:v", "mpeg4", "-q:v", "5", "-an", str(p1)], check=True)
    res["codec_traces"] = {"x264_file_flagged": bool(V.encoder_traces(p0) and any("x264" in t["label"] for t in V.encoder_traces(p0))),
                           "mpeg4_encoded_file_flagged_x264": bool(any("x264" in t["label"] for t in V.encoder_traces(p1)))}
    # static vs moving camera
    st = tmp / "static.mp4"
    synth.make_video(st, seconds=4, readout=0.0, amplitude=0.0, seed=11)
    res["stabilisation"] = {"static_camera_detected_as_static": bool(V.stabilisation(V.global_motion(decode_gray(st, 320, 240)), 25).get("static")),
                            "panning_camera_detected_as_static": bool(V.stabilisation(V.global_motion(decode_gray(p0, 320, 240)), 25).get("static"))}
    return res


# ================================================================================================ audio
def bench_audio(quick: bool = False) -> dict:
    from ..modules import audio as A
    sr = 22050
    n = 4 if quick else 12
    res: dict = {}
    hum = {}
    for f0 in (50.0, 60.0):
        for lvl in (-60.0, -50.0, -42.0):
            ok = ident = 0
            for i in range(n):
                x = synth.add_hum(synth.add_noise_floor(synth.speech_like(14, sr, 100 + i), sr, -58, seed=i), sr, f0, lvl)
                r = A.hum_analysis(x, sr)
                ok += bool(r.get("present"))
                ident += bool(r.get("present") and r["f0"] == f0)
            hum[f"{f0:.0f}Hz_at_{lvl:.0f}dB"] = {"detected": rate(ok, n), "correct_frequency": rate(ident, n)}
    clean = sum(bool(A.hum_analysis(synth.add_noise_floor(synth.speech_like(14, sr, 200 + i), sr, -58, seed=i), sr).get("present")) for i in range(n * 2))
    res["hum"] = {"by_condition": hum, "false_alarm_on_clean": rate(clean, n * 2)}
    rv = {}
    for rt in (0.3, 0.5, 0.9):
        ests = []
        for i in range(n):
            x = synth.add_noise_floor(synth.add_reverb(synth.speech_like(20, sr, 300 + i), sr, rt, seed=i), sr, -60, seed=i)
            r = A.rt60_estimate(x, sr)
            if r.get("assessable"):
                ests.append(r["rt60_s"])
        rv[str(rt)] = {"true_rt60_s": rt, "assessable": rate(len(ests), n), "median_estimate_s": round(float(np.median(ests)), 2) if ests else None,
                       "median_abs_error_s": round(float(np.median(np.abs(np.array(ests) - rt))), 2) if ests else None}
    res["reverberation"] = rv
    cl = {}
    for gain in (1.0, 2.0, 3.0, 5.0):
        k = 0
        for i in range(n):
            x = synth.clip(synth.speech_like(8, sr, 400 + i) * gain * 1.0, 1.0)
            k += bool(A.clipping(x).get("clipped"))
        cl[f"gain_{gain}"] = rate(k, n)
    res["clipping_flagged"] = cl
    nf_pos = nf_neg = 0
    for i in range(n):
        a = synth.add_noise_floor(synth.speech_like(12, sr, 500 + i), sr, -55, seed=i, tilt=1.0)
        b = synth.add_noise_floor(synth.speech_like(12, sr, 600 + i), sr, -42, seed=i + 50, tilt=0.1)
        r1 = A.noise_floor(np.concatenate([a, a[::-1].copy() if False else synth.add_noise_floor(synth.speech_like(12, sr, 700 + i), sr, -55, seed=i, tilt=1.0), a]), sr)
        r2 = A.noise_floor(np.concatenate([a, b, b]), sr)
        nf_neg += bool(r1.get("first_vs_last_third_db") and r1["first_vs_last_third_db"] > 6)
        nf_pos += bool(r2.get("first_vs_last_third_db") and r2["first_vs_last_third_db"] > 6)
    res["noise_floor_inconsistency"] = {"flagged_when_two_recordings_joined": rate(nf_pos, n), "flagged_when_same_conditions": rate(nf_neg, n)}
    # audio/video sync estimator with known offsets (series level)
    sy = {}
    rng = np.random.default_rng(1)
    fps, N = 20, 600
    for lag in (-4, 0, 3, 6):
        errs, ok = [], 0
        for i in range(n):
            env = np.convolve(rng.standard_normal(N), np.hanning(5), "same")
            mouth = np.roll(env, lag) + 0.8 * rng.standard_normal(N)
            r = A.av_sync(mouth, env, fps)
            errs.append(abs(r["lag_ms"] - lag * 50))
            ok += bool(r["p"] < 0.01)
        sy[f"true_lag_{lag * 50}ms"] = {"median_abs_error_ms": float(np.median(errs)), "significant": rate(ok, n)}
    null = sum(A.av_sync(rng.standard_normal(N), np.convolve(rng.standard_normal(N), np.hanning(5), "same"), fps)["p"] < 0.01 for _ in range(n * 3))
    sy["unrelated_signals_flagged"] = rate(null, n * 3)
    res["av_sync_estimator"] = sy
    return res


# ================================================================================================ driver
def _tier_summary(mid: str, data: dict, summary: str, dataset: str, tier: str = "synthetic") -> dict:
    return {"tier": tier, "summary": summary, "dataset": dataset, "metrics": data}


def run_all(quick: bool = False, real_dir: Path | None = None, real_n: int = 120, out: Path | None = None,
            only: set[str] | None = None) -> dict:
    t0 = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="dfir_bench_"))
    want = lambda k: only is None or k in only  # noqa: E731
    mods: dict = {}
    if want("prnu"):
        d = bench_prnu(quick)
        mods["prnu"] = _tier_summary("prnu", d, "Validated on simulated sensors with known fixed-pattern noise. NOT validated on real cameras: no labelled real-camera set was available.",
                                     f"{d['devices']} simulated devices x {d['refs_per_device']} reference images, {d['tests_per_device']} test images per device, {d['image_px']} px, 13 transformations")
    if want("camera_pipeline"):
        d = bench_camera(quick, tmp)
        mods["camera_pipeline"] = _tier_summary("camera_pipeline", d, "Validated on simulated sensors (bilinear demosaicing, known sharpening / denoising / chromatic aberration). Real camera pipelines (proprietary demosaicing, HDR, AI denoising) are not covered.",
                                                "Simulated Bayer sensors with known processing")
    if want("editing"):
        d = bench_editing(quick, tmp)
        mods["editing"] = _tier_summary("editing", d, "Validated on simulated splices, copy-move, resizing and double compression, plus a false-positive measurement on real web photographs.",
                                        "Simulated tampering with known masks")
    if want("video_acquisition"):
        d = bench_video(quick, tmp)
        mods["video_acquisition"] = _tier_summary("video_acquisition", d, "Validated on simulated panning video with known rolling-shutter read-out, frame drops/duplicates and encoders. No real camera footage was measured.",
                                                  "Synthetic 320x240 pans encoded with libx264")
    if want("audio_forensics"):
        d = bench_audio(quick)
        mods["audio_forensics"] = _tier_summary("audio_forensics", d, "Validated on simulated speech with known hum, reverberation, clipping, noise floors and audio/video offsets. No real recordings were measured.",
                                                "Synthetic speech-like signals at 22.05 kHz")
    mods["provenance"] = {"tier": "none", "summary": "Parsing of EXIF/C2PA/watermark data is deterministic and covered by unit tests; no statistical method is involved. Attribution tiers follow reasoning/generator.py.",
                          "dataset": "n/a", "metrics": {}}
    real = None
    if real_dir and (want("editing") or want("camera_pipeline")):
        real = bench_real_false_positives(real_dir, real_n, tmp)
        if real:
            for mid in ("editing", "camera_pipeline"):
                if mid in mods:
                    mods[mid]["metrics"]["real_photo_false_positives"] = real
                    mods[mid]["real_data_fp_measured"] = True
    result = {"generated_at": datetime.now(timezone.utc).isoformat(), "quick": quick, "seconds": round(time.time() - t0, 1),
              "platform": f"{platform.system()} {platform.release()} / Python {platform.python_version()}",
              "versions": tool_versions("numpy", "scipy", "cv2", "pywt", "PIL", "ffmpeg"),
              "caveat": ("All 'synthetic' results come from simulated sensors, edits, video and audio with known truth. They show the methods work as "
                         "designed; they do not show how accurate they are on real cameras, real editors or real platforms. The real-photo false-positive rates "
                         "use web photographs that are not guaranteed unedited."),
              "modules": mods}
    if only is not None and (out or VALIDATION_FILE).exists():  # merge partial runs into the existing file
        try:
            old = json.loads((out or VALIDATION_FILE).read_text(encoding="utf-8"))
            old["modules"].update(mods)
            old.update({k: v for k, v in result.items() if k != "modules"})
            result = old
        except (OSError, json.JSONDecodeError):
            pass
    (out or VALIDATION_FILE).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true", help="tiny run (seconds) for smoke tests; does not overwrite the shipped results unless --out is omitted")
    ap.add_argument("--real-dir", type=Path, help="folder with real_*.jpg / fake_*.jpg for the false-positive measurement")
    ap.add_argument("--real-n", type=int, default=120)
    ap.add_argument("--only", help="comma-separated module ids to (re)run, merged into the existing results")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    r = run_all(a.quick, a.real_dir, a.real_n, a.out, set(a.only.split(",")) if a.only else None)
    print(json.dumps({k: v for k, v in r.items() if k != "modules"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
