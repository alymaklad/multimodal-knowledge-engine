from fastapi import APIRouter

from app.core.config import get_settings
from app.services.video import media_tools_status

router = APIRouter(prefix="/api", tags=["metadata"])


@router.get("/capabilities")
def capabilities() -> dict[str, object]:
    """Report what is actually configured, so clients never assume an unavailable capability."""
    settings = get_settings()
    speech_configured = settings.speech_provider.lower() not in {"none", "", "disabled"}
    vision_is_local = settings.vision_provider.lower() in {"evidence", "local", "none"}
    extraction_is_local = settings.extraction_provider.lower() in {"rule_based", "rules", "local"}
    limitations = []
    tools = media_tools_status()
    for tool, path in tools.items():
        if path is None:
            limitations.append(
                f"{tool} was not found; video uploads cannot be processed until it is on PATH "
                f"or {tool.upper()}_BINARY points at it"
            )
    if vision_is_local:
        limitations.append("evidence-only vision fallback; set VISION_PROVIDER=claude for a real VLM")
    if extraction_is_local:
        limitations.append("deterministic pattern extraction; set EXTRACTION_PROVIDER=claude for normalisation")
    if not speech_configured:
        limitations.append("no configured speech provider; silent-video handling only")
    if settings.answer_provider.lower() in {"extractive", "local"}:
        limitations.append("extractive answering quotes evidence; set ANSWER_PROVIDER=claude to compose over it")
    if not settings.auth_required:
        limitations.append("X-User-Id development identity is accepted; set AUTH_REQUIRED for real auth")
    if settings.reranker.lower() in {"none", "identity", ""}:
        limitations.append("no reranker; fusion order is final")
    if settings.rate_limit_per_minute <= 0:
        limitations.append("rate limiting is disabled")
    if settings.object_store.lower() == "local":
        limitations.append("local filesystem object store; it cannot issue signed URLs")
    if settings.embedding_provider.lower() == "hashing":
        limitations.append("hashed lexical embeddings; configure a dense model for true semantics")
    if settings.vector_store.lower() == "database":
        limitations.append("exact brute-force vector scan; move to Qdrant for large collections")
    return {
        "ingestion": ["instagram_reel_url", "local_video_upload"],
        "media_preparation": {
            "features": ["ffmpeg_audio", "frame_sampling"],
            # Resolved paths, not a claim: an upload fails deep in the pipeline if these are
            # missing, so the answer belongs here where it can be seen beforehand.
            "binaries": media_tools_status(),
        },
        "analysis": [
            settings.speech_provider if speech_configured else "speech_disabled",
            f"{settings.ocr_provider}_ocr",
            f"{settings.vision_provider}_vision",
            f"{settings.extraction_provider}_extraction",
        ],
        "evidence_types": ["visual", "ocr", "transcript", "metadata", "inferred"],
        "resilience": {
            "independent_stages": ["transcription", "ocr", "vision", "extraction"],
            "vision_fallback": True,
            "degraded_runs_go_to_review": True,
        },
        "model_routing": {
            "enabled": settings.vision_routing,
            "small_model": settings.anthropic_small_model,
            "strong_model": settings.anthropic_model,
        },
        "authentication": {
            "required": settings.auth_required,
            "scheme": "bearer",
            "development_header": not settings.auth_required,
        },
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "confidence_routing": {
            "accept_above": settings.auto_accept_confidence,
            "verify_between": [settings.auto_review_confidence, settings.auto_accept_confidence],
            "review_below": settings.auto_review_confidence,
            "verification": "corroborate mid-confidence values against stored OCR and transcript text",
        },
        "provider_contracts": ["speech", "ocr", "vision", "extraction", "embedding", "answering"],
        "retrieval": {
            "modes": ["lexical", "semantic", "hybrid"],
            "filters": ["content_type", "creator", "topic", "minimum_score"],
            "fusion": "reciprocal_rank",
            "reranker": settings.reranker,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model if settings.embedding_provider != "hashing" else "local-hashing-v1",
            "vector_store": settings.vector_store,
        },
        "answering": {
            "provider": settings.answer_provider,
            "grounded": True,
            "cites_sources": True,
            "citations_restricted_to_retrieved_reels": True,
        },
        "reporting": {
            "aggregates": True,
            "narrative": settings.answer_provider.lower() in {"claude", "anthropic"},
            "source": "database aggregates only, never whole Reels",
        },
        "object_storage": {
            "backend": settings.object_store,
            "layout": "users/{user_id}/reels/{reel_id}/",
            "signed_urls": settings.object_store.lower() in {"s3", "minio"},
        },
        "provenance": {
            "recorded_per_reel": True,
            "fields": [
                "transcription_model",
                "vision_model",
                "embedding_model",
                "ocr_provider",
                "extraction_provider",
                "extraction_prompt_version",
            ],
        },
        "known_limitations": limitations,
    }
