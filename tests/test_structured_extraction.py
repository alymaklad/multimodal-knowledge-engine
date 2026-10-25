"""Extraction that reads the vision model's typed fields rather than re-parsing its prose."""

from app.providers.base import KEY_POINTS_CONTENT_TYPE, ExtractionResult, VisionResult
from app.providers.local import RuleBasedExtractionProvider
from app.providers.structured_extraction import VisionFieldExtractionProvider

# A vision model's fields for a short talk, with the evidence map a small model really produces:
# every field claimed as "visual", although the topic is on a slide and a point is quoted from speech.
ANALYSIS = {
    "topic": "Reciprocal Rank Fusion",
    "key_points": ["Fusion sums reciprocal ranks from each retriever", "No score normalisation is needed"],
    "entities": ["BM25", "Qdrant"],
    "evidence": {"topic": "visual", "key_points": "visual", "entities": "visual"},
}

OCR = "RECIPROCAL RANK FUSION\nk = 60"
TRANSCRIPT = "Fusion sums reciprocal ranks from each retriever, so no score normalisation is needed."


def _vision(analysis=None, description="A speaker explains a slide about search.", confidence=0.95):
    return VisionResult(description=description, analysis=analysis or {}, confidence=confidence, model="test-vlm")


def test_fields_are_read_directly_rather_than_regexed_from_prose() -> None:
    records = VisionFieldExtractionProvider().extract(_vision(ANALYSIS), TRANSCRIPT, OCR)
    assert len(records) == 1
    assert records[0].content_type == KEY_POINTS_CONTENT_TYPE
    data = records[0].data
    assert data["topic"] == "Reciprocal Rank Fusion"
    assert data["key_points"][0] == "Fusion sums reciprocal ranks from each retriever"
    assert data["entities"] == ["BM25", "Qdrant"]


def test_evidence_is_re_derived_not_taken_from_the_model() -> None:
    """The model labelled every field `visual`; the values say otherwise."""
    evidence = VisionFieldExtractionProvider().extract(_vision(ANALYSIS), TRANSCRIPT, OCR)[0].data["evidence"]
    assert evidence["topic"] == "ocr"              # the slide reads RECIPROCAL RANK FUSION
    assert evidence["key_points"] == "transcript"  # quoted from the speech
    # Nothing corroborates the entity list, so the model's "visual" claim stands.
    assert evidence["entities"] == "visual"


def test_an_uncorroborated_value_the_model_did_not_claim_is_inferred() -> None:
    analysis = {"topic": "Vector databases", "entities": ["Milvus"], "evidence": {}}
    records = VisionFieldExtractionProvider().extract(_vision(analysis), "", "")
    assert records[0].data["evidence"]["entities"] == "inferred"


def test_attribution_survives_ocr_spacing_differences() -> None:
    """OCR often drops the spaces in a stylised caption; that is still the same evidence."""
    records = VisionFieldExtractionProvider().extract(_vision(ANALYSIS), "", "RECIPROCALRANKFUSION")
    assert records[0].data["evidence"]["topic"] == "ocr"


def test_short_values_do_not_claim_false_corroboration() -> None:
    """A two-letter entity appears inside almost any transcript; matching on it would invent provenance."""
    analysis = {"topic": "Search", "entities": ["AI"], "evidence": {"entities": "visual"}}
    records = VisionFieldExtractionProvider().extract(_vision(analysis), "we said AI earlier", "")
    assert records[0].data["evidence"]["entities"] == "visual"


def test_text_representation_reads_as_sentences() -> None:
    """This string is what gets embedded and shown as evidence, so it must be readable."""
    text = VisionFieldExtractionProvider().extract(_vision(ANALYSIS), TRANSCRIPT, OCR)[0].text_representation
    assert text == (
        "Topic: Reciprocal Rank Fusion. Fusion sums reciprocal ranks from each retriever. "
        "No score normalisation is needed. Mentions BM25 and Qdrant."
    )


def test_empty_and_null_fields_are_dropped() -> None:
    analysis = {"topic": "Search", "key_points": [], "entities": None}
    data = VisionFieldExtractionProvider().extract(_vision(analysis), "", "")[0].data
    assert "key_points" not in data and "entities" not in data


def test_confidence_is_the_vision_model_s_own_claim() -> None:
    records = VisionFieldExtractionProvider().extract(_vision(ANALYSIS, confidence=0.72), TRANSCRIPT, OCR)
    assert records[0].confidence == 0.72


def test_a_description_only_vision_result_falls_back_to_the_deterministic_extractor() -> None:
    """The local evidence provider returns no structured fields; it still needs an extractor."""
    provider = VisionFieldExtractionProvider(fallback=RuleBasedExtractionProvider())
    vision = _vision(analysis={"frame_count": 4}, description="8 sampled frames; on-screen text detected")
    records = provider.extract(vision, "", "Hybrid search beats either retriever alone")
    assert records and all(isinstance(r, ExtractionResult) for r in records)
    assert records[0].data["key_points"] == ["Hybrid search beats either retriever alone"]


def test_no_fields_and_no_fallback_yields_nothing_rather_than_an_empty_record() -> None:
    assert VisionFieldExtractionProvider().extract(_vision(analysis={"frame_count": 2}), "", "") == []
