from app.db.database import SessionLocal
from app.models.analysis import ExtractedContent
from app.models.collection import Collection
from app.models.reel import Reel, ReelStatus
from app.schemas.workout import WorkoutPlanRequest
from app.services.reporting import build_collection_report
from app.services.workout import generate_workout_plan


def _reel(db: SessionLocal, collection_id: int, suffix: str, creator: str | None = None) -> Reel:
    reel = Reel(
        collection_id=collection_id,
        instagram_url=f"upload://{suffix}.mp4",
        creator_username=creator,
        status=ReelStatus.completed,
        processing_version="v1",
    )
    db.add(reel)
    db.flush()
    return reel


def test_collection_report_aggregates_ai_knowledge() -> None:
    with SessionLocal() as db:
        collection = Collection(name="AI report")
        db.add(collection)
        db.flush()
        first = _reel(db, collection.id, "rag", "@ada")
        second = _reel(db, collection.id, "lora", "@ada")
        db.add_all(
            [
                ExtractedContent(
                    reel_id=first.id,
                    content_type="ai_knowledge",
                    data={"topic": "RAG", "technologies": ["Qdrant", "BM25"]},
                    text_representation="RAG with Qdrant and BM25",
                ),
                ExtractedContent(
                    reel_id=second.id,
                    content_type="ai_knowledge",
                    data={"topic": "LoRA", "technologies": ["Qdrant"]},
                    text_representation="LoRA fine-tuning",
                ),
            ]
        )
        db.commit()

        report = build_collection_report(db, collection.id)
        assert report.total_reels == 2
        assert report.analyzed_reels == 2
        assert report.top_technologies[0] == ("Qdrant", 2)
        assert report.top_creators == [("@ada", 2)]


def test_workout_plan_uses_sources_and_equipment_filter() -> None:
    with SessionLocal() as db:
        collection = Collection(name="Workout plan")
        db.add(collection)
        db.flush()
        dumbbell = _reel(db, collection.id, "press")
        barbell = _reel(db, collection.id, "squat")
        db.add_all(
            [
                ExtractedContent(
                    reel_id=dumbbell.id,
                    content_type="workout_exercise",
                    data={
                        "exercise": "Incline Dumbbell Press",
                        "sets": 4,
                        "reps": "8-12",
                        "equipment": ["dumbbells", "bench"],
                        "target_muscles": ["chest"],
                    },
                    text_representation="Incline dumbbell press for chest",
                    confidence=0.9,
                ),
                ExtractedContent(
                    reel_id=barbell.id,
                    content_type="workout_exercise",
                    data={"exercise": "Back Squat", "sets": 3, "reps": "8", "equipment": ["barbell"]},
                    text_representation="Barbell back squat",
                    confidence=0.8,
                ),
            ]
        )
        db.commit()

        plan = generate_workout_plan(
            db,
            WorkoutPlanRequest(
                collection_id=collection.id,
                days=2,
                exercises_per_day=1,
                available_equipment=["dumbbells", "bench"],
            ),
        )
        assert len(plan.days) == 2
        assert all(day.exercises[0].name == "Incline Dumbbell Press" for day in plan.days)
        assert plan.days[0].exercises[0].source_reel_id == dumbbell.id
