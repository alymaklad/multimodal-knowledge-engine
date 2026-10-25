"""Tests for the local-model providers.

These drive the providers through fakes rather than real weights: the point is to pin the contract
(what the provider does with a backend's output), not to benchmark a model. Downloading several
gigabytes to assert a dict shape would make the suite unrunnable.
"""

from pathlib import Path

import pytest

from app.providers.base import VisionResult
from app.providers.paddle_ocr import MIN_LINE_CONFIDENCE, PaddleOCRProvider
from app.providers.qwen_vision import _parse_json


class FakeRapidOCR:
    """Mimics rapidocr_onnxruntime.RapidOCR: returns (result, elapsed)."""

    def __init__(self, per_frame):
        self._per_frame = per_frame
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        return self._per_frame.get(Path(path).name, []), 0.01


def _provider_with(engine) -> PaddleOCRProvider:
    provider = PaddleOCRProvider.__new__(PaddleOCRProvider)
    provider._language = "en"
    provider._engine = engine
    provider._backend = "rapidocr"
    return provider


def test_ocr_keeps_lines_as_separate_statements() -> None:
    """Caption lines are separate claims, so they must not be flattened into one sentence."""
    engine = FakeRapidOCR(
        {
            "frame_00001.jpg": [
                ([[0, 0], [10, 0], [10, 5], [0, 5]], "RECIPROCAL RANK FUSION", 0.98),
                ([[0, 6], [10, 6], [10, 11], [0, 11]], "K = 60", 0.95),
            ]
        }
    )
    result = _provider_with(engine).extract([Path("frames/frame_00001.jpg")])

    assert len(result) == 1
    assert result[0].text == "RECIPROCAL RANK FUSION\nK = 60"
    assert result[0].confidence == pytest.approx((0.98 + 0.95) / 2)
    assert len(result[0].bounding_boxes) == 2
    assert result[0].bounding_boxes[0]["text"] == "RECIPROCAL RANK FUSION"


def test_low_confidence_lines_are_dropped() -> None:
    """Decorative artefacts must not reach the extractor as confident-looking text."""
    engine = FakeRapidOCR(
        {
            "frame_00001.jpg": [
                ([[0, 0], [1, 0], [1, 1], [0, 1]], "REAL TEXT", 0.9),
                ([[0, 0], [1, 0], [1, 1], [0, 1]], "~~garbage~~", MIN_LINE_CONFIDENCE - 0.2),
            ]
        }
    )
    result = _provider_with(engine).extract([Path("frames/frame_00001.jpg")])
    assert result[0].text == "REAL TEXT"


def test_frames_with_no_readable_text_produce_no_observation() -> None:
    engine = FakeRapidOCR({})
    assert _provider_with(engine).extract([Path("frames/frame_00002.jpg")]) == []


def test_every_frame_is_read() -> None:
    engine = FakeRapidOCR({"a.jpg": [([[0, 0]], "A", 0.9)], "b.jpg": [([[0, 0]], "B", 0.9)]})
    result = _provider_with(engine).extract([Path("f/a.jpg"), Path("f/b.jpg"), Path("f/c.jpg")])
    assert len(engine.calls) == 3
    assert [item.text for item in result] == ["A", "B"]


def test_a_malformed_polygon_does_not_break_the_observation() -> None:
    engine = FakeRapidOCR({"a.jpg": [("not-a-box", "STILL READABLE", 0.9)]})
    result = _provider_with(engine).extract([Path("f/a.jpg")])
    assert result[0].text == "STILL READABLE"
    assert result[0].bounding_boxes[0]["box"] == []


# --------------------------------------------------------------- vision JSON

def test_plain_json_is_parsed() -> None:
    assert _parse_json('{"description": "a slide", "confidence": 0.9}')["confidence"] == 0.9


def test_fenced_json_is_recovered() -> None:
    payload = 'Sure!\n```json\n{"description": "a slide"}\n```\nHope that helps.'
    assert _parse_json(payload) == {"description": "a slide"}


def test_json_surrounded_by_prose_is_recovered() -> None:
    payload = 'Here is the result: {"description": "a slide", "topic": "fusion"} — let me know.'
    assert _parse_json(payload)["topic"] == "fusion"


def test_unparsable_output_returns_none_rather_than_guessing() -> None:
    assert _parse_json("I think it is probably a slide, but I am not sure.") is None
    assert _parse_json("") is None


def test_a_json_list_is_rejected() -> None:
    """The schema is an object; a list would silently break every downstream field read."""
    assert _parse_json('["slide", "chart"]') is None


def test_vision_provider_reports_zero_confidence_without_frames() -> None:
    """No frames means no visual evidence — and no model call."""
    from app.providers.qwen_vision import QwenVisionProvider

    provider = QwenVisionProvider()
    result = provider.analyze_video([], "", "")
    assert isinstance(result, VisionResult)
    assert result.confidence == 0.0
    assert "no visual analysis is possible" in result.description
