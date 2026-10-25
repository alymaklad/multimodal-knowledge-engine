from app.providers.base import KEY_POINTS_CONTENT_TYPE
from app.providers.local import EvidenceVisionProvider, RuleBasedExtractionProvider


def _extract(transcript: str = "", ocr_text: str = ""):
    vision = EvidenceVisionProvider().analyze_video([], transcript, ocr_text)
    return RuleBasedExtractionProvider().extract(vision, transcript, ocr_text)


def test_on_screen_text_and_speech_become_key_points_in_their_own_words() -> None:
    [record] = _extract(
        transcript="Hybrid search fuses two rankings. It needs no score normalisation.",
        ocr_text="RECIPROCAL RANK FUSION\nRECIPROCAL RANK FUSION\nk = 60",
    )
    assert record.content_type == KEY_POINTS_CONTENT_TYPE
    # A caption shown on every frame counts once; on-screen text comes first, then speech.
    assert record.data["key_points"] == [
        "RECIPROCAL RANK FUSION",
        "k = 60",
        "Hybrid search fuses two rankings.",
        "It needs no score normalisation.",
    ]
    # Nothing is inferred without a model: no topic, no entities.
    assert record.data["topic"] is None and record.data["entities"] == []
    assert record.data["evidence"] == {"key_points": "ocr"}


def test_no_evidence_means_no_record() -> None:
    assert _extract() == []


def test_key_points_are_capped() -> None:
    sentences = " ".join(f"Point number {index} is here." for index in range(20))
    [record] = _extract(transcript=sentences)
    assert len(record.data["key_points"]) == 8
