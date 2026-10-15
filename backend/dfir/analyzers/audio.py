"""Audio forensics: spectrogram, bandwidth/sample-rate history, pause regularity, optional models."""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image
from scipy import signal

from ..core.claims import NotRun, Polarity, Status, Strength
from .base import Analyzer, Ctx, register
from .media_io import decode_audio, ffmpeg_bin


def spectrogram_image(y: np.ndarray, sr: int) -> tuple[Image.Image, np.ndarray, np.ndarray, np.ndarray]:
    f, t, S = signal.spectrogram(y, fs=sr, nperseg=1024, noverlap=768)
    db = 10 * np.log10(S + 1e-12)
    norm = np.clip((db - np.percentile(db, 5)) / (np.percentile(db, 99.5) - np.percentile(db, 5) + 1e-9), 0, 1)
    img = (np.flipud(norm) * 255).astype(np.uint8)
    img = cv2.resize(img, (min(2000, max(400, img.shape[1])), 300))
    col = cv2.cvtColor(cv2.applyColorMap(img, cv2.COLORMAP_MAGMA), cv2.COLOR_BGR2RGB)
    return Image.fromarray(col), f, t, db


def effective_bandwidth(f: np.ndarray, db: np.ndarray) -> float:
    """Highest frequency where long-term energy stays within 60 dB of the peak."""
    prof = db.mean(axis=1)
    thresh = prof.max() - 60
    idx = np.where(prof > thresh)[0]
    return float(f[idx[-1]]) if len(idx) else 0.0


@register
class AudioAnalyzer(Analyzer):
    name = "audio_forensics"
    version = "1.0"
    modalities = ("audio", "video")
    title = "Audio spectrum & voice-synthesis indicators"
    order = 85

    def available(self):
        if not ffmpeg_bin():
            return NotRun(component=self.name, reason="ffmpeg not found", how_to_enable="Install FFmpeg")
        return None

    def run(self, ctx: Ctx):
        sr = 44100
        y = decode_audio(ctx.path, sr=sr)
        if y is None or len(y) < sr * 0.5:
            if ctx.media.modality == "audio":
                ctx.claim("audio", "No decodable audio stream", Status.UNKNOWN)
            return
        ctx.shared["audio"] = (y, sr)
        img, f, t, db = spectrogram_image(y, sr)
        art = ctx.artifact("spectrogram", "Spectrogram (0–22 kHz)", img, data={"duration": len(y) / sr})
        bw = effective_bandwidth(f, db)
        probe = ctx.shared.get("ffprobe") or {}
        native_sr = next((int(s.get("sample_rate", 0)) for s in probe.get("streams", []) if s.get("codec_type") == "audio"), 0)
        hint = ""
        if 7000 < bw < 8500:
            hint = "consistent with 16 kHz-sampled speech (common in telephony and many TTS/voice-clone outputs)"
        elif 10500 < bw < 12500:
            hint = "consistent with 22.05/24 kHz-sampled audio (a common neural TTS output rate)"
        elif bw < 4200:
            hint = "consistent with narrowband telephone audio"
        ctx.claim("audio", f"Effective audio bandwidth ≈ {bw / 1000:.1f} kHz (container sample rate "
                  f"{native_sr or 'unknown'} Hz)" + (f"; {hint}" if hint else ""), Status.INFERRED,
                  strength=Strength.WEAK, polarity=Polarity.NEUTRAL,
                  why="Audio generated or processed at a lower sample rate and then resampled keeps a hard ceiling in "
                      "its spectrum — a trace of its processing history.",
                  limitations="Phone calls, voice notes (WhatsApp uses Opus) and many codecs also band-limit audio. "
                              "This describes processing history, not synthesis.",
                  evidence=[art.sha256], data={"bandwidth_hz": bw, "native_sr": native_sr})
        # pause structure: neural TTS often produces very regular inter-phrase pauses
        frame = int(0.02 * sr)
        energy = np.sqrt(np.convolve(y ** 2, np.ones(frame) / frame, mode="valid")[::frame])
        thr = np.percentile(energy, 20) * 1.5 + 1e-6
        silent = energy < thr
        runs, cur = [], 0
        for s in silent:
            if s:
                cur += 1
            elif cur:
                runs.append(cur)
                cur = 0
        pauses = [r * 0.02 for r in runs if r * 0.02 >= 0.15]
        if len(pauses) >= 5:
            cv = float(np.std(pauses) / (np.mean(pauses) + 1e-6))
            regular = cv < 0.25
            ctx.claim("audio", f"{len(pauses)} pauses; pause-length variability {cv:.2f} "
                      f"({'unusually regular' if regular else 'natural range'})", Status.INFERRED, strength=Strength.WEAK,
                      polarity=Polarity.SUPPORTS_MANIPULATION if regular else Polarity.NEUTRAL,
                      why="Human speech has irregular pauses; some synthetic speech is metronomic.",
                      limitations="Reading from a script and edited audio can also be regular. Weak indicator only.",
                      data={"pauses": pauses[:50], "cv": cv})
        # Learned anti-spoofing / audio watermark models are optional
        ctx.claim("audio", "Learned voice-clone detection not run (no anti-spoofing model installed)", Status.UNKNOWN,
                  limitations="Install an anti-spoofing model (e.g. AASIST) or use a commercial API with consent. "
                              "Audio watermarks (e.g. Meta AudioSeal) were not checked.",
                  confirm="Compare against verified recordings of the purported speaker (forensic voice comparison by an expert).")
