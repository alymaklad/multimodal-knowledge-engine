import json
import subprocess
from pathlib import Path

import pytest

from app.services.video import FFmpegVideoProcessor, VideoProcessingError


def test_probe_extracts_audio_and_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    probe_payload = {
        "format": {"duration": "12.5"},
        "streams": [
            {"codec_type": "video", "width": 1080, "height": 1920},
            {"codec_type": "audio"},
        ],
    }

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(probe_payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    metadata = FFmpegVideoProcessor().probe("demo.mp4")
    assert metadata.duration_seconds == 12.5
    assert metadata.has_audio is True
    assert (metadata.width, metadata.height) == (1080, 1920)


def test_prepare_rejects_missing_media(tmp_path: Path) -> None:
    with pytest.raises(VideoProcessingError, match="does not exist"):
        FFmpegVideoProcessor().prepare(tmp_path / "missing.mp4", tmp_path / "output")
