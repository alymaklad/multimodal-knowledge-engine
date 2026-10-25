from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TranscriptRead(BaseModel):
    text: str
    language: str | None
    segments: list[dict[str, Any]]
    model: str

    model_config = ConfigDict(from_attributes=True)


class OCRResultRead(BaseModel):
    id: int
    timestamp_seconds: float | None
    text: str
    bounding_boxes: list[dict[str, Any]]
    confidence: float | None

    model_config = ConfigDict(from_attributes=True)


class VisualAnalysisRead(BaseModel):
    description: str
    analysis_json: dict[str, Any]
    model: str
    confidence: float | None

    model_config = ConfigDict(from_attributes=True)


class ExtractedContentRead(BaseModel):
    id: int
    content_type: str
    data: dict[str, Any]
    text_representation: str
    confidence: float | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProvenanceRead(BaseModel):
    processing_version: str
    models: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class SourceAnalysisRead(BaseModel):
    source_id: int
    transcript: TranscriptRead | None
    ocr_results: list[OCRResultRead]
    visual_analysis: VisualAnalysisRead | None
    extracted_content: list[ExtractedContentRead]
    provenance: ProvenanceRead | None = None
