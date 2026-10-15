"""Video decomposition: frame sampling, per-frame detectors, temporal consistency."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from ..core.claims import NotRun, Polarity, Status, Strength
from .base import Analyzer, Ctx, register
from .detectors_local import score_to_evidence, ufd_available, ufd_scores
from .faces import detect_faces
from .image_forensics import ela, spectral_peaks
from .media_io import ffmpeg_bin, sample_frames, video_info


def frame_strip(frames: list[tuple[float, Image.Image]], h: int = 90) -> Image.Image:
    thumbs = []
    for _, im in frames:
        r = h / im.height
        thumbs.append(im.resize((max(1, int(im.width * r)), h)))
    w = sum(t.width for t in thumbs) or 1
    strip = Image.new("RGB", (w, h + 16), (20, 20, 24))
    x = 0
    d = ImageDraw.Draw(strip)
    for (t, _), th in zip(frames, thumbs):
        strip.paste(th, (x, 0))
        d.text((x + 3, h + 2), f"{t:.1f}s", fill=(200, 200, 200))
        x += th.width
    return strip


@register
class VideoAnalyzer(Analyzer):
    name = "video_temporal"
    version = "1.0"
    modalities = ("video",)
    title = "Video frames & temporal consistency"
    order = 45

    def available(self):
        if not ffmpeg_bin():
            return NotRun(component=self.name, reason="ffmpeg not found", how_to_enable="Install FFmpeg")
        return None

    def run(self, ctx: Ctx):
        info = video_info(ctx.path)
        frames = sample_frames(ctx.path, max_frames=24)
        ctx.shared["frames"] = frames
        ctx.shared["video_info"] = {k: v for k, v in info.items() if k != "probe"}
        if not frames:
            ctx.claim("video", "Could not decode frames from this video", Status.UNKNOWN)
            return
        strip = ctx.artifact("frames", f"{len(frames)} sampled frames", frame_strip(frames),
                             data={"times": [t for t, _ in frames], "fps": info["fps"], "duration": info["duration"]})
        ctx.claim("video", f"Video: {info['width']}×{info['height']}, {info['fps']:.2f} fps, {info['duration']:.1f}s, "
                  f"codec {info['codec']}, audio {'present' if info['has_audio'] else 'absent'}", Status.VERIFIED,
                  data=ctx.shared["video_info"], evidence=[strip.sha256])

        series: dict[str, list] = {"t": [t for t, _ in frames]}
        # per-frame learned detector
        ok, reason = ufd_available()
        if ok:
            scores, spreads, sha = ufd_scores([im for _, im in frames])
            series["ufd"] = scores
            med = float(np.median(scores))
            strength, pol, desc = score_to_evidence(med, float(np.median(spreads)))
            hi = [round(t, 1) for t, s in zip(series["t"], scores) if s >= 0.9]
            ctx.claim("detector", f"UniversalFakeDetect across {len(scores)} frames: median frame {desc} "
                      f"(median output {med:.2f}; {len(hi)} frame(s) ≥ 0.90)", Status.INFERRED,
                      strength=strength, polarity=pol, model_sha=sha,
                      why="Image-level detector applied to sampled frames.",
                      limitations="Frame-level image detectors miss temporal artefacts and are weakened by video "
                                  "compression. Trained mainly on GAN images, not face-swap video.",
                      data={"median": med, "flagged_times": hi})
        else:
            series["ufd"] = None
        # per-frame spectral + ELA level (temporal inconsistency of re-encoded regions)
        series["ela"] = [float(ela(np.asarray(im)).mean()) for _, im in frames]
        series["spectral"] = [spectral_peaks(np.asarray(im))[1] for _, im in frames]

        faces_series = []
        for t, im in frames:
            fs = detect_faces(np.asarray(im))
            if fs is None:
                faces_series = None
                break
            faces_series.append(fs)
        if faces_series is not None:
            counts = [len(f) for f in faces_series]
            series["faces"] = counts
            # face box jitter relative to face size between consecutive sampled frames
            jit = []
            for a, b in zip(faces_series, faces_series[1:]):
                if a and b:
                    ba, bb = a[0]["box"], b[0]["box"]
                    size = max(1, (ba[2] + bb[2]) / 2)
                    jit.append(abs((ba[2] - bb[2]) / size))
            scale_jitter = float(np.median(jit)) if jit else None
            if scale_jitter is not None:
                odd = scale_jitter > 0.25
                ctx.claim("temporal", f"Face size changes between sampled frames: median {scale_jitter:.0%} "
                          f"({'unusually unstable' if odd else 'stable'})", Status.INFERRED, strength=Strength.WEAK,
                          polarity=Polarity.SUPPORTS_MANIPULATION if odd else Polarity.NEUTRAL,
                          why="Face-swap pipelines re-detect and re-blend the face per frame, which can make the face "
                              "region flicker in size relative to the head.",
                          limitations="Frames are sampled sparsely; camera zoom and fast motion produce the same effect.",
                          data={"median_scale_jitter": scale_jitter})
        ela_cv = float(np.std(series["ela"]) / (np.mean(series["ela"]) + 1e-6))
        ctx.claim("temporal", f"Compression-error level across frames varies by {ela_cv:.0%} (coefficient of variation)",
                  Status.INFERRED, strength=Strength.WEAK, polarity=Polarity.NEUTRAL,
                  limitations="Scene changes and keyframe intervals dominate this measure; shown for context in the "
                              "frame chart.", data={"ela_cv": ela_cv})
        ctx.claim("temporal", "Lip-sync consistency not assessed", Status.UNKNOWN,
                  limitations="Requires a dense facial-landmark or audio-visual sync model, which is not installed. "
                              "A lip-sync mismatch check would strengthen or weaken voice-swap hypotheses.",
                  confirm="Expert review of mouth movement against audio, or an audio-visual sync model.")
        ctx.artifact("timeseries", "Per-frame signals", __import__("json").dumps(series).encode(),
                     mime="application/json", data={"series": list(series.keys())})
