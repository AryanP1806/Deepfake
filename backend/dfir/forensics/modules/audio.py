"""Module 5 — audio forensics: mains hum, reverberation, microphone noise floor, clipping and audio/video synchronisation.

These describe the RECORDING ENVIRONMENT and chain. They can show that parts of a recording were made in different
conditions, that a track was clipped or band-limited, or that sound and mouth movement are out of step. They cannot say whether
a voice is real, and no learned voice-clone detector is bundled (optional plugin slot only).
"""
from __future__ import annotations

import numpy as np
from scipy import signal as sps

from ...analyzers.media_io import ffmpeg_bin
from ..base import Evidence, FCtx, Finding, ForensicModule, ModuleResult, Requirement
from ..registry import register
from ._av import audio_stream_info, decode_audio_native, decode_rgb_stream


# ------------------------------------------------------------------------------------------------ helpers
def frame_rms_db(x: np.ndarray, sr: int, hop_s: float = 0.01) -> np.ndarray:
    hop = max(1, int(hop_s * sr))
    n = len(x) // hop
    fr = x[: n * hop].reshape(n, hop)
    return 10 * np.log10(np.mean(fr.astype(np.float64) ** 2, axis=1) + 1e-12)


# ------------------------------------------------------------------------------------------------ hum
def _band_prom_db(f: np.ndarray, P: np.ndarray, fk: float) -> float:
    peak = P[(f >= fk - 1.0) & (f <= fk + 1.0)]
    ref = P[((f >= fk - 8) & (f <= fk - 2.5)) | ((f >= fk + 2.5) & (f <= fk + 8))]
    if len(peak) == 0 or len(ref) < 4:
        return 0.0
    return float(10 * np.log10(peak.max() / (np.median(ref) + 1e-30) + 1e-12))


def hum_analysis(x: np.ndarray, sr: int) -> dict:
    """Mains hum at 50 or 60 Hz and harmonics, measured on the quietest part of the recording (speech would mask it)."""
    if len(x) < sr * 3:
        return {"assessable": False, "reason": "under 3 s of audio"}
    win = sr  # 1 s windows
    n = len(x) // win
    rms = np.array([np.sqrt(np.mean(x[i * win:(i + 1) * win] ** 2)) for i in range(n)])
    quiet = np.argsort(rms)[: max(2, int(n * 0.4))]
    nper = min(2 ** 15, win)
    Ps = []
    for i in quiet:
        f, P = sps.welch(x[i * win:(i + 1) * win], sr, nperseg=nper)
        Ps.append(P)
    P = np.mean(Ps, axis=0)
    best = None
    for f0 in (50.0, 60.0):
        proms = []
        for k in range(1, 6):
            fk = f0 * k
            if fk > sr / 2 - 20:
                break
            proms.append(_band_prom_db(f, P, fk))
        score = proms[0] + 0.5 * sum(max(0.0, p) for p in proms[1:])
        if best is None or score > best["score"]:
            best = {"f0": f0, "prominence_db": [round(p, 1) for p in proms], "score": round(score, 1)}
    pr = best["prominence_db"]  # the fundamental must show too: speech harmonics alone can land on 100/150/250 Hz
    present = pr[0] >= 12 or (pr[0] >= 6 and sum(p >= 8 for p in pr[1:]) >= 2)
    # stability over time: prominence of the fundamental in 2 s windows
    steps = []
    w2 = 2 * sr
    series = []
    for i in range(0, len(x) - w2 + 1, w2):
        ff, PP = sps.welch(x[i:i + w2], sr, nperseg=min(2 ** 15, w2))
        series.append(_band_prom_db(ff, PP, best["f0"]))
    if present and len(series) >= 4:
        arr = np.array(series)
        steps = [int(i) for i in np.nonzero(np.abs(np.diff(arr)) > 15)[0]]
    return {"assessable": True, "present": bool(present), **best, "windows": len(series), "abrupt_changes_at_window": steps,
            "quiet_windows_used": int(len(quiet))}


