"""Speech transcription (faster-whisper, optional)."""
from __future__ import annotations

from ..core.claims import NotRun, Status, Strength
from ..core.config import MODELS_DIR
from .base import Analyzer, Ctx, register
from .media_io import decode_audio

_model = None
_device = "cpu"


def load_model():
    """faster-whisper small: GPU (int8 weights, fp32 compute — fp16 is slow on Turing TU117) or CPU int8."""
    global _device
    from faster_whisper import WhisperModel
    try:
        import torch  # noqa: F401 — loads the CUDA runtime DLLs ctranslate2 reuses on Windows
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            m = WhisperModel("small", device="cuda", compute_type="int8_float32",
                             download_root=str(MODELS_DIR / "whisper"), local_files_only=True)
            _device = "cuda"
            return m
    except Exception:  # noqa: BLE001
        pass
    _device = "cpu"
    return WhisperModel("small", device="cpu", compute_type="int8",
                        download_root=str(MODELS_DIR / "whisper"), local_files_only=True)


def runtime_info() -> dict:
    return {"loaded": _model is not None, "device": _device if _model is not None else None}


@register
class ASRAnalyzer(Analyzer):
    name = "asr"
    version = "1.0"
    modalities = ("audio", "video")
    title = "Speech transcription (Whisper)"
    order = 90

    def available(self):
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return NotRun(component=self.name, reason="faster-whisper not installed",
                          how_to_enable="uv sync --extra asr, then: uv run python ../scripts/setup_models.py whisper")
        if not (MODELS_DIR / "whisper" / "ready").exists():
            return NotRun(component=self.name, reason="Whisper model not downloaded",
                          how_to_enable="uv run python ../scripts/setup_models.py whisper")
        return None

    def run(self, ctx: Ctx):
        global _model
        y = decode_audio(ctx.path, sr=16000)
        if y is None or len(y) < 16000:
            return
        if _model is None:
            from ..core.gpu import models

            def _build(dev):
                return load_model()

            def _drop(_m):
                global _model
                _model = None
            _model, _ = models.get("whisper", _build, est_mb=600, unload=_drop)
        segs, info = _model.transcribe(y, vad_filter=True)
        segments = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segs]
        text = " ".join(s["text"] for s in segments).strip()
        ctx.shared["transcript"] = segments
        if not text:
            ctx.claim("asr", "No speech detected in the audio track", Status.UNKNOWN,
                      limitations="Voice-activity detection found no speech; music, tones or very quiet speech can be missed.",
                      data={"language_guess": info.language, "device": _device})
            return
        art = ctx.artifact("transcript", f"Transcript ({info.language}, p={info.language_probability:.2f})",
                           __import__("json").dumps({"language": info.language, "segments": segments},
                                                    ensure_ascii=False, indent=2).encode(), mime="application/json")
        ctx.claim("asr", f"Speech transcribed ({info.language}): “{text[:300]}{'…' if len(text) > 300 else ''}”",
                  Status.INFERRED, strength=Strength.WEAK, evidence=[art.sha256],
                  limitations="Automatic transcription can mishear names and code-mixed speech; verify by listening.",
                  data={"language": info.language, "chars": len(text), "device": _device})
