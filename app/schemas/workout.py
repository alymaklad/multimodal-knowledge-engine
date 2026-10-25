from pydantic import BaseModel, Field


class WorkoutPlanRequest(BaseModel):
    collection_id: int = Field(gt=0)
    days: int = Field(ge=1, le=7)
    exercises_per_day: int = Field(default=4, ge=1, le=10)
    available_equipment: list[str] = Field(default_factory=list)
    target_muscles: list[str] = Field(default_factory=list)
    allow_repeats: bool = False


class WorkoutExercise(BaseModel):
    name: str
    sets: int | None
    reps: str | None
    equipment: list[str]
    target_muscles: list[str]
    source_reel_id: int
    source_url: str


class WorkoutDay(BaseModel):
    day: int
    exercises: list[WorkoutExercise]


class WorkoutPlan(BaseModel):
    collection_id: int
    days: list[WorkoutDay]