# ------------------------------------------------------------------------------------------------ reverberation
def rt60_estimate(x: np.ndarray, sr: int) -> dict:
    """Blind estimate from free decays after sound offsets: the slope of the energy envelope between -5 and -25 dB below the
    offset's peak, extrapolated to -60 dB. The room can only slow a decay, never speed it, so the slower decays are taken."""
    e = frame_rms_db(x, sr, 0.01)
    if len(e) < 200:
        return {"assessable": False, "reason": "under 2 s of audio"}
    floor = np.percentile(e, 5)
    est = []
    i = 2
    while i < len(e) - 12:
        if e[i] > e[i - 1] and e[i] >= e[i + 1] and e[i] - floor > 30:
            peak = e[i]
            j = i
            while j + 1 < len(e) and e[j + 1] <= e[j] + 1.5 and e[j] > peak - 28:
                j += 1
            seg = e[i:j + 1]
            t = np.arange(len(seg)) * 0.01
            sel = (seg <= peak - 5) & (seg >= peak - 25)
            if sel.sum() >= 8 and seg[-1] <= peak - 22:
                sl, ic, r, _, _ = __import__("scipy").stats.linregress(t[sel], seg[sel])
                if sl < -8 and r * r > 0.9:
                    est.append(-60.0 / sl)
            i = max(j, i) + 1
        else:
            i += 1
    if len(est) < 4:
        return {"assessable": False, "reason": f"only {len(est)} clean decay(s) found (≥4 needed)", "n_decays": len(est)}
    est = np.array(est)
    return {"assessable": True, "n_decays": int(len(est)), "rt60_s": round(float(np.percentile(est, 75)), 2), "min_s": round(float(est.min()), 2),
            "max_s": round(float(est.max()), 2), "spread": round(float(np.std(est) / np.mean(est)), 2)}


# ------------------------------------------------------------------------------------------------ noise floor
THIRD_OCT = [100, 160, 250, 400, 630, 1000, 1600, 2500, 4000, 6300]


def _floor_spectrum(x: np.ndarray, sr: int, frames_idx: np.ndarray, hop: int) -> np.ndarray:
    seg = np.concatenate([x[i * hop:(i + 1) * hop] for i in frames_idx])
    f, P = sps.welch(seg, sr, nperseg=min(1024, len(seg)))
    out = []
    for c in THIRD_OCT:
        if c * 1.12 > sr / 2:
            break
        band = (f >= c / 1.12) & (f <= c * 1.12)
        out.append(10 * np.log10(P[band].mean() + 1e-20) if band.any() else -200.0)
    return np.array(out)


def noise_floor(x: np.ndarray, sr: int) -> dict:
    hop = int(0.03 * sr)
    n = len(x) // hop
    if n < 100:
        return {"assessable": False, "reason": "under 3 s of audio"}
    fr = x[: n * hop].reshape(n, hop)
    rms = np.sqrt((fr.astype(np.float64) ** 2).mean(1))
    digital_silence = float((rms < 1e-5).mean())
    live = np.nonzero(rms >= 1e-5)[0]
    if len(live) < 60:
        return {"assessable": False, "reason": "mostly digital silence", "digital_silence_fraction": round(digital_silence, 3)}
    lo = live[np.argsort(rms[live])[: max(15, int(len(live) * 0.12))]]
    level = 20 * np.log10(rms[lo].mean() + 1e-12)
    spec = _floor_spectrum(x, sr, lo, hop)
    oct_slope = float(np.polyfit(np.log2(THIRD_OCT[: len(spec)]), spec, 1)[0])
    # consistency between the first and last third of the recording
    thirds = np.array_split(live, 3)
    segs = []
    for tchunk in (thirds[0], thirds[-1]):
        if len(tchunk) >= 60:
            q = tchunk[np.argsort(rms[tchunk])[: max(10, int(len(tchunk) * 0.15))]]
            segs.append(_floor_spectrum(x, sr, q, hop))
    lsd = float(np.sqrt(np.mean((segs[0] - segs[1]) ** 2))) if len(segs) == 2 and len(segs[0]) == len(segs[1]) else None
    return {"assessable": True, "level_dbfs": round(float(level), 1), "slope_db_per_octave": round(oct_slope, 2),
            "digital_silence_fraction": round(digital_silence, 3), "first_vs_last_third_db": None if lsd is None else round(lsd, 1)}


