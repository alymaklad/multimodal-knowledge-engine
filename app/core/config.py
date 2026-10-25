import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.passwords import generate_secret

_EPHEMERAL_SECRET: str | None = None


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or `.env`."""

    database_url: str = "sqlite:///./data/instagram_knowledge.db"
    processing_version: str = "v1"
    media_root: str = "./data/media"
    max_upload_bytes: int = 100 * 1024 * 1024
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    frame_sample_fps: float = 1.0
    max_sampled_frames: int = 120
    # "celery" hands work to a Redis-backed worker; "local" runs it on a background thread in
    # this process, which is what a single-user install without Redis needs.
    task_runner: str = "celery"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    ocr_provider: str = "tesseract"
    tesseract_binary: str = "tesseract"
    auto_review_confidence: float = 0.60
    cors_origins: str = ""
    speech_provider: str = "none"
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    embedding_provider: str = "hashing"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimensions: int = 256
    retrieval_fp16: bool = True
    vlm_max_ocr_chars: int = 6000
    vlm_max_transcript_chars: int = 4000
    vector_store: str = "database"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "reel_content"
    vision_provider: str = "evidence"
    extraction_provider: str = "rule_based"
    anthropic_model: str = "claude-opus-5"
    anthropic_max_tokens: int = 16000
    vlm_max_frames: int = 8
    qwen_vision_model: str = "Qwen/Qwen2.5-VL-3B-Instruct"
    qwen_load_in_4bit: bool = True
    # 512*28*28. Frames dominate VRAM in a multi-image prompt, so this is the first knob to lower
    # on an 8 GB card - well before swapping to a smaller checkpoint.
    qwen_max_pixels: int = 401_408
    qwen_max_new_tokens: int = 1024
    # Constrained decoding: the model may only emit tokens that keep the output a valid instance
    # of the vision schema. Makes malformed JSON unrepresentable rather than detected afterwards.
    qwen_constrained_json: bool = True
    anthropic_small_model: str = "claude-haiku-4-5"
    vision_routing: bool = False
    routing_simple_max_frames: int = 4
    auto_accept_confidence: float = 0.85
    auth_required: bool = False
    secret_key: str = ""
    token_ttl_seconds: int = 60 * 60 * 12
    rate_limit_per_minute: int = 0
    log_level: str = "INFO"
    object_store: str = "local"
    object_store_root: str = "./data/objects"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_prefix: str = ""
    signed_url_ttl_seconds: int = 900
    answer_provider: str = "extractive"
    extraction_prompt_version: str = "v1"
    reranker: str = "none"
    reranker_model: str = "BAAI/bge-reranker-base"
    rerank_candidates: int = 30
    rag_evidence_limit: int = 8
    duplicate_similarity_threshold: float = 0.92

    @property
    def resolved_secret_key(self) -> str:
        """The signing key, generated per process when unset.

        An ephemeral key is fine for local development — tokens simply stop working on restart —
        but it would silently break a multi-process deployment, so `AUTH_REQUIRED` demands a real
        one at startup rather than failing confusingly later.
        """
        if self.secret_key:
            return self.secret_key
        if self.auth_required:
            raise RuntimeError("Set SECRET_KEY when AUTH_REQUIRED is enabled")
        global _EPHEMERAL_SECRET
        if _EPHEMERAL_SECRET is None:
            _EPHEMERAL_SECRET = generate_secret()
        return _EPHEMERAL_SECRET

    # The env file is indirected through ENV_FILE so the test suite can opt out of the developer's
    # own .env. Without that, configuring real models locally silently changes what the tests assert.
    model_config = SettingsConfigDict(env_file=os.getenv("ENV_FILE", ".env"), extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
