"""Media decoding helpers built on Pillow and the ffmpeg/ffprobe binaries."""
from __future__ import annotations

import io
import json
import mimetypes
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp", ".flv", ".ts"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".amr", ".wma"}


@lru_cache
def ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


@lru_cache
def ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


@lru_cache
def ffmpeg_version() -> str:
    if not ffmpeg_bin():
        return "unavailable"
    out = subprocess.run([ffmpeg_bin(), "-version"], capture_output=True, text=True).stdout
    return out.splitlines()[0] if out else "unknown"


def sniff_modality(data: bytes, filename: str) -> tuple[str, str]:
    """Return (modality, mime) using magic bytes first and the extension second."""
    head = data[:64]
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image", "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if head.startswith(b"GIF8"):
        return "image", "image/gif"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1", b"avif"):
            return "image", "image/heic" if brand != b"avif" else "image/avif"
        if brand.startswith(b"M4A"):
            return "audio", "audio/mp4"
        return "video", "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "video/webm"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio", "audio/mpeg"
    if head.startswith(b"OggS"):
        return "audio", "audio/ogg"
    if head.startswith(b"fLaC"):
        return "audio", "audio/flac"
    ext = Path(filename).suffix.lower()
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if ext in IMAGE_EXT:
        return "image", mime
    if ext in VIDEO_EXT:
        return "video", mime
    if ext in AUDIO_EXT:
        return "audio", mime
    return "unknown", mime


def load_image(path: Path, *, apply_exif_orientation: bool = True) -> Image.Image:
    im = Image.open(path)
    im.load()
    if apply_exif_orientation:
        im = ImageOps.exif_transpose(im)
    return im


def to_rgb_array(im: Image.Image) -> np.ndarray:
    return np.asarray(im.convert("RGB"))


def png_bytes(im: Image.Image | np.ndarray) -> bytes:
    if isinstance(im, np.ndarray):
        im = Image.fromarray(im)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def jpeg_bytes(im: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def thumbnail(im: Image.Image, size: int = 320) -> Image.Image:
    t = im.convert("RGB").copy()
    t.thumbnail((size, size))
    return t


def ffprobe(path: Path) -> dict:
    if not ffprobe_bin():
        return {}
    r = subprocess.run([ffprobe_bin(), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
                        str(path)], capture_output=True, text=True)
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def video_info(path: Path) -> dict:
    p = ffprobe(path)
    v = next((s for s in p.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in p.get("streams", []) if s.get("codec_type") == "audio"), None)
    fr = v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1"
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    dur = float(p.get("format", {}).get("duration") or v.get("duration") or 0)
    return {"width": v.get("width"), "height": v.get("height"), "fps": fps, "duration": dur,
            "has_audio": a is not None, "codec": v.get("codec_name"), "probe": p}


def sample_frames(path: Path, *, max_frames: int = 24, scene_threshold: float = 0.3) -> list[tuple[float, Image.Image]]:
    """Sample frames uniformly across the video. Returns [(timestamp_seconds, PIL image)]."""
    info = video_info(path)
    dur = info["duration"] or 0
    if not ffmpeg_bin() or dur <= 0:
        return []
    n = max(1, min(max_frames, int(dur * 2) or 1))
    times = [dur * (i + 0.5) / n for i in range(n)]
    frames = []
    for t in times:
        r = subprocess.run([ffmpeg_bin(), "-v", "quiet", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                            "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
        if r.stdout:
            try:
                frames.append((t, Image.open(io.BytesIO(r.stdout)).convert("RGB")))
            except Exception:  # noqa: BLE001
                pass
    return frames


def decode_audio(path: Path, sr: int = 16000) -> np.ndarray | None:
    """Decode any audio/video file's first audio stream to mono float32 at ``sr``."""
    if not ffmpeg_bin():
        return None
    r = subprocess.run([ffmpeg_bin(), "-v", "quiet", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                        "-f", "f32le", "-"], capture_output=True)
    if not r.stdout:
        return None
    return np.frombuffer(r.stdout, dtype=np.float32).copy()