# ------------------------------------------------------------------------------------------------ clipping
def clipping(x: np.ndarray) -> dict:
    if len(x) < 4000:
        return {"assessable": False}
    peak = float(np.abs(x).max())
    if peak < 0.05:
        return {"assessable": True, "peak_dbfs": round(20 * np.log10(peak + 1e-12), 1), "clipped": False, "fraction": 0.0, "longest_run": 0, "events": 0}
    thr = peak * 0.995
    hit = np.abs(x) >= thr
    # runs of consecutive samples at the ceiling
    d = np.diff(np.concatenate([[0], hit.view(np.int8), [0]]))
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    runs = ends - starts
    long_runs = runs[runs >= 3]
    clipped = bool(len(long_runs) >= 3)
    return {"assessable": True, "peak_dbfs": round(20 * np.log10(peak + 1e-12), 2), "clipped": clipped, "fraction": round(float(hit.mean()), 6),
            "longest_run": int(runs.max()) if len(runs) else 0, "events": int(len(long_runs)), "at_full_scale": bool(peak >= 0.98)}


# ------------------------------------------------------------------------------------------------ band limit
def band_limit(x: np.ndarray, sr: int) -> dict:
    f, P = sps.welch(x, sr, nperseg=4096)
    db = 10 * np.log10(P + 1e-20)
    ref = np.median(db[(f > 300) & (f < 3000)])
    above = np.nonzero(db > ref - 55)[0]
    return {"cutoff_hz": round(float(f[above[-1]]), 0) if len(above) else 0.0, "sample_rate": sr}


# ------------------------------------------------------------------------------------------------ A/V sync
def av_sync(mouth: np.ndarray, env: np.ndarray, fps: float, max_lag_s: float = 0.5, n_perm: int = 300, seed: int = 0) -> dict:
    """Cross-correlate mouth-region motion with the speech envelope. A positive lag means mouth movement comes LATER than the
    sound it matches (video behind audio). The p-value comes from circularly shifting the audio by random whole-second offsets."""
    n = min(len(mouth), len(env))
    if n < int(8 * fps):
        return {"assessable": False, "reason": "under 8 s of overlapping face and speech"}
    m, a = np.asarray(mouth[:n], float), np.asarray(env[:n], float)

    def prep(v):
        v = v - np.convolve(v, np.ones(int(fps)) / int(fps), mode="same")  # remove slow drift
        return (v - v.mean()) / (v.std() + 1e-9)

    m, a = prep(m), prep(a)
    L = int(max_lag_s * fps)
    lags = np.arange(-L, L + 1)

    def xc(av):
        out = []
        for l in lags:
            if l >= 0:
                out.append(np.mean(av[: n - l] * m[l:]))
            else:
                out.append(np.mean(av[-l:] * m[: n + l]))
        return np.array(out)

    c = xc(a)
    k = int(np.argmax(c))
    rng = np.random.default_rng(seed)
    null = []
    for _ in range(n_perm):
        s = int(rng.integers(int(fps), n - int(fps)))
        null.append(np.abs(xc(np.roll(a, s))).max())
    p = (1 + sum(v >= abs(c[k]) for v in null)) / (1 + n_perm)
    return {"assessable": True, "lag_ms": round(float(lags[k] / fps * 1000), 0), "correlation": round(float(c[k]), 3), "p": round(float(p), 4),
            "n_frames": int(n), "peak_to_median": round(float(abs(c[k]) / (np.median(np.abs(c)) + 1e-9)), 2)}


def speech_envelope(x: np.ndarray, sr: int, fps: float, nframes: int) -> np.ndarray:
    b, a = sps.butter(3, [150 / (sr / 2), min(3500, sr / 2 * 0.9) / (sr / 2)], btype="band")
    y = sps.filtfilt(b, a, x)
    hop = sr / fps
    env = np.array([np.sqrt(np.mean(y[int(i * hop):int((i + 1) * hop)] ** 2) + 1e-12) for i in range(nframes)])
    return np.log(env + 1e-6)


