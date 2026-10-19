"""Module 4 — video acquisition analysis: frame timing, codec traces, camera-motion / stabilisation and rolling shutter.

What a video's *container and motion* reveal about how it was recorded and what happened to it afterwards. Timing and codec
facts are direct readings of the file; the motion-based tests (stabilisation, rolling shutter) are statistical estimates that
need camera movement and are validated on simulated video only.
"""
from __future__ import annotations

import re
import struct

import cv2
import numpy as np
from scipy import stats

from ...analyzers.media_io import ffprobe, ffprobe_bin
from ..base import Evidence, FCtx, Finding, ForensicModule, ModuleResult, Requirement
from ..registry import register
from ._av import decode_gray, frame_table


# ------------------------------------------------------------------------------------------------ frame timing
def timing_stats(ts: np.ndarray) -> dict:
    ts = np.asarray([t for t in ts if t is not None], float)
    if len(ts) < 12:
        return {"assessable": False, "n": int(len(ts))}
    ts = np.sort(ts)
    dt = np.diff(ts)
    dt = dt[dt > 1e-6]
    if len(dt) < 10:
        return {"assessable": False, "n": int(len(ts))}
    med = float(np.median(dt))
    ratio = dt / med
    gaps = ratio[ratio > 1.6]
    return {"assessable": True, "n": int(len(ts)), "median_dt_ms": round(med * 1000, 3), "fps_from_timestamps": round(1 / med, 3),
            "cv": round(float(dt.std() / dt.mean()), 4), "frac_off_nominal": round(float((np.abs(ratio - 1) > 0.1).mean()), 4),
            "n_gaps": int(len(gaps)), "gap_multiples": sorted({int(round(g)) for g in gaps})[:6],
            "max_gap_ms": round(float(dt.max() * 1000), 1), "variable_frame_rate": bool((np.abs(ratio - 1) > 0.1).mean() > 0.05)}


def duplicate_frames(frames: np.ndarray) -> dict:
    """Frames identical (to within compression noise) to their predecessor while their neighbours still move."""
    if len(frames) < 12:
        return {"assessable": False}
    d = np.abs(np.diff(frames.astype(np.int16), axis=0)).mean(axis=(1, 2))
    med = float(np.median(d))
    if med < 0.25:
        return {"assessable": False, "reason": "almost no motion in the clip"}
    prev_d, next_d = np.r_[d[0], d[:-1]], np.r_[d[1:], d[-1]]
    # identical to the previous frame while BOTH neighbouring steps show real motion (a pan that reverses direction also has
    # a near-zero step, but its neighbours are small too)
    dup = (d < max(0.06, 0.04 * med)) & (prev_d > 4 * np.maximum(d, 0.03)) & (next_d > 4 * np.maximum(d, 0.03))
    # periodic duplicates (e.g. 24 -> 30 fps conversion) repeat at a regular spacing
    idx = np.nonzero(dup)[0]
    spacing = np.diff(idx)
    regular = bool(len(idx) >= 4 and len(spacing) and np.std(spacing) < 0.2 * np.mean(spacing) + 0.5)
    return {"assessable": True, "n_duplicates": int(dup.sum()), "fraction": round(float(dup.mean()), 4), "regular_spacing": regular,
            "median_spacing": float(np.median(spacing)) if len(spacing) else None}


# ------------------------------------------------------------------------------------------------ codec traces
def mp4_atoms(path, limit: int = 64) -> list[tuple[str, int]]:
    out = []
    try:
        with open(path, "rb") as f:
            pos = 0
            f.seek(0, 2)
            end = f.tell()
            while pos + 8 <= end and len(out) < limit:
                f.seek(pos)
                hdr = f.read(16)
                size, typ = struct.unpack(">I4s", hdr[:8])
                if size == 1 and len(hdr) >= 16:
                    size = struct.unpack(">Q", hdr[8:16])[0]
                elif size == 0:
                    size = end - pos
                if size < 8:
                    break
                out.append((typ.decode("latin-1"), size))
                pos += size
    except (OSError, struct.error):
        return out
    return out


