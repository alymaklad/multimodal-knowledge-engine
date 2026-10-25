from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import ExtractedContent
from app.models.reel import Reel
from app.schemas.workout import WorkoutDay, WorkoutExercise, WorkoutPlan, WorkoutPlanRequest

WORKOUT_CONTENT_TYPES = ("workout", "workout_exercise")


def generate_workout_plan(db: Session, request: WorkoutPlanRequest) -> WorkoutPlan:
    entries = list(
        db.execute(
            select(ExtractedContent, Reel)
            .join(Reel)
            .where(Reel.collection_id == request.collection_id, ExtractedContent.content_type.in_(WORKOUT_CONTENT_TYPES))
            .order_by(ExtractedContent.confidence.desc().nullslast(), ExtractedContent.id)
        )
    )
    candidates = [_to_exercise(entry, reel) for entry, reel in entries]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if request.available_equipment:
        requested = {item.lower() for item in request.available_equipment}
        candidates = [
            candidate
            for candidate in candidates
            if not candidate.equipment or {item.lower() for item in candidate.equipment}.issubset(requested)
        ]
    if request.target_muscles:
        targets = {item.lower() for item in request.target_muscles}
        candidates = [candidate for candidate in candidates if targets.intersection(item.lower() for item in candidate.target_muscles)]
    if not candidates:
        raise ValueError("No compatible extracted workout exercises are available in this collection")

    if request.allow_repeats:
        selected_days = [
            [candidates[(day * request.exercises_per_day + index) % len(candidates)] for index in range(request.exercises_per_day)]
            for day in range(request.days)
        ]
    else:
        # Avoid duplicate selections in one session while allowing sensible reuse on later days.
        selected_days = [candidates[: request.exercises_per_day] for _ in range(request.days)]
    return WorkoutPlan(
        collection_id=request.collection_id,
        days=[
            WorkoutDay(
                day=day + 1,
                exercises=selected_days[day],
            )
            for day in range(request.days)
        ],
    )


def _to_exercise(entry: ExtractedContent, reel: Reel) -> WorkoutExercise | None:
    data = entry.data
    exercise = data.get("exercise")
    name = exercise.get("name") if isinstance(exercise, dict) else exercise
    if not isinstance(name, str) or not name.strip():
        return None
    sets = data.get("sets")
    if isinstance(sets, dict):
        sets = sets.get("value")
    return WorkoutExercise(
        name=name.strip(),
        sets=sets if isinstance(sets, int) else None,
        reps=_string_value(data.get("reps")),
        equipment=_string_list(data.get("equipment")),
        target_muscles=_string_list(data.get("target_muscles") or data.get("primary_muscles")),
        source_reel_id=reel.id,
        source_url=reel.instagram_url,
    )


def _string_value(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("value")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _string_list(value: object) -> list[str]:
    return [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, Iterable) and not isinstance(value, str) else []
