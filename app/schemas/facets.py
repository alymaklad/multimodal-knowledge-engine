from pydantic import BaseModel


class CollectionFacets(BaseModel):
    content_types: list[tuple[str, int]]
    topics: list[tuple[str, int]]
    entities: list[tuple[str, int]]