ENCODER_PATTERNS = [
    (re.compile(rb"x264 - core (\d+)[^\x00]{0,40}"), "x264 (software H.264 encoder) settings string in the video stream"),
    (re.compile(rb"x265 \[info\]: HEVC encoder version ([\d.+\w-]+)"), "x265 (software HEVC encoder) settings string"),
    (re.compile(rb"Lavc(\d+\.\d+\.\d+)"), "libavcodec (FFmpeg) encoder tag"),
    (re.compile(rb"Lavf(\d+\.\d+\.\d+)"), "libavformat (FFmpeg) muxer tag"),
    (re.compile(rb"(HandBrake [\d.]+)"), "HandBrake"),
]


def encoder_traces(path, nbytes: int = 3_000_000) -> list[dict]:
    try:
        with open(path, "rb") as f:
            head = f.read(nbytes)
            f.seek(0, 2)
            size = f.tell()
            tail = b""
            if size > nbytes:
                f.seek(max(0, size - 1_000_000))
                tail = f.read()
    except OSError:
        return []
    data = head + tail
    out, seen = [], set()
    for rx, label in ENCODER_PATTERNS:
        m = rx.search(data)
        if m and label not in seen:
            seen.add(label)
            out.append({"label": label, "text": m.group(0)[:60].decode("latin-1", "replace")})
    return out


def gop_structure(table: list[dict]) -> dict:
    if len(table) < 20:
        return {"assessable": False}
    types = "".join(t["type"] if t["type"] in "IPB" else "?" for t in table)
    keys = [i for i, c in enumerate(types) if c == "I"]
    gaps = np.diff(keys) if len(keys) > 1 else np.array([])
    max_b = max((len(m.group(0)) for m in re.finditer(r"B+", types)), default=0)
    return {"assessable": True, "n_frames": len(types), "n_keyframes": len(keys), "keyint_median": float(np.median(gaps)) if len(gaps) else None,
            "keyint_regular": bool(len(gaps) >= 2 and np.std(gaps) < 0.15 * np.mean(gaps)), "b_frames": types.count("B") > 0, "max_consecutive_b": max_b,
            "pattern_head": types[:40]}


# ------------------------------------------------------------------------------------------------ motion
def global_motion(frames: np.ndarray) -> np.ndarray:
    """Per-step global translation (dx, dy) between consecutive frames by phase correlation. Shape [n-1, 2]."""
    h, w = frames.shape[1:]
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    out = np.zeros((len(frames) - 1, 2), np.float32)
    prev = frames[0].astype(np.float32)
    for i in range(1, len(frames)):
        cur = frames[i].astype(np.float32)
        (dx, dy), _ = cv2.phaseCorrelate(prev, cur, win)
        out[i - 1] = (dx, dy)
        prev = cur
    return out


def vertical_edge_tilt(frame: np.ndarray) -> float:
    """Average tilt (dx per dy) of near-vertical edges, from the structure tensor. Weighted by edge strength."""
    g = cv2.GaussianBlur(frame.astype(np.float32), (0, 0), 1.0)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    strong = (np.abs(gx) > 2 * np.abs(gy)) & (np.hypot(gx, gy) > np.percentile(np.hypot(gx, gy), 85))
    jxx = float((gx[strong] ** 2).sum())
    jxy = float((gx[strong] * gy[strong]).sum())
    return -jxy / jxx if jxx > 0 else 0.0


def rolling_shutter(frames: np.ndarray, motion: np.ndarray) -> dict:
    """Across frames, edge tilt should follow horizontal camera speed if the sensor is read out row by row: a row captured later
    sees the scene displaced further. The regression slope times the frame height is the read-out time as a fraction of the
    frame period. Needs real horizontal camera movement."""
    n = len(frames)
    if n < 40:
        return {"assessable": False, "reason": "fewer than 40 frames"}
    v = (motion[:-1, 0] + motion[1:, 0]) / 2  # speed at frame i (average of the steps before and after), frames 1..n-2
    tilt = np.array([vertical_edge_tilt(f) for f in frames[1:-1]])
    if v.std() < 0.35:
        return {"assessable": False, "reason": f"too little horizontal camera movement (speed varies by {v.std():.2f} px/frame; ≥0.35 needed)"}
    ok = np.abs(v) < 40
    v, tilt = v[ok], tilt[ok]
    res = stats.linregress(v, tilt)
    h = frames.shape[1]
    rho = float(res.slope * h)
    se = float(res.stderr * h)
    return {"assessable": True, "readout_fraction": round(rho, 3), "stderr": round(se, 3), "r": round(float(res.rvalue), 3), "p": float(res.pvalue),
            "n_frames": int(len(v)), "speed_std_px": round(float(v.std()), 2)}


