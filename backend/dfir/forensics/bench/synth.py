"""Synthetic ground-truth generators for validating the forensic modules.

Everything here is SIMULATED, with the truth known by construction:

  * ``Sensor``      a camera with its own fixed multiplicative noise pattern (PRNU), shot + read noise, a Bayer colour
                    filter array with bilinear demosaicing, optional denoising/sharpening, gamma and JPEG.
  * ``scene``       natural-looking test scenes (1/f fields, shapes with soft edges, fine texture).
  * transformations resize / JPEG / crop / blur / rotate ... (what happens to a photo when it is shared).
  * tampering       splice (with or without resampling) and copy-move, with the ground-truth mask.
  * ``speech_like`` audio with known hum, reverberation time, clipping and noise floor.
  * ``make_video``  a pan with a known rolling-shutter read-out fraction, frame drops and duplicates.

Results measured on this data show the methods WORK AS DESIGNED. They do not show how they behave on real cameras,
real editing software or real platforms; the validation tier of every module says so.
"""
from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image
from scipy import signal as sps


# ---------------------------------------------------------------------------------------------------- scenes
def _field(rng: np.random.Generator, h: int, w: int, beta: float) -> np.ndarray:
    """Random field with a 1/f^beta amplitude spectrum, zero mean, unit std (half-spectrum, single precision: ~6x faster)."""
    from scipy import fft as sfft
    fy, fx = np.fft.fftfreq(h)[:, None].astype(np.float32), np.fft.rfftfreq(w)[None, :].astype(np.float32)
    f = np.sqrt(fx ** 2 + fy ** 2)
    f[0, 0] = 1.0
    shape = (h, w // 2 + 1)
    spec = (rng.standard_normal(shape, dtype=np.float32) + 1j * rng.standard_normal(shape, dtype=np.float32)) / f ** beta
    spec[0, 0] = 0
    a = sfft.irfft2(spec.astype(np.complex64), s=(h, w), workers=-1)
    return (a / (a.std() + 1e-9)).astype(np.float32)


@lru_cache(maxsize=48)
def _scene_cached(seed: int, h: int, w: int) -> np.ndarray:
    a = _scene(seed, h, w)
    a.setflags(write=False)
    return a


def scene(seed: int, h: int = 512, w: int = 512) -> np.ndarray:
    """Float RGB scene in [0.04, 0.96] (display-referred). Cached; returns a private copy."""
    return _scene_cached(seed, h, w).copy()


def _scene(seed: int, h: int, w: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    luma = 0.55 * _field(rng, h, w, 1.25) + 0.12 * _field(rng, h, w, 0.7)
    chroma = [0.25 * _field(rng, h, w, 1.5) for _ in range(3)]
    img = np.stack([luma + c for c in chroma], axis=-1)
    canvas = np.zeros((h, w, 3), np.float32)
    mask = np.zeros((h, w), np.float32)
    for _ in range(int(rng.integers(8, 16))):  # shapes with soft edges give the edge statistics real photos have
        col = rng.uniform(-1.2, 1.2, 3).astype(np.float32)
        layer = np.zeros((h, w), np.float32)
        cx, cy = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < 0.5:
            cv2.ellipse(layer, (cx, cy), (int(rng.integers(20, w // 3)), int(rng.integers(20, h // 3))), float(rng.integers(0, 180)), 0, 360, 1.0, -1)
        else:
            x0, y0 = cx, cy
            cv2.rectangle(layer, (x0, y0), (min(w - 1, x0 + int(rng.integers(30, w // 2))), min(h - 1, y0 + int(rng.integers(30, h // 2)))), 1.0, -1)
        layer = cv2.GaussianBlur(layer, (0, 0), 1.1)
        ys, xs = np.nonzero(layer > 1e-3)
        if len(ys) == 0:
            continue
        sl = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))  # composite inside the shape's bounding box only
        a = layer[sl][..., None]
        canvas[sl] = canvas[sl] * (1 - a) + col[None, None, :] * a
        mask[sl] = np.maximum(mask[sl], layer[sl])
    img = img * (1 - 0.7 * mask[..., None]) + canvas * 0.7
    img = img + 0.05 * _field(rng, h, w, 0.5)[..., None]  # fine texture
    img = (img - img.min()) / (img.max() - img.min() + 1e-9)
    return (0.04 + 0.92 * img).astype(np.float32)


# ---------------------------------------------------------------------------------------------------- sensor
KG = np.array([[0, 1, 0], [1, 4, 1], [0, 1, 0]], np.float32) / 4
KRB = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], np.float32) / 4


def mosaic_demosaic(rgb: np.ndarray, pattern_offset: tuple[int, int] = (0, 0)) -> np.ndarray:
    """RGGB Bayer sampling followed by bilinear demosaicing (the classic source of CFA correlations)."""
    h, w, _ = rgb.shape
    yy, xx = np.indices((h, w))
    oy, ox = pattern_offset
    py, px = (yy + oy) % 2, (xx + ox) % 2
    mr, mb = ((py == 0) & (px == 0)).astype(np.float32), ((py == 1) & (px == 1)).astype(np.float32)
    mg = 1 - mr - mb
    out = np.empty_like(rgb)
    out[..., 0] = cv2.filter2D(rgb[..., 0] * mr, -1, KRB, borderType=cv2.BORDER_REFLECT)
    out[..., 1] = cv2.filter2D(rgb[..., 1] * mg, -1, KG, borderType=cv2.BORDER_REFLECT)
    out[..., 2] = cv2.filter2D(rgb[..., 2] * mb, -1, KRB, borderType=cv2.BORDER_REFLECT)
    return out


@dataclass
class Sensor:
    seed: int
    h: int = 512
    w: int = 512
    prnu_sigma: float = 0.02
    shot_gain: float = 0.0009
    read_noise: float = 0.004
    denoise: float = 0.0  # gaussian sigma applied after demosaicing (0 = none)
    sharpen: float = 0.0  # unsharp-mask amount
    cfa: bool = True
    gamma: float = 2.2
    k: np.ndarray = field(init=False, repr=False)

    def __post_init__(self):
        rng = np.random.default_rng(10_000 + self.seed)
        self.k = (rng.standard_normal((self.h, self.w)) * self.prnu_sigma).astype(np.float32)

    def capture(self, sc: np.ndarray, noise_seed: int, jpeg_q: int | None = 95, subsampling: int = 2) -> np.ndarray | bytes:
        """sc: display-referred RGB scene. Returns JPEG bytes (or a uint8 array when jpeg_q is None)."""
        rng = np.random.default_rng(noise_seed)
        lin = np.power(sc, self.gamma)
        sig = lin * (1.0 + self.k[..., None])
        noise = np.sqrt(self.shot_gain * np.maximum(sig, 0) + self.read_noise ** 2) * rng.standard_normal(sig.shape).astype(np.float32)
        raw = np.clip(sig + noise, 0, 1)
        img = mosaic_demosaic(raw) if self.cfa else raw
        if self.denoise > 0:
            img = cv2.GaussianBlur(img, (0, 0), self.denoise)
        if self.sharpen > 0:
            img = img + self.sharpen * (img - cv2.GaussianBlur(img, (0, 0), 1.2))
        out = (np.clip(img, 0, 1) ** (1 / self.gamma) * 255).round().astype(np.uint8)
        if jpeg_q is None:
            return out
        return to_jpeg(out, jpeg_q, subsampling)


def to_jpeg(arr: np.ndarray, q: int, subsampling: int = 2) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=q, subsampling=subsampling)
    return buf.getvalue()


def decode(b: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(b)).convert("RGB"))


# ---------------------------------------------------------------------------------------------------- transformations
def t_jpeg(q: int):
    return lambda a: decode(to_jpeg(a, q))


def t_resize(scale: float):
    def f(a):
        h, w = a.shape[:2]
        return cv2.resize(a, (max(8, int(round(w * scale))), max(8, int(round(h * scale)))), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    return f


def t_crop(frac: float):
    def f(a):
        h, w = a.shape[:2]
        ch, cw = int(h * frac), int(w * frac)
        y, x = (h - ch) // 2 + 3, (w - cw) // 2 + 5
        return a[y:y + ch, x:x + cw]
    return f


def t_blur(sigma: float):
    return lambda a: cv2.GaussianBlur(a, (0, 0), sigma)


def t_rot90(a):
    return np.ascontiguousarray(np.rot90(a))


def t_brightness(gain: float = 1.15, off: float = -8):
    return lambda a: np.clip(a.astype(np.float32) * gain + off, 0, 255).astype(np.uint8)


def t_social(a):
    """Roughly what a messaging app does: downscale, then JPEG at a moderate quality."""
    return decode(to_jpeg(t_resize(0.5)(a), 70))


def t_screenshot(a):
    """A screenshot of the picture shown at 75 %: resample, then lossless (PNG-like) capture, then JPEG share."""
    return decode(to_jpeg(t_resize(0.75)(a), 82))


TRANSFORMS = {
    "none": lambda a: a,
    "jpeg_q95": t_jpeg(95), "jpeg_q85": t_jpeg(85), "jpeg_q70": t_jpeg(70), "jpeg_q50": t_jpeg(50),
    "resize_0.9": t_resize(0.9), "resize_0.5": t_resize(0.5), "crop_90pct": t_crop(0.9),
    "rotate_90": t_rot90, "blur_1.0": t_blur(1.0), "brightness_contrast": t_brightness(),
    "messaging_app": t_social, "screenshot": t_screenshot,
}


# ---------------------------------------------------------------------------------------------------- tampering
def splice(base: np.ndarray, donor: np.ndarray, box: tuple[int, int, int, int], rescale: float | None = None,
           feather: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Paste a patch of ``donor`` into ``base`` inside box (x, y, w, h). Optionally rescale the patch first (the usual
    manual-editing step). Returns (image, ground-truth mask)."""
    x, y, w, h = box
    sw, sh = (int(w / rescale), int(h / rescale)) if rescale else (w, h)
    patch = donor[:sh, :sw] if donor.shape[0] >= sh and donor.shape[1] >= sw else donor
    patch = cv2.resize(patch, (w, h), interpolation=cv2.INTER_CUBIC if rescale else cv2.INTER_LINEAR)
    out = base.copy()
    m = np.zeros(base.shape[:2], np.float32)
    m[y:y + h, x:x + w] = 1
    soft = cv2.GaussianBlur(m, (0, 0), feather) if feather > 0 else m
    out = (out * (1 - soft[..., None]) + np.pad(patch, ((y, base.shape[0] - y - h), (x, base.shape[1] - x - w), (0, 0)))
           * soft[..., None]).round().astype(np.uint8)
    return out, m > 0.5


def copy_move(base: np.ndarray, src: tuple[int, int, int, int], dst_xy: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    x, y, w, h = src
    dx, dy = dst_xy
    out = base.copy()
    out[dy:dy + h, dx:dx + w] = base[y:y + h, x:x + w]
    m = np.zeros(base.shape[:2], bool)
    m[dy:dy + h, dx:dx + w] = True
    return out, m


# ---------------------------------------------------------------------------------------------------- audio
def speech_like(seconds: float, sr: int, seed: int, f0: float = 130.0) -> np.ndarray:
    """Voiced bursts (glottal pulses through formant resonators) separated by pauses. Peak-normalised to 0.5."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * sr), np.float32)
    t = 0.0
    while t < seconds - 0.4:
        dur = float(rng.uniform(0.18, 0.5))
        n = int(dur * sr)
        pitch = f0 * float(rng.uniform(0.85, 1.25))
        ph = np.cumsum(np.full(n, pitch / sr))
        src = np.zeros(n)
        src[np.diff(np.floor(ph), prepend=0) > 0] = 1.0
        y = src
        for fc, bw in ((rng.uniform(450, 800), 90), (rng.uniform(1100, 1900), 120), (rng.uniform(2300, 3200), 160)):
            r = np.exp(-np.pi * bw / sr)
            th = 2 * np.pi * fc / sr
            y = sps.lfilter([1 - r], [1, -2 * r * np.cos(th), r * r], y)
        env = np.hanning(n) ** 0.6
        i = int(t * sr)
        out[i:i + n] += (y * env)[: len(out) - i].astype(np.float32)
        t += dur + float(rng.uniform(0.15, 0.5))
    return (out / (np.abs(out).max() + 1e-9) * 0.5).astype(np.float32)


def add_reverb(x: np.ndarray, sr: int, rt60: float, seed: int = 0, wet: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(min(rt60 * 1.5, 2.5) * sr)
    t = np.arange(n) / sr
    ir = rng.standard_normal(n) * np.exp(-6.9078 * t / rt60)
    ir[0] = 1.0
    ir = ir / np.sqrt(np.sum(ir ** 2))
    y = sps.fftconvolve(x, ir)[: len(x)]
    out = (1 - wet) * x + wet * y
    return (out / (np.abs(out).max() + 1e-9) * 0.5).astype(np.float32)


def add_hum(x: np.ndarray, sr: int, f: float = 50.0, level_db: float = -40.0, harmonics: int = 4) -> np.ndarray:
    t = np.arange(len(x)) / sr
    amp = 0.5 * 10 ** (level_db / 20)
    h = sum(amp / (k ** 0.8) * np.sin(2 * np.pi * f * k * t + k) for k in range(1, harmonics + 1))
    return (x + h).astype(np.float32)


def add_noise_floor(x: np.ndarray, sr: int, level_db: float = -55.0, seed: int = 0, tilt: float = 1.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = rng.standard_normal(len(x))
    spec = np.fft.rfft(n)
    f = np.maximum(np.fft.rfftfreq(len(x), 1 / sr), 20.0)
    spec = spec / f ** (tilt / 2)
    n = np.fft.irfft(spec, len(x))
    n = n / (n.std() + 1e-12) * 10 ** (level_db / 20)
    return (x + n).astype(np.float32)


def clip(x: np.ndarray, gain: float) -> np.ndarray:
    return np.clip(x * gain, -1.0, 1.0).astype(np.float32)


def write_wav(path, x: np.ndarray, sr: int) -> None:
    import soundfile as sf
    sf.write(str(path), x, sr, subtype="PCM_16")


# ---------------------------------------------------------------------------------------------------- video
def _world(seed: int, h: int, w: int) -> np.ndarray:
    """A wide static scene made of vertical bars and blobs (so a pan gives clear vertical edges)."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 0.5, np.float32)
    x = 0
    while x < w:
        bw = int(rng.integers(6, 28))
        img[:, x:x + bw] = rng.uniform(0.1, 0.9)
        x += bw
    img += 0.25 * _field(rng, h, w, 1.0)
    img = cv2.GaussianBlur(img, (0, 0), 0.9)
    return np.clip(img, 0, 1)


def make_video(path, *, seconds: float = 4.0, fps: int = 25, size: tuple[int, int] = (320, 240), readout: float = 0.0,
               amplitude: float = 14.0, hz: float = 0.9, seed: int = 1, drop: list[int] | None = None,
               duplicate: list[int] | None = None, crf: int = 18, audio: np.ndarray | None = None, audio_sr: int = 16000,
               frame_hook=None) -> dict:
    """Render a horizontally panning scene. ``readout`` is the rolling-shutter read-out time as a fraction of the frame
    period (0 = global shutter). ``drop`` removes frame indices; ``duplicate`` repeats them. Returns the ground truth."""
    w, h = size
    world = _world(seed, h, w + 200)
    n = int(seconds * fps)
    rng = np.random.default_rng(seed + 7)
    jitter = np.cumsum(rng.standard_normal(n * 4)) * 0.4
    frames = []
    ys = np.arange(h, dtype=np.float32)

    def cam_x(t):  # camera position in pixels at time t (seconds), continuous so each row can sample its own instant
        return 100 + amplitude * np.sin(2 * np.pi * hz * t) + 0.6 * amplitude * np.sin(2 * np.pi * 2.3 * t + 1.0) + (np.interp(t * fps * 4, np.arange(len(jitter)), jitter) * 0.5 if amplitude else 0.0)

    for i in range(n):
        t_rows = i / fps + ys / h * readout / fps
        xs = cam_x(t_rows).astype(np.float32)
        map_x = (np.arange(w, dtype=np.float32)[None, :] + xs[:, None]).astype(np.float32)
        map_y = np.repeat(ys[:, None], w, axis=1)
        fr = cv2.remap(world, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        fr8 = (np.clip(fr, 0, 1) * 255).astype(np.uint8)
        rgb = np.stack([fr8, np.roll(fr8, 1, axis=0), fr8], axis=-1)
        if frame_hook:
            rgb = frame_hook(i, rgb)
        frames.append(rgb)
    order = list(range(n))
    if duplicate:
        for d in sorted(duplicate, reverse=True):
            order.insert(d + 1, d)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    tmp_audio = None
    if audio is not None:
        tmp_audio = str(path) + ".wav"
        write_wav(tmp_audio, audio, audio_sr)
        cmd += ["-i", tmp_audio]
    if drop:  # dropped frames leave a GAP in the timestamps (select keeps each frame's own pts; vfr output preserves it)
        sel = "+".join("eq(n\\,%d)" % d for d in drop)  # a literal backslash escapes the comma inside the ffmpeg filter
        cmd += ["-vf", f"select='not({sel})'", "-fps_mode", "vfr"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-g", "25"]
    if audio is not None:
        cmd += ["-c:a", "aac", "-b:a", "96k", "-shortest"]
    cmd += [str(path)]
    p = subprocess.run(cmd, input=b"".join(frames[k].tobytes() for k in order), capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="ignore")[:300])
    if tmp_audio:
        import os
        os.unlink(tmp_audio)
    return {"readout": readout, "fps": fps, "frames": len(order) - len(drop or []), "dropped": drop or [], "duplicated": duplicate or []}
