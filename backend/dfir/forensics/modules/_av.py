"""Shared audio/video helpers for the forensic modules (ffmpeg / ffprobe wrappers)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from ...analyzers.media_io import ffmpeg_bin, ffprobe_bin


def frame_table(path: Path, seconds: float = 90.0, limit: int = 4000) -> list[dict]:
    """Per-frame timing and picture type of the first video stream (ffprobe -show_frames), in decode order."""
    if not ffprobe_bin():
        return []
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-read_intervals", f"%+{int(seconds)}",
                        "-show_entries", "frame=pts_time,pkt_pts_time,best_effort_timestamp_time,pict_type,key_frame,pkt_size,duration_time",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        frames = json.loads(r.stdout or "{}").get("frames", [])
    except json.JSONDecodeError:
        return []
    out = []
    for f in frames[:limit]:
        t = f.get("best_effort_timestamp_time") or f.get("pts_time") or f.get("pkt_pts_time")
        try:
            out.append({"t": float(t) if t not in (None, "N/A") else None, "type": f.get("pict_type", "?"), "key": f.get("key_frame") == 1 or f.get("key_frame") == "1",
                        "size": int(f.get("pkt_size") or 0)})
        except ValueError:
            continue
    return out


def decode_gray(path: Path, width: int = 320, max_frames: int = 240, seconds: float | None = None) -> np.ndarray:
    """Decoded luma frames [n, h, w] at every source frame (no frame-rate conversion), at a reduced width."""
    if not ffmpeg_bin():
        return np.zeros((0, 0, 0), np.uint8)
    probe = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
                           capture_output=True, text=True)
    st = (json.loads(probe.stdout or "{}").get("streams") or [{}])[0]
    sw, sh = int(st.get("width") or 0), int(st.get("height") or 0)
    if not sw or not sh:
        return np.zeros((0, 0, 0), np.uint8)
    w = min(width, sw)
    w -= w % 2
    h = int(round(sh * w / sw))
    h -= h % 2
    for mode in (["-fps_mode", "passthrough"], ["-vsync", "0"]):
        cmd = [ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:v:0", *mode, "-vf", f"scale={w}:{h}:flags=area,format=gray"]
        if seconds:
            cmd += ["-t", str(seconds)]
        cmd += ["-frames:v", str(max_frames), "-f", "rawvideo", "-"]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and r.stdout:
            break
    n = len(r.stdout) // (w * h)
    return np.frombuffer(r.stdout[: n * w * h], np.uint8).reshape(n, h, w).copy() if n else np.zeros((0, h, w), np.uint8)


def decode_rgb_stream(path: Path, width: int, fps: float, seconds: float):
    """Yield RGB frames [h, w, 3] at ``fps`` for ``seconds`` seconds (streamed, so memory stays flat)."""
    probe = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
                           capture_output=True, text=True)
    st = (json.loads(probe.stdout or "{}").get("streams") or [{}])[0]
    sw, sh = int(st.get("width") or 0), int(st.get("height") or 0)
    if not sw or not sh:
        return
    w = min(width, sw)
    w -= w % 2
    h = int(round(sh * w / sw))
    h -= h % 2
    p = subprocess.Popen([ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-t", str(seconds), "-vf", f"fps={fps},scale={w}:{h}:flags=area",
                          "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.kill()
        p.wait()


def audio_stream_info(path: Path) -> dict | None:
    if not ffprobe_bin():
        return None
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate,duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        s = (json.loads(r.stdout or "{}").get("streams") or [None])[0]
    except json.JSONDecodeError:
        return None
    return s


def decode_audio_native(path: Path, max_sr: int = 48000, seconds: float | None = None) -> tuple[np.ndarray, int] | None:
    """Mono float32 at the file's own sample rate (capped), without resampling when possible so clipping and band-limits survive."""
    info = audio_stream_info(path)
    if not info or not ffmpeg_bin():
        return None
    sr = min(int(info.get("sample_rate") or 44100), max_sr)
    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", str(sr)]
    if seconds:
        cmd += ["-t", str(seconds)]
    r = subprocess.run(cmd + ["-f", "f32le", "-"], capture_output=True)
    if not r.stdout:
        return None
    return np.frombuffer(r.stdout, np.float32).copy(), sr