def stabilisation(motion: np.ndarray, fps: float) -> dict:
    n = len(motion)
    if n < 30:
        return {"assessable": False, "reason": "fewer than 30 frames"}
    tr = np.cumsum(motion, axis=0)
    amp = float(np.hypot(*(tr - tr.mean(0)).T).std())
    if float(np.hypot(motion[:, 0], motion[:, 1]).mean()) < 0.15:
        return {"assessable": True, "static": True, "path_std_px": round(amp, 2)}
    spec = np.abs(np.fft.rfft(motion[:, 0] - motion[:, 0].mean())) ** 2 + np.abs(np.fft.rfft(motion[:, 1] - motion[:, 1].mean())) ** 2
    f = np.fft.rfftfreq(n, 1 / fps)
    total = float(spec[1:].sum()) + 1e-9
    hf = float(spec[f >= min(4.0, fps / 4)].sum())
    return {"assessable": True, "static": False, "path_std_px": round(amp, 2), "high_freq_share": round(hf / total, 4),
            "mean_speed_px": round(float(np.hypot(motion[:, 0], motion[:, 1]).mean()), 3)}


@register
class VideoAcquisitionModule(ForensicModule):
    id = "video_acquisition"
    title = "Video acquisition analysis"
    question = "How was this video recorded and processed: frame timing, codec and container, camera motion and rolling shutter?"
    version = "1.0"
    modalities = ("video",)
    requirements = [Requirement(kind="binary", name="ffprobe", how_to_enable="Install FFmpeg and add it to PATH"),
                    Requirement(kind="binary", name="ffmpeg", how_to_enable="Install FFmpeg and add it to PATH")]
    library_versions = ("numpy", "scipy", "cv2", "ffmpeg")
    order = 40
    limitations = [
        "Frame timing and container facts describe the file as received. Platforms re-encode uploads, which rewrites all of them.",
        "Rolling-shutter and stabilisation estimates need real camera movement; a tripod shot or a still scene gives Unknown.",
        "Codec traces show the LAST encoder, not the original capture device.",
        "Motion-based estimates were validated on simulated video only; no real camera footage has been measured.",
    ]

    def analyse(self, fx: FCtx) -> ModuleResult:
        res = ModuleResult(module=self.id, title=self.title, version=self.version)
        F, ev = res.findings, res.evidence
        probe = fx.shared.get("ffprobe") or ffprobe(fx.path)
        vs = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
        table = frame_table(fx.path)
        fps_nom = vs.get("avg_frame_rate", "0/1")
        try:
            a, b = fps_nom.split("/")
            fps = float(a) / float(b) if float(b) else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        fps = fps or 25.0

        # ------------------------------------------------ frame timing
        ts = timing_stats(np.array([t["t"] for t in table if t["t"] is not None]))
        frames = decode_gray(fx.path, 320, 240)
        dup = duplicate_frames(frames)
        if ts["assessable"]:
            r_rate = vs.get("r_frame_rate")
            F.append(Finding(id="timing.cadence", title="Frame cadence", kind="observation", value=ts["fps_from_timestamps"], unit="fps (from timestamps)",
                             statement=f"Frames arrive every {ts['median_dt_ms']:.2f} ms ({ts['fps_from_timestamps']:.2f} fps; declared {fps_nom}, base rate {r_rate}); "
                                       f"{'variable frame rate' if ts['variable_frame_rate'] else 'constant frame rate'} ({ts['frac_off_nominal']:.1%} of intervals off by >10 %).",
                             why="Fixed-rate cadence is typical of encoded exports; phones record variable rate in low light or on battery saver, and screen recordings are variable.",
                             caution="Re-encoding by a platform usually normalises cadence, so this describes the file, not the original recording.", data=ts))
            if ts["n_gaps"]:
                F.append(Finding(id="timing.gaps", title="Gaps in the frame sequence", kind="hypothesis", confidence=0.35, value=ts["n_gaps"], unit="gap(s)",
                                 statement=f"{ts['n_gaps']} interval(s) are longer than the usual frame spacing (up to {ts['max_gap_ms']:.0f} ms; multiples {ts['gap_multiples']}): "
                                           "frames may have been dropped, cut, or the recording paused.",
                                 caution="Encoder frame drops, variable-rate recording and edit points all look like this. Compare with the audio and the visible content.", data=ts))
        else:
            F.append(Finding(id="timing.cadence", title="Frame cadence", kind="unknown", statement=f"Frame timestamps could not be read reliably ({ts.get('n', 0)} usable)."))
        if dup.get("assessable"):
            if dup["n_duplicates"] > 0:
                F.append(Finding(id="timing.duplicates", title="Duplicated frames", kind="observation", value=dup["n_duplicates"], unit="frame(s)",
                                 statement=f"{dup['n_duplicates']} frame(s) ({dup['fraction']:.1%}) are identical to the one before while the rest of the clip moves"
                                           f"{'; they repeat at regular spacing, as in a frame-rate conversion' if dup['regular_spacing'] else ''}.",
                                 caution="Frame-rate conversion, a frozen source or a dropped stream all repeat frames.", data=dup))
            else:
                F.append(Finding(id="timing.duplicates", title="Duplicated frames", kind="observation", value=0, statement="No repeated frames were found.", data=dup))
        else:
            F.append(Finding(id="timing.duplicates", title="Duplicated frames", kind="unknown", statement=f"Duplicate frames could not be assessed ({dup.get('reason') or 'too few frames'})."))

        # ------------------------------------------------ codec / container
        g = gop_structure(table)
        atoms = mp4_atoms(fx.path)
        order = [a for a, _ in atoms if a in ("ftyp", "moov", "mdat", "free", "wide", "skip")]
        traces = encoder_traces(fx.path)
        codec = vs.get("codec_name")
        F.append(Finding(id="codec.stream", title="Video stream", kind="observation", value=codec,
                         statement=f"{codec} {vs.get('profile') or ''} level {vs.get('level')}, {vs.get('pix_fmt')}, {vs.get('width')}×{vs.get('height')}, "
                                   f"{int(vs.get('bit_rate') or probe.get('format', {}).get('bit_rate') or 0) / 1000:.0f} kb/s.".replace("  ", " "),
                         data={k: vs.get(k) for k in ("codec_name", "profile", "level", "pix_fmt", "bit_rate", "color_space", "color_transfer", "color_range")}))
        if g["assessable"]:
            F.append(Finding(id="codec.gop", title="Group-of-pictures structure", kind="observation", value=g["keyint_median"], unit="frames between keyframes",
                             statement=(f"Keyframes every {g['keyint_median']:.0f} frames ({'regular' if g['keyint_regular'] else 'irregular'}); " if g["keyint_median"] else "Only one keyframe in the analysed part; ")
                                       + f"{'B-frames present (up to ' + str(g['max_consecutive_b']) + ' in a row)' if g['b_frames'] else 'no B-frames'}.",
                             why="Hardware camera encoders and software encoders choose different GOP patterns; an irregular keyframe spacing can mark scene cuts or an edit.",
                             caution="Platforms re-encode with their own GOP; this shows the last encode.", data=g))
        if traces:
            F.append(Finding(id="codec.traces", title="Software-encoder traces", kind="observation", value=traces[0]["label"],
                             statement="The file contains: " + "; ".join(f"{t['label']} (“{t['text']}”)" for t in traces) + ".",
                             why="Camera hardware encoders do not write x264/x265 settings strings; software encoders do.",
                             data={"traces": traces}))
            if any("x26" in t["label"] for t in traces):
                F.append(Finding(id="codec.reencoded", title="Re-encoded after capture", kind="observation", value=True,
                                 statement="The stream was written by a software encoder (x264/x265), so the file has been processed after any camera recording.",
                                 why="Camera hardware encoders do not write x264/x265 settings strings; this is a direct reading of the file, not a statistical estimate.",
                                 caution="Says nothing about WHAT was changed, and a clean export of an unedited video looks the same."))
        else:
            F.append(Finding(id="codec.traces", title="Software-encoder traces", kind="unknown",
                             statement="No x264/x265/FFmpeg encoder strings were found in the parts of the file that were searched.",
                             caution="Absence does not show the file is an original: many encoders write nothing."))
        if order:
            faststart = order.index("moov") < order.index("mdat") if "moov" in order and "mdat" in order else None
            brand = (probe.get("format", {}).get("tags") or {}).get("major_brand")
            F.append(Finding(id="container.layout", title="Container layout", kind="observation", value=" → ".join(order),
                             statement=f"Top-level boxes: {' → '.join(order)}{f' (brand {brand})' if brand else ''}"
                                       + ("; the index (moov) comes BEFORE the media data, the layout used when a file is prepared for streaming." if faststart else
                                          "; the index comes after the media data, as most cameras write it." if faststart is False else "."),
                             caution="Layout is chosen by the last tool or service that wrote the file."))

        # ------------------------------------------------ motion: stabilisation and rolling shutter
        if len(frames) >= 30:
            motion = global_motion(frames)
            fx.shared["video_motion"] = motion
            stab = stabilisation(motion, fps)
            if stab.get("static"):
                F.append(Finding(id="stabilisation", title="Camera stabilisation", kind="unknown",
                                 statement="The camera barely moves, so stabilisation cannot be assessed (tripod, locked-off or heavily stabilised).", data=stab))
            elif stab.get("assessable"):
                hf = stab["high_freq_share"]
                F.append(Finding(id="stabilisation.path", title="Camera path", kind="observation", value=hf, unit="high-frequency share",
                                 statement=f"The camera path moves {stab['mean_speed_px']:.2f} px per frame on average; {hf:.1%} of its motion energy is fast shake (>{min(4.0, fps / 4):.0f} Hz).",
                                 caution="Hand-held footage normally carries tremor; gimbals and digital stabilisation remove it.", data=stab))
                if hf < 0.02 and stab["mean_speed_px"] > 0.8:
                    F.append(Finding(id="stabilisation.hypothesis", title="Stabilised or mounted", kind="hypothesis", confidence=0.3,
                                     statement="The camera moves smoothly with almost no shake: consistent with optical/digital stabilisation or a gimbal/tripod pan.",
                                     caution="A slow deliberate pan by a steady hand looks the same."))
            rs = rolling_shutter(frames, motion)
            if rs.get("assessable"):
                rho, se = rs["readout_fraction"], rs["stderr"]
                sig = rs["p"] < 0.01 and abs(rs["r"]) >= 0.4
                if sig and abs(rho) >= 0.1:
                    F.append(Finding(id="rolling_shutter", title="Rolling-shutter skew", kind="supported", confidence=0.45, value=abs(rho), unit="of a frame period",
                                     statement=f"Vertical edges lean in proportion to horizontal camera speed (r = {rs['r']:+.2f}): a row-by-row read-out of about "
                                               f"{abs(rho):.2f} frame periods (≈ {abs(rho) / fps * 1000:.0f} ms) — as a rolling-shutter sensor produces.",
                                     why="Rows captured later see the scene displaced further, slanting vertical lines when the camera pans.",
                                     caution="Digital stabilisation and warping can remove or imitate skew. The magnitude is an estimate; compare ranges between cameras only cautiously.", data=rs))
                else:
                    F.append(Finding(id="rolling_shutter", title="Rolling-shutter skew", kind="observation", value=rho, unit="of a frame period",
                                     statement=f"No measurable skew linked to camera speed (estimate {rho:+.2f} ± {se:.2f} frame periods).",
                                     why="Consistent with a global shutter, a stabiliser that removes skew, or a video that never came from a rolling-shutter sensor.",
                                     caution="Not evidence of any particular origin.", data=rs))
            else:
                F.append(Finding(id="rolling_shutter", title="Rolling-shutter skew", kind="unknown",
                                 statement=f"Rolling shutter could not be assessed: {rs.get('reason')}."))
        else:
            for fid, t in (("stabilisation", "Camera stabilisation"), ("rolling_shutter", "Rolling-shutter skew")):
                F.append(Finding(id=fid, title=t, kind="unknown", statement=f"{t} could not be assessed: the video is too short ({len(frames)} frames decoded)."))
        res.summary = (F[0].statement if F else "")
        res.status = "ok"
        return res
