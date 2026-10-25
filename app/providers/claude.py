"""Claude-backed perception and normalisation providers.

Two stages, deliberately separate (architecture sections 19 and 25): the vision model supplies
*perception* from sampled frames, and the extraction model *normalises* that perception together
with OCR and transcript evidence into structured records. Both are instructed never to invent a
value, and both tag every field with the evidence type it came from (section 22).
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.providers.base import (
    KEY_POINTS_CONTENT_TYPE,
    ExtractionProvider,
    ExtractionResult,
    ImageDescriber,
    ImageDescription,
    VisionProvider,
    VisionResult,
)

EVIDENCE_TYPES = ["visual", "ocr", "transcript", "metadata", "inferred"]

_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

NO_INVENTION_RULE = (
    "Use only the supplied frames, on-screen text, and transcript. Do not invent information. "
    "If a value is not explicitly visible or spoken, return null for it rather than guessing. "
    "Tag every field with the evidence type it came from: "
    + ", ".join(EVIDENCE_TYPES)
    + ". Prefer explicit evidence over inference."
)

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # maxLength is load-bearing under constrained decoding, not cosmetic. Without it the model
        # narrated the video for 5,881 characters and ran out of token budget mid-string, leaving
        # JSON that no parser could accept. A prefix constraint keeps every partial output
        # *extendable* to something valid; only a length bound makes it *finishable*.
        "description": {
            "type": "string",
            "maxLength": 600,
            "description": "What happens across the frames, in order. Two or three sentences.",
        },
        "topic": {"type": ["string", "null"], "maxLength": 120, "description": "What the video is about, in a few words."},
        "key_points": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 200},
            "description": "The main things the video shows or says, one claim each.",
        },
        "entities": {
            "type": "array",
            "maxItems": 15,
            "items": {"type": "string", "maxLength": 80},
            "description": "People, products, tools, places, or terms the video names.",
        },
        "evidence": {
            "type": "object",
            "description": "Field name mapped to the evidence type that supports it.",
            "additionalProperties": {"type": "string", "enum": EVIDENCE_TYPES},
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["description", "evidence", "confidence"],
    "additionalProperties": False,
}

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content_type": {"type": "string", "enum": [KEY_POINTS_CONTENT_TYPE]},
                    "data": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": ["string", "null"]},
                            "key_points": {"type": "array", "items": {"type": "string"}},
                            "entities": {"type": "array", "items": {"type": "string"}},
                            "evidence": {
                                "type": "object",
                                "additionalProperties": {"type": "string", "enum": EVIDENCE_TYPES},
                            },
                        },
                        "required": ["topic", "key_points", "entities", "evidence"],
                        "additionalProperties": False,
                    },
                    "text_representation": {
                        "type": "string",
                        "description": "Readable sentences describing the record, used for embedding.",
                    },
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["content_type", "data", "text_representation", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["records"],
    "additionalProperties": False,
}


def build_anthropic_client() -> Any:
    try:
        import anthropic
    except ImportError as error:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Install the anthropic package to enable Claude providers, "
            "or set VISION_PROVIDER=evidence and EXTRACTION_PROVIDER=rule_based"
        ) from error
    return anthropic.Anthropic()


def select_frames(frame_paths: list[Path], limit: int) -> list[Path]:
    """Evenly spaced frames spanning the whole source.

    A movement cannot be identified from one frame (section 21), and the first N frames would only
    cover the opening seconds. Even spacing keeps the sampled sequence representative of the clip.
    """
    if limit <= 0 or not frame_paths:
        return []
    if len(frame_paths) <= limit:
        return list(frame_paths)
    if limit == 1:
        return [frame_paths[0]]
    step = (len(frame_paths) - 1) / (limit - 1)
    return [frame_paths[round(index * step)] for index in range(limit)]


def _image_block(frame_path: Path) -> dict[str, Any]:
    media_type = _MEDIA_TYPES.get(frame_path.suffix.lower(), "image/jpeg")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(frame_path.read_bytes()).decode("utf-8"),
        },
    }


def structured_payload(response: Any) -> dict[str, Any]:
    text = next((block.text for block in response.content if block.type == "text"), "")
    if not text:
        raise RuntimeError("Claude returned no text block for a structured request")
    return json.loads(text)


class ClaudeVisionProvider(VisionProvider):
    """Multi-frame visual understanding: the perception half of the pipeline."""

    def __init__(self, model: str | None = None, client: Any | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.anthropic_model
        self._max_frames = settings.vlm_max_frames
        self._max_tokens = settings.anthropic_max_tokens
        self._client = client if client is not None else build_anthropic_client()

    def analyze_video(self, frame_paths: list[Path], transcript: str, ocr_text: str) -> VisionResult:
        frames = select_frames(frame_paths, self._max_frames)
        if not frames:
            return VisionResult(
                description="No frames were sampled from this source, so no visual analysis is possible.",
                analysis={"frame_count": 0, "evidence": {}},
                confidence=0.0,
                model=f"{self._model}-vision",
            )
        content: list[dict[str, Any]] = []
        for index, frame_path in enumerate(frames, start=1):
            content.append({"type": "text", "text": f"Frame {index} of {len(frames)}:"})
            content.append(_image_block(frame_path))
        content.append({"type": "text", "text": self._instruction(transcript, ocr_text)})

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=(
                "You analyse videos for a personal knowledge base. You report only "
                "what the supplied evidence shows. A missing value is always better than a guessed one."
            ),
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": VISION_SCHEMA}},
        )
        payload = structured_payload(response)
        description = payload.pop("description")
        confidence = payload.pop("confidence", None)
        payload["frame_count"] = len(frames)
        payload["frames_available"] = len(frame_paths)
        return VisionResult(
            description=description,
            analysis=payload,
            confidence=confidence,
            model=f"{self._model}-vision",
        )

    @staticmethod
    def _instruction(transcript: str, ocr_text: str) -> str:
        return (
            "The frames above are sampled in order from one video.\n\n"
            "ON-SCREEN TEXT (OCR):\n" + (ocr_text or "(none detected)") + "\n\n"
            "TRANSCRIPT:\n" + (transcript or "(this video has no speech)") + "\n\n"
            "Describe what happens across the frames as a sequence. Then give its topic, its key "
            "points, and the entities it names, filling in only what the evidence actually "
            "supports.\n\n" + NO_INVENTION_RULE
        )


class ClaudeExtractionProvider(ExtractionProvider):
    """Normalises perception plus OCR and transcript evidence into structured records."""

    def __init__(self, model: str | None = None, client: Any | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.anthropic_model
        self._max_tokens = settings.anthropic_max_tokens
        self._client = client if client is not None else build_anthropic_client()

    def extract(self, vision: VisionResult, transcript: str, ocr_text: str) -> list[ExtractionResult]:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=(
                "You normalise multimodal evidence about one video into a structured record. "
                "You never add a fact that is not present in the evidence you were given."
            ),
            messages=[{"role": "user", "content": self._instruction(vision, transcript, ocr_text)}],
            output_config={"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}},
        )
        payload = structured_payload(response)
        return [
            ExtractionResult(
                content_type=record["content_type"],
                data=record["data"],
                text_representation=record["text_representation"],
                confidence=record.get("confidence"),
            )
            for record in payload.get("records", [])
        ]

    @staticmethod
    def _instruction(vision: VisionResult, transcript: str, ocr_text: str) -> str:
        return (
            "VISUAL ANALYSIS:\n" + vision.description + "\n\n"
            "VISUAL FIELDS:\n" + json.dumps(vision.analysis, ensure_ascii=False) + "\n\n"
            "ON-SCREEN TEXT (OCR):\n" + (ocr_text or "(none detected)") + "\n\n"
            "TRANSCRIPT:\n" + (transcript or "(this video has no speech)") + "\n\n"
            "Produce one key-points record for this video: its topic, the main points it makes, and "
            "the entities it names. Return an empty list when the evidence supports nothing. In "
            "`evidence`, map each field you filled to the evidence type that supports it.\n\n"
            + NO_INVENTION_RULE
        )


IMAGE_LONG_EDGE = 1568
IMAGE_MAX_BYTES = 3_750_000


class ClaudeImageDescriber(ImageDescriber):
    """Describes a standalone image, such as a chart, diagram, slide, or photo of notes.

    Unlike the video provider this asks for prose, not a schema: the description is evidence to be
    searched and quoted, and a fixed set of fields would lose most of what a diagram says.
    """

    def __init__(self, model: str | None = None, client: Any | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.anthropic_model
        self._client = client if client is not None else build_anthropic_client()

    def describe(self, image_path: Path, ocr_text: str) -> ImageDescription:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=(
                "You describe images for a personal knowledge base, so that the image can be found by "
                "search and quoted in answers. Describe only what is visible. Never guess at what is "
                "not shown, and say so when something is unreadable."
            ),
            messages=[
                {
                    "role": "user",
                    "content": [
                        _scaled_image_block(image_path),
                        {
                            "type": "text",
                            "text": (
                                "TEXT DETECTED IN THE IMAGE (OCR, may contain errors):\n"
                                + (ocr_text.strip() or "(none detected)")
                                + "\n\nDescribe this image in plain prose. Say what kind of image it is. "
                                "For a chart or table, state what it measures and the values it shows. "
                                "For a diagram, explain its parts and how they connect. Transcribe any "
                                "important visible text exactly."
                            ),
                        },
                    ],
                }
            ],
        )
        text = "\n".join(block.text for block in response.content if block.type == "text").strip()
        return ImageDescription(text=text, model=f"{self._model}-image")


def _scaled_image_block(image_path: Path) -> dict[str, Any]:
    """The image as a base64 block, downscaled when it is larger than the model can use."""
    data = image_path.read_bytes()
    media_type = _MEDIA_TYPES.get(image_path.suffix.lower(), "image/jpeg")
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        with Image.open(image_path) as image:
            if max(image.size) > IMAGE_LONG_EDGE or len(data) > IMAGE_MAX_BYTES:
                image.thumbnail((IMAGE_LONG_EDGE, IMAGE_LONG_EDGE))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=88)
                data, media_type = buffer.getvalue(), "image/jpeg"
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": base64.standard_b64encode(data).decode("utf-8")},
    }
