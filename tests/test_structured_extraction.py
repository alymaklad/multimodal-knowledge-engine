"""Extraction that reads the vision model's typed fields rather than re-parsing its prose."""

from app.providers.base import ExtractionResult, VisionResult
from app.providers.local import RuleBasedExtractionProvider
from app.providers.structured_extraction import VisionFieldExtractionProvider

# The fields Qwen2.5-VL actually returned for a real split-squat Source, including its evidence map,
# which labelled everything "visual" even though reps came from on-screen text and the technique
# note was quoted from the narration.
QWEN_ANALYSIS = {
    "content_kind": "workout",
    "exercise": "Bulgarian Split Squat",
    "equipment": ["elevated platform"],
    "target_muscles": ["glutes", "quads", "hamstrings"],
    "sets": 3,
    "reps": "12 each leg",
    "technique_notes": ["Keep the front shin vertical and control the descent"],
    "evidence": {
        "exercise": "visual",
        "equipment": "visual",
        "target_muscles": "visual",
        "sets": "visual",
        "reps": "visual",
        "technique_notes": "visual",
    },
}

OCR = "BULGARIAN SPLIT SQUAT\n3 x 12 each leg"
TRANSCRIPT = "Rear foot elevated split squat. Keep the front shin vertical and control the descent."


def _vision(analysis=None, description="A lifter performs a split squat.", confidence=0.95):
    return VisionResult(description=description, analysis=analysis or {}, confidence=confidence, model="test-vlm")


def test_fields_are_read_directly_rather_than_regexed_from_prose() -> None:
    """The regression this exists for: the old extractor produced
    exercise: "performed by an animated character" by pattern-matching the description."""
    records = VisionFieldExtractionProvider().extract(_vision(QWEN_ANALYSIS), TRANSCRIPT, OCR)

    assert len(records) == 1
    data = records[0].data
    assert data["exercise"] == "Bulgarian Split Squat"
    assert data["sets"] == 3
    assert data["reps"] == "12 each leg"
    assert data["equipment"] == ["elevated platform"]
    assert records[0].content_type == "workout_exercise"


def test_evidence_is_re_derived_not_taken_from_the_model() -> None:
    """The model labelled every field `visual`; the values say otherwise."""
    records = VisionFieldExtractionProvider().extract(_vision(QWEN_ANALYSIS), TRANSCRIPT, OCR)
    evidence = records[0].data["evidence"]

    assert evidence["exercise"] == "ocr"          # the caption reads BULGARIAN SPLIT SQUAT
    assert evidence["reps"] == "ocr"              # "12 each leg" is on screen
    assert evidence["technique_notes"] == "transcript"  # quoted from the narration
    # Nothing corroborates the equipment guess, so the model's "visual" claim stands.
    assert evidence["equipment"] == "visual"


def test_an_uncorroborated_value_the_model_did_not_claim_is_inferred() -> None:
    analysis = {"exercise": "Front Squat", "target_muscles": ["obliques"], "evidence": {}}
    records = VisionFieldExtractionProvider().extract(_vision(analysis), "", "")
    assert records[0].data["evidence"]["target_muscles"] == "inferred"


def test_attribution_survives_ocr_spacing_differences() -> None:
    """PP-OCR read a real caption as `BULGARIANSPLITSQUAT` while the model wrote it with spaces.
    That is the same evidence, and calling it unattributable would mislabel an on-screen value."""
    squashed = "BULGARIANSPLITSQUAT\n3X12EACHLEG"
    records = VisionFieldExtractionProvider().extract(_vision(QWEN_ANALYSIS), "", squashed)
    evidence = records[0].data["evidence"]
    assert evidence["exercise"] == "ocr"
    assert evidence["reps"] == "ocr"


def test_short_values_do_not_claim_false_corroboration() -> None:
    """`3` appears in almost any transcript; matching on it would invent provenance."""
    analysis = {"exercise": "Squat", "sets": 3, "evidence": {"sets": "visual"}}
    records = VisionFieldExtractionProvider().extract(_vision(analysis), "take 3 breaths", "")
    assert records[0].data["evidence"]["sets"] == "visual"


def test_text_representation_reads_as_sentences() -> None:
    """This string is what gets embedded and shown as evidence, so it must be readable."""
    text = VisionFieldExtractionProvider().extract(_vision(QWEN_ANALYSIS), TRANSCRIPT, OCR)[0].text_representation
    assert text.startswith("Bulgarian Split Squat.")
    assert "3 sets." in text
    assert "12 each leg repetitions." in text
    assert "Targets glutes, quads and hamstrings." in text
    assert "Uses elevated platform." in text


def test_ai_knowledge_records_are_produced_too() -> None:
    analysis = {"content_kind": "ai_technical", "topic": "hybrid search", "technologies": ["BM25", "Qdrant"]}
    records = VisionFieldExtractionProvider().extract(_vision(analysis), "", "hybrid search with BM25")
    assert [r.content_type for r in records] == ["ai_knowledge"]
    assert records[0].data["topic"] == "hybrid search"
    assert records[0].data["evidence"]["topic"] == "ocr"
    assert "Topic: hybrid search." in records[0].text_representation


def test_both_record_types_can_come_from_one_source() -> None:
    analysis = {"exercise": "Squat", "topic": "strength training"}
    records = VisionFieldExtractionProvider().extract(_vision(analysis), "", "")
    assert sorted(r.content_type for r in records) == ["ai_knowledge", "workout_exercise"]


def test_empty_and_null_fields_are_dropped() -> None:
    analysis = {"exercise": "Squat", "tempo": None, "warnings": [], "reps": ""}
    data = VisionFieldExtractionProvider().extract(_vision(analysis), "", "")[0].data
    assert "tempo" not in data and "warnings" not in data and "reps" not in data


def test_confidence_is_the_vision_model_s_own_claim() -> None:
    records = VisionFieldExtractionProvider().extract(_vision(QWEN_ANALYSIS, confidence=0.72), TRANSCRIPT, OCR)
    assert records[0].confidence == 0.72


def test_a_description_only_vision_result_falls_back_to_the_regex_extractor() -> None:
    """The local evidence provider returns no structured fields; it still needs an extractor."""
    provider = VisionFieldExtractionProvider(fallback=RuleBasedExtractionProvider())
    vision = _vision(analysis={"frame_count": 4}, description="8 sampled frames; on-screen text detected")
    records = provider.extract(vision, "", "exercise: Barbell Back Squat 4 x 8 reps")
    assert records and all(isinstance(r, ExtractionResult) for r in records)
    assert records[0].content_type == "workout_exercise"


def test_no_fields_and_no_fallback_yields_nothing_rather_than_an_empty_record() -> None:
    assert VisionFieldExtractionProvider().extract(_vision(analysis={"frame_count": 2}), "", "") == []
