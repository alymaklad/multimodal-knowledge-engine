from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.core.gpu import prefer_torch_cudnn
from app.providers.base import SpeechProvider, TranscriptionResult


@lru_cache(maxsize=2)
def _load_whisper(model_name: str, device: str, compute_type: str):
    """Cached per process: the worker builds a speech provider for every analysed Reel."""
    # CTranslate2 brings its own CUDA libraries; torch must claim cuDNN first. See app/core/gpu.py.
    prefer_torch_cudnn()
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Install faster-whisper to enable transcription, or set SPEECH_PROVIDER=none"
        ) from error
    return WhisperModel(model_name, device=device, compute_type=compute_type)


class FasterWhisperSpeechProvider(SpeechProvider):
    """Whisper transcription with word-level timing preserved as segments.

    Segments matter downstream: OCR observations already carry frame timestamps, so a timed
    transcript lets evidence from both modalities be aligned to the same moment in the Reel.
    """

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.whisper_model
        self._model = _load_whisper(
            self._model_name, settings.whisper_device, settings.whisper_compute_type
        )

    def transcribe(self, audio_path: Path) -> TranscriptionResult:  # pragma: no cover - needs weights
        segments, info = self._model.transcribe(str(audio_path), vad_filter=True)
        collected = [
            {"start": float(segment.start), "end": float(segment.end), "text": segment.text.strip()}
            for segment in segments
        ]
        return TranscriptionResult(
            text=" ".join(segment["text"] for segment in collected).strip(),
            language=getattr(info, "language", None),
            segments=collected,
            model=f"faster-whisper-{self._model_name}",
        )


def get_speech_provider() -> SpeechProvider | None:
    """Return the configured provider, or `None` when transcription is intentionally disabled."""
    name = get_settings().speech_provider.lower()
    if name in {"none", "", "disabled"}:
        return None
    if name in {"faster_whisper", "faster-whisper", "whisper"}:
        return FasterWhisperSpeechProvider()
    raise ValueError(f"Unsupported speech provider: {name}")
