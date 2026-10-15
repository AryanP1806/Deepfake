"""Classical pixel-level forensics: ELA, noise-residual consistency, spectral periodicity,
JPEG ghost (double-compression). Each yields a heatmap artifact plus a deliberately modest claim."""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

from ..core.claims import Polarity, Status, Strength
from .base import Analyzer, Ctx, register
from .media_io import load_image

MAX_SIDE = 1536


def _prep(ctx: Ctx) -> np.ndarray:
    if "rgb" in ctx.shared:
        return ctx.shared["rgb"]
    im = load_image(ctx.path).convert("RGB")
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    rgb = np.asarray(im)
    ctx.shared["rgb"] = rgb
    return rgb


def _colorize(m: np.ndarray) -> Image.Image:
    m = m.astype(np.float32)
    hi = np.percentile(m, 99.5) or 1.0
    norm = np.clip(m / hi * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(cv2.cvtColor(cv2.applyColorMap(norm, cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB))


def _block_stat(m: np.ndarray, bs: int, fn=np.mean) -> np.ndarray:
    h, w = m.shape[:2]
    hb, wb = h // bs, w // bs
    if hb == 0 or wb == 0:
        return np.array([[fn(m)]])
    v = m[: hb * bs, : wb * bs].reshape(hb, bs, wb, bs)
    return fn(fn(v, axis=3), axis=1) if fn is np.mean else np.apply_over_axes(fn, v, [1, 3]).reshape(hb, wb)


def ela(rgb: np.ndarray, quality: int = 90) -> np.ndarray:
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=quality)
    re = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")).astype(np.int16)
    return np.abs(rgb.astype(np.int16) - re).max(axis=2).astype(np.float32)


def noise_residual(rgb: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    return g - cv2.medianBlur(g.astype(np.uint8), 3).astype(np.float32)


def spectral_peaks(rgb: np.ndarray) -> tuple[np.ndarray, float, list]:
    """Average spectrum of the high-pass residual. Upsampling layers in many generators
    leave periodic peaks at regular frequency offsets. Returns (spectrum, peak_score, peaks)."""
    res = noise_residual(rgb)
    h, w = res.shape
    s = min(h, w, 1024)
    tiles = []
    for y in range(0, h - s + 1, max(1, s)):
        for x in range(0, w - s + 1, max(1, s)):
            t = res[y:y + s, x:x + s]
            tiles.append(np.abs(np.fft.fftshift(np.fft.fft2(t * np.outer(np.hanning(s), np.hanning(s))))))
    spec = np.log1p(np.mean(tiles, axis=0))
    # normalise radially so natural 1/f falloff doesn't dominate
    cy, cx = s // 2, s // 2
    yy, xx = np.indices(spec.shape)
    r = np.hypot(yy - cy, xx - cx).astype(int)
    radial = np.bincount(r.ravel(), spec.ravel()) / np.maximum(1, np.bincount(r.ravel()))
    norm = spec - radial[r]
    mask = r > s * 0.05
    vals = norm[mask]
    thresh = vals.mean() + 6 * vals.std()
    peaks = np.argwhere((norm > thresh) & mask)
    score = float((norm[mask].max() - vals.mean()) / (vals.std() + 1e-6))
    pk = [(int(y - cy), int(x - cx)) for y, x in peaks[:20]]
    return spec, score, pk


def jpeg_ghost(rgb: np.ndarray, qualities=range(50, 100, 5)) -> tuple[list[tuple[int, float]], int | None]:
    curve = []
    for q in qualities:
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, "JPEG", quality=q)
        re = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")).astype(np.float32)
        curve.append((q, float(np.mean((rgb.astype(np.float32) - re) ** 2))))
    # A local minimum strictly below the top quality suggests a previous compression at that quality.
    diffs = [c[1] for c in curve]
    local_min = None
    for i in range(1, len(diffs) - 1):
        if diffs[i] < diffs[i - 1] and diffs[i] < diffs[i + 1] and diffs[i] < 0.8 * diffs[i - 1]:
            local_min = curve[i][0]
    return curve, local_min


@register
class ImageForensics(Analyzer):
    order = 40
    name = "image_forensics"
    version = "1.0"
    modalities = ("image",)
    title = "Pixel forensics (ELA, noise, spectrum, JPEG ghost)"

    def run(self, ctx: Ctx):
        rgb = _prep(ctx)
        self._ela(ctx, rgb)
        self._noise(ctx, rgb)
        self._fft(ctx, rgb)
        if ctx.media.mime == "image/jpeg":
            self._ghost(ctx, rgb)


    def _ela(self, ctx, rgb):
        m = ela(rgb)
        blocks = _block_stat(m, 16)
        med = float(np.median(blocks)) + 1e-3
        p99 = float(np.percentile(blocks, 99))
        ratio = p99 / med
        ctx.shared["ela_blocks"] = blocks
        art = ctx.artifact("ela", "Error Level Analysis (resaved at q90)", _colorize(m),
                           data={"p99_over_median": round(ratio, 2)})
        suspicious = ratio > 6
        ctx.claim("ela",
                  f"ELA: {'localised regions recompress very differently from the rest of the image' if suspicious else 'error levels are broadly uniform'} "
                  f"(p99/median block ratio {ratio:.1f})",
                  Status.INFERRED, strength=Strength.WEAK,
                  polarity=Polarity.SUPPORTS_MANIPULATION if suspicious else Polarity.NEUTRAL,
                  why="Regions pasted or regenerated after the last JPEG save often show a different error level.",
                  limitations="ELA is noisy: high-contrast edges, text, smooth skies and recompression all change error "
                              "levels. Useful for directing attention, not as standalone evidence. Uniform ELA does not "
                              "show authenticity (fully generated images are often uniform).",
                  evidence=[art.sha256], data={"p99_over_median": ratio})

    def _noise(self, ctx, rgb):
        res = noise_residual(rgb)
        blocks = _block_stat(np.abs(res), 32)
        cv = float(np.std(blocks) / (np.mean(blocks) + 1e-6))
        heat = cv2.resize(blocks.astype(np.float32), (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)
        art = ctx.artifact("noise", "Noise-residual level map", _colorize(heat), data={"block_cv": round(cv, 3)})
        inconsistent = cv > 0.9
        ctx.claim("noise",
                  f"Sensor-noise residual is {'inconsistent across regions' if inconsistent else 'broadly consistent'} "
                  f"(block coefficient of variation {cv:.2f})",
                  Status.INFERRED, strength=Strength.WEAK,
                  polarity=Polarity.SUPPORTS_MANIPULATION if inconsistent else Polarity.NEUTRAL,
                  why="Camera images carry roughly uniform sensor noise; spliced or regenerated regions often differ.",
                  limitations="Texture, exposure, denoising and blur all change local noise. Requires visual review of "
                              "the map; does not identify a generator.",
                  evidence=[art.sha256], data={"block_cv": cv})

    def _fft(self, ctx, rgb):
        spec, score, peaks = spectral_peaks(rgb)
        art = ctx.artifact("fft", "Residual frequency spectrum", _colorize(spec - spec.min()),
                           data={"peak_score": round(score, 2), "peaks": peaks[:10]})
        periodic = score > 12 and len(peaks) >= 2
        ctx.claim("spectrum",
                  f"Frequency spectrum {'shows isolated periodic peaks typical of upsampling layers' if periodic else 'shows no strong periodic peaks'} "
                  f"(peak score {score:.1f})",
                  Status.INFERRED, strength=Strength.WEAK if not periodic else Strength.MODERATE,
                  polarity=Polarity.SUPPORTS_MANIPULATION if periodic else Polarity.NEUTRAL,
                  why="Transposed-convolution / upsampling in many GANs and some diffusion decoders leaves grid-like "
                      "spectral peaks.",
                  limitations="Resizing, JPEG 8×8 blocking, screen capture moiré and halftone patterns also create peaks. "
                              "Modern diffusion models often leave none.",
                  evidence=[art.sha256], data={"peak_score": score, "n_peaks": len(peaks)})

    def _ghost(self, ctx, rgb):
        curve, qmin = jpeg_ghost(rgb)
        current_q = ((ctx.shared.get("metadata_details") or {}).get("jpeg") or {}).get("quality_estimate")
        if qmin and current_q and abs(qmin - current_q) <= 5:
            qmin = None  # the dip at the file's own quality is expected, not evidence of an earlier save
        if qmin:
            ctx.claim("jpeg_ghost", f"JPEG ghost: error curve dips at quality ≈{qmin}, suggesting an earlier compression "
                      "at that quality (double compression)", Status.INFERRED, strength=Strength.WEAK,
                      why="Images compressed twice keep a trace of the first quantisation.",
                      limitations="Double compression is normal for anything reshared through apps; it indicates "
                                  "re-saving, not manipulation.",
                      data={"curve": curve, "prior_quality": qmin})
        else:
            ctx.claim("jpeg_ghost", "No clear double-compression signature", Status.UNKNOWN,
                      limitations="Inconclusive either way.", data={"curve": curve})
