from pathlib import Path

from app.providers.local import EvidenceVisionProvider, RuleBasedExtractionProvider


def test_rule_based_extraction_preserves_explicit_workout_evidence() -> None:
    provider = RuleBasedExtractionProvider()
    vision = EvidenceVisionProvider().analyze_video([], "", "")
    results = provider.extract(vision, "Exercise: Incline Dumbbell Press. 4 x 8-12 reps.", "")
    workout = next(result for result in results if result.content_type == "workout_exercise")
    assert workout.data["exercise"] == "Incline Dumbbell Press"
    assert workout.data["sets"] == 4
    assert workout.data["reps"] == "8-12 reps"


def test_rule_based_extraction_identifies_ai_terms() -> None:
    provider = RuleBasedExtractionProvider()
    vision = EvidenceVisionProvider().analyze_video([Path("frame.jpg")], "Hybrid search uses BM25 and embeddings.", "")
    results = provider.extract(vision, "Hybrid search uses BM25 and embeddings.", "")
    knowledge = next(result for result in results if result.content_type == "ai_knowledge")
    assert knowledge.data["topic"] == "hybrid search"
