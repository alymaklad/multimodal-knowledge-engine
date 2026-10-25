import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.providers.base import ExtractionResult, VisionResult
from app.providers.claude import (
    EXTRACTION_SCHEMA,
    VISION_SCHEMA,
    ClaudeExtractionProvider,
    ClaudeVisionProvider,
    select_frames,
)
from app.providers.factory import get_extraction_provider, get_vision_provider
from app.services.verification import ReviewDecision, verify_extractions


class FakeClaudeClient:
    """Records the request and replays a canned structured response."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.requests: list[dict] = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self._payload))]
        )


def _write_frames(directory: Path, count: int) -> list[Path]:
    # A one-pixel JPEG header is enough: the provider only base64-encodes the bytes.
    paths = []
    for index in range(1, count + 1):
        path = directory / f"frame_{index:05d}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0" + bytes([index]))
        paths.append(path)
    return paths


def test_select_frames_spans_the_whole_reel() -> None:
    frames = [Path(f"frame_{index}.jpg") for index in range(100)]
    chosen = select_frames(frames, 5)
    assert len(chosen) == 5
    assert chosen[0] == frames[0]
    assert chosen[-1] == frames[-1]
    assert select_frames(frames, 200) == frames
    assert select_frames([], 8) == []
    assert select_frames(frames, 0) == []


def test_vision_provider_sends_every_frame_and_both_evidence_streams(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VLM_MAX_FRAMES", "3")
    from app.core.config import get_settings

    get_settings.cache_clear()
    frames = _write_frames(tmp_path, 10)
    client = FakeClaudeClient(
        {
            "description": "A person performs a split squat with a rear foot elevated.",
            "content_kind": "workout",
            "exercise": "Bulgarian Split Squat",
            "equipment": ["dumbbells", "bench"],
            "sets": 3,
            "reps": "10 each leg",
            "evidence": {"exercise": "visual", "sets": "ocr", "reps": "ocr"},
            "confidence": 0.91,
        }
    )
    provider = ClaudeVisionProvider(client=client)
    result = provider.analyze_video(frames, "", "Bulgarian Split Squat\n3 x 10 each leg")

    request = client.requests[0]
    content = request["messages"][0]["content"]
    images = [block for block in content if block["type"] == "image"]
    assert len(images) == 3
    assert all(block["source"]["media_type"] == "image/jpeg" for block in images)
    assert request["output_config"]["format"]["schema"] is VISION_SCHEMA

    instruction = content[-1]["text"]
    assert "Do not invent information" in instruction
    assert "3 x 10 each leg" in instruction
    assert "(this video has no speech)" in instruction

    assert result.description.startswith("A person performs")
    assert result.confidence == 0.91
    assert result.analysis["exercise"] == "Bulgarian Split Squat"
    assert result.analysis["evidence"]["sets"] == "ocr"
    assert result.analysis["frame_count"] == 3
    assert result.analysis["frames_available"] == 10
    get_settings.cache_clear()


def test_vision_provider_reports_zero_confidence_without_frames() -> None:
    client = FakeClaudeClient({})
    result = ClaudeVisionProvider(client=client).analyze_video([], "", "")
    assert client.requests == []
    assert result.confidence == 0.0
    assert "no visual analysis is possible" in result.description


def test_extraction_provider_returns_structured_records() -> None:
    client = FakeClaudeClient(
        {
            "records": [
                {
                    "content_type": "workout_exercise",
                    "data": {
                        "exercise": "Bulgarian Split Squat",
                        "sets": 3,
                        "evidence": {"exercise": "visual", "sets": "ocr"},
                    },
                    "text_representation": "Bulgarian Split Squat. 3 sets.",
                    "confidence": 0.88,
                }
            ]
        }
    )
    vision = VisionResult(description="Split squat", analysis={"exercise": "Bulgarian Split Squat"})
    results = ClaudeExtractionProvider(client=client).extract(vision, "", "3 x 10")

    assert client.requests[0]["output_config"]["format"]["schema"] is EXTRACTION_SCHEMA
    assert "Do not invent information" in client.requests[0]["messages"][0]["content"]
    assert len(results) == 1
    assert results[0].content_type == "workout_exercise"
    assert results[0].data["evidence"]["sets"] == "ocr"
    assert results[0].confidence == 0.88


def test_extraction_provider_accepts_an_empty_record_list() -> None:
    client = FakeClaudeClient({"records": []})
    vision = VisionResult(description="Nothing identifiable", analysis={})
    assert ClaudeExtractionProvider(client=client).extract(vision, "", "") == []


def _result(confidence: float, data: dict) -> ExtractionResult:
    return ExtractionResult(
        content_type="workout_exercise", data=data, text_representation="x", confidence=confidence
    )


def test_high_confidence_is_accepted_without_verification() -> None:
    outcome = verify_extractions([_result(0.93, {"exercise": "Squat"})])
    assert outcome.decision is ReviewDecision.accepted
    assert outcome.requires_review is False


def test_low_confidence_goes_straight_to_review() -> None:
    outcome = verify_extractions([_result(0.42, {"exercise": "Squat"})], ocr_text="Squat")
    assert outcome.decision is ReviewDecision.needs_review


def test_absent_knowledge_requires_review() -> None:
    assert verify_extractions([]).decision is ReviewDecision.needs_review
    assert verify_extractions([_result(None, {"exercise": "Squat"})]).decision is ReviewDecision.needs_review


def test_mid_confidence_is_promoted_when_evidence_corroborates_it() -> None:
    outcome = verify_extractions(
        [_result(0.7, {"exercise": "Bulgarian Split Squat", "sets": 3})],
        ocr_text="Bulgarian Split Squat\n3 sets",
    )
    assert outcome.decision is ReviewDecision.verified
    assert outcome.requires_review is False
    assert outcome.lowest_confidence == 0.7


def test_mid_confidence_falls_back_to_review_when_nothing_corroborates_it() -> None:
    outcome = verify_extractions(
        [_result(0.7, {"exercise": "Barbell Back Squat"})], ocr_text="unrelated caption"
    )
    assert outcome.decision is ReviewDecision.needs_review
    assert "Barbell Back Squat" not in outcome.reason
    assert "workout_exercise.exercise" in outcome.reason


def test_an_ocr_tagged_field_counts_as_corroborated() -> None:
    outcome = verify_extractions(
        [_result(0.7, {"exercise": "Squat", "evidence": {"exercise": "ocr"}})], ocr_text=""
    )
    assert outcome.decision is ReviewDecision.verified


def test_factories_default_to_the_local_providers() -> None:
    assert type(get_vision_provider()).__name__ == "EvidenceVisionProvider"
    assert type(get_extraction_provider()).__name__ == "RuleBasedExtractionProvider"


def test_factories_reject_an_unknown_provider(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("VISION_PROVIDER", "nonexistent")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Unsupported vision provider"):
        get_vision_provider()
    get_settings.cache_clear()
