"""Robust media fingerprints for near-duplicate discovery.

* Images: PDQ (Meta, 256-bit) + pHash + dHash on the image and its mirror; colour moments;
  aspect ratio. Crops are handled by geometric verification in ``match.py``.
* Video: per-second PDQ sequence (and mirror), plus a representative frame.
* Audio: landmark (spectral-peak pair) hashes, robust to re-encoding and level changes.
"""
from __future__ import annotations

from pathlib import Path

import imagehash
import numpy as np
import pdqhash
from PIL import Image, ImageOps
from scipy import ndimage, signal

from ..analyzers.media_io import decode_audio, load_image, sample_frames


def _bits_to_hex(bits: np.ndarray) -> str:
    return np.packbits(bits.astype(np.uint8)).tobytes().hex()


def hex_to_bits(h: str) -> np.ndarray:
    return np.unpackbits(np.frombuffer(bytes.fromhex(h), dtype=np.uint8))


def hamming_hex(a: str, b: str) -> int:
    return int(np.count_nonzero(hex_to_bits(a) != hex_to_bits(b)))


def pdq(im: Image.Image) -> tuple[str, int]:
    arr = np.asarray(im.convert("RGB"))
    h, q = pdqhash.compute(arr)
    return _bits_to_hex(h), int(q)


def image_fingerprint(im: Image.Image) -> dict:
    im = im.convert("RGB")
    mir = ImageOps.mirror(im)
    p, q = pdq(im)
    pm, _ = pdq(mir)
    a = np.asarray(im.resize((64, 64)), dtype=np.float32)
    return {
        "pdq": p, "pdq_quality": q, "pdq_mirror": pm,
        "phash": str(imagehash.phash(im)), "phash_mirror": str(imagehash.phash(mir)),
        "dhash": str(imagehash.dhash(im)),
        "color_mean": [float(x) for x in a.reshape(-1, 3).mean(0)],
        "aspect": im.width / max(1, im.height), "size": [im.width, im.height],
    }


def video_fingerprint(path: Path) -> dict:
    frames = sample_frames(path, max_frames=48)
    seq = []
    for t, im in frames:
        p, q = pdq(im)
        pm, _ = pdq(ImageOps.mirror(im))
        seq.append({"t": t, "pdq": p, "pdq_mirror": pm, "q": q})
    rep = image_fingerprint(frames[len(frames) // 2][1]) if frames else {}
    return {"frames": seq, "rep": rep}


def audio_landmarks(y: np.ndarray, sr: int, fan: int = 5) -> list[tuple[int, int]]:
    """Return [(hash, t_frame)] from spectral-peak pairs (Wang 2003 style)."""
    f, t, S = signal.spectrogram(y, fs=sr, nperseg=512, noverlap=256)
    S = np.log1p(S)
    peaks = (S == ndimage.maximum_filter(S, size=(15, 15))) & (S > np.percentile(S, 97))
    fi, ti = np.nonzero(peaks)
    order = np.argsort(ti)
    fi, ti = fi[order], ti[order]
    out = []
    for i in range(len(ti)):
        for j in range(1, fan + 1):
            if i + j >= len(ti):
                break
            dt = ti[i + j] - ti[i]
            if 0 < dt <= 64:
                h = (int(fi[i]) << 16) | (int(fi[i + j]) << 6) | int(dt)
                out.append((h, int(ti[i])))
    return out


def audio_fingerprint(path: Path) -> dict:
    y = decode_audio(path, sr=8000)
    if y is None or len(y) < 8000:
        return {}
    lm = audio_landmarks(y, 8000)
    return {"landmarks": lm[:20000], "duration": len(y) / 8000}


def fingerprint_media(path: Path, modality: str) -> dict:
    if modality == "image":
        return {"image": image_fingerprint(load_image(path))}
    if modality == "video":
        fp = {"video": video_fingerprint(path)}
        a = audio_fingerprint(path)
        if a:
            fp["audio"] = a
        return fp
    if modality == "audio":
        return {"audio": audio_fingerprint(path)}
    return {}


def representative_image(path: Path, modality: str) -> Image.Image | None:
    if modality == "image":
        return load_image(path).convert("RGB")
    if modality == "video":
        fr = sample_frames(path, max_frames=3)
        return fr[len(fr) // 2][1] if fr else None
    return None