def mouth_motion(path, fps: float = 20.0, seconds: float = 40.0) -> dict:
    """Motion energy in the lower face (mouth region) per frame, from YuNet face boxes."""
    from ...analyzers.faces import detect_faces
    prev, box, vals, seen = None, None, [], 0
    for i, rgb in enumerate(decode_rgb_stream(path, 320, fps, seconds)):
        gray = rgb.mean(axis=2).astype(np.float32)
        if i % 3 == 0:
            fs = detect_faces(rgb)
            if fs is None:
                return {"assessable": False, "reason": "face detector model not installed"}
            if fs:
                best = max(fs, key=lambda f: f["box"][2] * f["box"][3])
                box = best["box"]
        if box is not None:
            seen += 1
            x, y, w, h = box
            y0, y1 = int(y + 0.58 * h), int(y + 1.02 * h)
            x0, x1 = int(x + 0.2 * w), int(x + 0.8 * w)
            reg = gray[max(0, y0):y1, max(0, x0):x1]
            if prev is not None and prev.shape == reg.shape and reg.size > 50:
                vals.append(float(np.abs(reg - prev).mean() / (reg.mean() + 5)))
            else:
                vals.append(vals[-1] if vals else 0.0)
            prev = reg
        else:
            vals.append(vals[-1] if vals else 0.0)
            prev = None
    n = len(vals)
    return {"assessable": n >= 8 * fps and seen / max(1, n) > 0.6, "series": np.array(vals), "face_fraction": round(seen / max(1, n), 2), "frames": n, "fps": fps}


