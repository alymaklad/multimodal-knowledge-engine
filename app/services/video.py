"""FFmpeg-based media preparation for the multimodal processing pipeline."""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings


class VideoProcessingError(RuntimeError):
    """Raised when ffmpeg or ffprobe cannot prepare user-provided media."""


@dataclass(frozen=True)
class VideoMetadata:
    duration_seconds: float | None
    has_audio: bool
    width: int | None
    height: int | None


@dataclass(frozen=True)
class PreparedMedia:
    audio_path: str | None
    frame_paths: list[str]
    metadata: VideoMetadata


def resolve_binary(name: str, *, sibling_of: str | None = None) -> str:
    """Turn a binary name into an absolute path while we can still report a good error.

    A bare name is resolved by the OS at `subprocess` time, so a process whose PATH differs from the
    shell's fails deep inside the pipeline with `FileNotFoundError` — which is what happened to a
    real upload: ffprobe was installed and on PATH, but the server process could not see it.
    Resolving up front makes the pipeline independent of the caller's environment, and lets
    `/api/capabilities` report the problem before anyone uploads a video.
    """
    if Path(name).is_absolute():
        return name
    found = shutil.which(name)
    if found:
        return found
    # ffmpeg and ffprobe ship together, so a located ffmpeg tells us where its sibling is.
    if sibling_of and Path(sibling_of).is_absolute():
        candidate = Path(sibling_of).with_name(Path(name).stem + Path(sibling_of).suffix)
        if candidate.exists():
            return str(candidate)
    return name  # Leave it for subprocess, which raises the actionable error above.


def media_tools_status() -> dict[str, str | None]:
    """Where the media binaries actually resolve, for the capabilities endpoint."""
    settings = get_settings()
    ffmpeg = resolve_binary(settings.ffmpeg_binary)
    ffprobe = resolve_binary(settings.ffprobe_binary, sibling_of=ffmpeg)
    return {
        "ffmpeg": ffmpeg if Path(ffmpeg).is_absolute() else None,
        "ffprobe": ffprobe if Path(ffprobe).is_absolute() else None,
    }


class FFmpegVideoProcessor:
    """A local processor that samples frames instead of sending an entire video onward."""

    def __init__(self, *, ffmpeg_binary: str | None = None, ffprobe_binary: str | None = None) -> None:
        settings = get_settings()
        self.ffmpeg_binary = resolve_binary(ffmpeg_binary or settings.ffmpeg_binary)
        self.ffprobe_binary = resolve_binary(ffprobe_binary or settings.ffprobe_binary, sibling_of=self.ffmpeg_binary)

    def probe(self, media_path: str | Path) -> VideoMetadata:
        result = self._run(
            [
                self.ffprobe_binary,
                "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,width,height",
                "-of", "json",
                str(media_path),
            ]
        )
        try:
            payload = json.loads(result.stdout)
            streams = payload.get("streams", [])
            video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
            duration = payload.get("format", {}).get("duration")
            return VideoMetadata(
                duration_seconds=float(duration) if duration is not None else None,
                has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
                width=video_stream.get("width"),
                height=video_stream.get("height"),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise VideoProcessingError("ffprobe returned unreadable media metadata") from error

    def prepare(self, media_path: str | Path, output_directory: str | Path) -> PreparedMedia:
        source = Path(media_path)
        if not source.is_file():
            raise VideoProcessingError(f"Media file does not exist: {source}")
        output_dir = Path(output_directory)
        frames_dir = output_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        metadata = self.probe(source)

        audio_path: Path | None = None
        if metadata.has_audio:
            audio_path = output_dir / "audio.wav"
            self._run([self.ffmpeg_binary, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)])

        settings = get_settings()
        frame_pattern = frames_dir / "frame_%05d.jpg"
        self._run(
            [
                self.ffmpeg_binary,
                "-y",
                "-i",
                str(source),
                "-vf",
                f"fps={settings.frame_sample_fps}",
                "-frames:v",
                str(settings.max_sampled_frames),
                "-q:v",
                "2",
                str(frame_pattern),
            ]
        )
        frames = [str(path) for path in sorted(frames_dir.glob("frame_*.jpg"))]
        if not frames:
            raise VideoProcessingError("ffmpeg did not produce any sampled frames")
        return PreparedMedia(audio_path=str(audio_path) if audio_path else None, frame_paths=frames, metadata=metadata)

    def extract_audio(self, media_path: str | Path, output_directory: str | Path) -> tuple[str, float | None]:
        """Convert any audio file to the mono 16 kHz WAV speech models expect. Returns path and duration."""
        source = Path(media_path)
        if not source.is_file():
            raise VideoProcessingError(f"Media file does not exist: {source}")
        metadata = self.probe(source)
        if not metadata.has_audio:
            raise VideoProcessingError("The file contains no audio stream")
        output_dir = Path(output_directory)
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / "audio.wav"
        self._run([self.ffmpeg_binary, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(audio_path)])
        return str(audio_path), metadata.duration_seconds

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(command, check=True, text=True, capture_output=True)
        except FileNotFoundError as error:
            name = Path(command[0]).name
            raise VideoProcessingError(
                f"Required binary was not found: {command[0]}. Install FFmpeg and make sure it is on "
                f"PATH, or set {'FFPROBE_BINARY' if 'ffprobe' in name.lower() else 'FFMPEG_BINARY'} "
                "to its absolute path in .env."
            ) from error
        except subprocess.CalledProcessError as error:
            detail = error.stderr.strip() or error.stdout.strip() or "unknown ffmpeg error"
            raise VideoProcessingError(detail) from error
