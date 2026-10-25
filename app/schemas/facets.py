from pydantic import BaseModel


class CollectionFacets(BaseModel):
    content_types: list[tuple[str, int]]
    topics: list[tuple[str, int]]
    technologies: list[tuple[str, int]]
    target_muscles: list[tuple[str, int]]