@register
class AudioForensicsModule(ForensicModule):
    id = "audio_forensics"
    title = "Audio forensics"
    question = "What does the audio say about the recording environment and chain, and does it stay in step with the picture?"
    version = "1.0"
    modalities = ("audio", "video")
    requirements = [Requirement(kind="binary", name="ffmpeg", how_to_enable="Install FFmpeg and add it to PATH"),
                    Requirement(kind="model", name="yunet", optional=True, how_to_enable="uv run python ../scripts/setup_models.py yunet   (needed for audio/video sync)")]
    library_versions = ("numpy", "scipy", "ffmpeg")
    order = 50
    limitations = [
        "These measurements describe the recording and its processing; none of them says whether a voice is genuine. No voice-clone classifier is bundled.",
        "Lossy codecs, noise reduction and platform re-encoding (WhatsApp voice notes, social video) change hum, noise floor and reverberation estimates.",
        "Reverberation is a blind estimate from speech decays and tends to under-read in dry rooms.",
        "Audio/video sync needs a visible talking face for at least 8 s; otherwise it is Unknown.",
        "Estimators were validated on simulated audio and video only.",
    ]

    def analyse(self, fx: FCtx) -> ModuleResult:
        res = ModuleResult(module=self.id, title=self.title, version=self.version)
        F = res.findings
        got = decode_audio_native(fx.path, seconds=180)
        info = audio_stream_info(fx.path) or {}
        if got is None:
            F.append(Finding(id="audio.stream", title="Audio stream", kind="unknown",
                             statement="The file has no decodable audio stream." if fx.modality == "video" else "The audio could not be decoded."))
            res.status = "partial" if fx.modality == "video" else "error"
            res.summary = F[0].statement
            return res
        x, sr = got
        dur = len(x) / sr
        fx.shared["audio_native"] = (x, sr)
        F.append(Finding(id="audio.stream", title="Audio stream", kind="observation", value=info.get("codec_name"),
                         statement=f"{info.get('codec_name')} audio, {sr} Hz, {dur:.1f} s analysed" + (f", {int(info['bit_rate']) // 1000} kb/s" if info.get("bit_rate") else "") + ".",
                         data=info))
        if dur < 2.0:
            F.append(Finding(id="audio.short", title="Length", kind="unknown", statement=f"Only {dur:.1f} s of audio: too short for most audio tests."))
            res.status, res.summary = "partial", F[-1].statement
            return res
        # ---- clipping
        cl = clipping(x)
        if cl.get("assessable"):
            if cl["clipped"]:
                F.append(Finding(id="clipping", title="Clipping", kind="observation", value=cl["fraction"] * 100, unit="% of samples",
                                 statement=f"{cl['events']} stretches of flat-topped peaks at {cl['peak_dbfs']:.1f} dBFS ({cl['fraction']:.3%} of samples, longest run {cl['longest_run']} samples): the signal was driven into a limit.",
                                 why="Clipping from an over-driven microphone, input stage or limiter. It also suggests the loudest parts were cut off before this file was made.",
                                 caution="A limiter or clipper applied in editing or by the platform looks the same.", data=cl))
            else:
                F.append(Finding(id="clipping", title="Clipping", kind="observation", value=0, statement=f"No clipping found (peak {cl['peak_dbfs']:.1f} dBFS).", data=cl))
        # ---- band limit
        bl = band_limit(x, sr)
        F.append(Finding(id="bandwidth", title="Bandwidth", kind="observation", value=bl["cutoff_hz"], unit="Hz",
                         statement=f"Usable bandwidth ends near {bl['cutoff_hz'] / 1000:.1f} kHz (sample rate {sr} Hz).",
                         why="A hard ceiling well below half the sample rate shows earlier low-rate processing, lossy coding or a telephone/voice-note path.",
                         caution="Codecs such as Opus (WhatsApp) and MP3 produce it; it is processing history, not synthesis.", data=bl))
        # ---- hum
        hm = hum_analysis(x, sr)
        if hm.get("assessable"):
            if hm["present"]:
                F.append(Finding(id="hum", title="Mains hum", kind="observation", value=hm["f0"], unit="Hz",
                                 statement=f"Mains hum at {hm['f0']:.0f} Hz is present ({hm['prominence_db'][0]:.0f} dB above its surroundings; harmonics {hm['prominence_db'][1:]} dB).",
                                 why="A power-line hum means the recording was made near mains-powered equipment; its frequency (50 or 60 Hz) fits the mains standard of the place.",
                                 caution="50 Hz is used in most of Europe, Asia and Africa and 60 Hz in the Americas and parts of Asia, so this narrows nothing down to a place.", data=hm))
                if hm["abrupt_changes_at_window"]:
                    F.append(Finding(id="hum.change", title="Hum changes abruptly", kind="hypothesis", confidence=0.3,
                                     statement=f"The hum level changes by more than 15 dB between neighbouring 2-second windows (windows {hm['abrupt_changes_at_window'][:5]}): the recording environment may change, or parts may be joined.",
                                     caution="Switching equipment on or off, moving rooms, or noise-reduction gating produce the same step."))
            else:
                F.append(Finding(id="hum", title="Mains hum", kind="observation", value=0, statement="No mains hum was found at 50 or 60 Hz or their harmonics.",
                                 caution="Absent hum is normal for battery devices, noise-reduced audio and platform re-encodes.", data=hm))
        else:
            F.append(Finding(id="hum", title="Mains hum", kind="unknown", statement=f"Hum could not be assessed ({hm.get('reason')})."))
        # ---- reverberation
        rv = rt60_estimate(x, sr)
        if rv.get("assessable"):
            F.append(Finding(id="reverb", title="Reverberation", kind="observation", value=rv["rt60_s"], unit="s (RT60 estimate)",
                             statement=f"Blind estimate of reverberation time ≈ {rv['rt60_s']:.2f} s from {rv['n_decays']} decays (range {rv['min_s']:.2f}–{rv['max_s']:.2f} s).",
                             why="Under about 0.15 s is a very dry space (booth, close microphone, or synthetic/studio audio); 0.3–0.8 s is a typical room; over 1 s is a hall or hard-walled space.",
                             caution="Blind estimates tend to under-read; close-microphone speech hides the room. Compare parts of the SAME recording, not different recordings.", data=rv))
            if rv["spread"] > 0.6 and rv["n_decays"] >= 8:
                F.append(Finding(id="reverb.inconsistent", title="Reverberation varies a lot", kind="hypothesis", confidence=0.25,
                                 statement=f"Decay times vary widely inside this recording (spread {rv['spread']:.0%}). Parts may have been recorded in different spaces.",
                                 caution="Speech style and pauses change blind estimates naturally; this is a weak lead."))
        else:
            F.append(Finding(id="reverb", title="Reverberation", kind="unknown", statement=f"Reverberation could not be estimated: {rv.get('reason')}."))
        # ---- noise floor
        nf = noise_floor(x, sr)
        if nf.get("assessable"):
            F.append(Finding(id="noise_floor", title="Microphone / noise floor", kind="observation", value=nf["level_dbfs"], unit="dBFS",
                             statement=f"The quietest passages sit at {nf['level_dbfs']:.0f} dBFS with a spectral tilt of {nf['slope_db_per_octave']:+.1f} dB/octave"
                                       + (f"; {nf['digital_silence_fraction']:.0%} of the file is digital silence" if nf["digital_silence_fraction"] > 0.02 else "") + ".",
                             why="The residual noise belongs to the microphone, preamp and room. Digital silence between words points to a noise gate or to audio that never had a noise floor.",
                             caution="Noise reduction, AGC and codecs reshape the floor.", data=nf))
            if nf["first_vs_last_third_db"] is not None and nf["first_vs_last_third_db"] > 6:
                F.append(Finding(id="noise_floor.inconsistent", title="Noise floor differs between parts", kind="hypothesis", confidence=0.3,
                                 statement=f"The background noise of the first and last third differ by {nf['first_vs_last_third_db']:.1f} dB (spectral distance): different recording conditions, or two recordings joined.",
                                 caution="Moving around, a passing vehicle, or adaptive noise reduction cause this too."))
        else:
            F.append(Finding(id="noise_floor", title="Microphone / noise floor", kind="unknown", statement=f"The noise floor could not be profiled ({nf.get('reason')})."))
        # ---- A/V sync (video only)
        if fx.modality == "video":
            self._sync(fx, F)
        # ---- optional learned detectors: explicit
        F.append(Finding(id="voice.clone", title="Voice-clone / anti-spoofing model", kind="unknown",
                         statement="No learned voice-clone detector is installed, so whether the voice is synthetic was not assessed.",
                         caution="Add one as a plugin (see plugins/README). Forensic voice comparison against verified recordings by an expert remains the stronger test."))
        res.summary = "; ".join(f.statement for f in F if f.kind in ("hypothesis", "supported"))[:300] or "No audio indicator of editing or unusual recording conditions was found; this does not show the audio is genuine."
        res.status = "ok"
        return res

    def _sync(self, fx: FCtx, F: list[Finding]) -> None:
        x, sr = fx.shared["audio_native"]
        try:
            mm = mouth_motion(fx.path)
        except Exception as e:  # noqa: BLE001
            F.append(Finding(id="av_sync", title="Audio/video synchronisation", kind="unknown", statement=f"Sync could not be assessed ({type(e).__name__})."))
            return
        if not mm.get("assessable"):
            secs = mm.get("frames", 0) / mm.get("fps", 20.0)
            why = mm.get("reason") or (f"the clip is only {secs:.1f} s long (8 s of visible talking are needed)" if secs < 8 else
                                       f"a face was visible in only {mm.get('face_fraction', 0):.0%} of the frames")
            F.append(Finding(id="av_sync", title="Audio/video synchronisation", kind="unknown", statement=f"Sync could not be assessed: {why}."))
            return
        fps = mm["fps"]
        env = speech_envelope(x, sr, fps, len(mm["series"]))
        r = av_sync(mm["series"], env, fps)
        if not r.get("assessable"):
            F.append(Finding(id="av_sync", title="Audio/video synchronisation", kind="unknown", statement=f"Sync could not be assessed: {r.get('reason')}."))
            return
        sig = r["p"] < 0.01 and r["correlation"] > 0.12
        if not sig:
            F.append(Finding(id="av_sync", title="Audio/video synchronisation", kind="unknown", value=r["correlation"],
                             statement=f"No significant link between mouth movement and the sound was found (best correlation {r['correlation']:+.2f}, p = {r['p']:.2f}). "
                                       "That is not evidence of a mismatch: the face may not be the speaker, or movement is too subtle at this resolution.", data=r))
            return
        lag = r["lag_ms"]
        side = "behind" if lag > 0 else "ahead of"
        within = abs(lag) <= 100
        F.append(Finding(id="av_sync", title="Audio/video synchronisation", kind="supported" if not within else "observation", confidence=None if within else 0.45,
                         value=lag, unit="ms",
                         statement=f"Mouth movement follows the sound with the best match when the picture is {abs(lag):.0f} ms {side} the audio "
                                   f"(correlation {r['correlation']:+.2f}, p = {r['p']:.3f}). "
                                   + ("That is within the range viewers cannot perceive." if within else "That is a perceptible offset."),
                         caution="An offset can come from capture, editing or streaming, and does not by itself show synthetic content. Resolution is about 50 ms at 20 fps.", data=r))
