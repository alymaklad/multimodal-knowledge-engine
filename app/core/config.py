import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.passwords import generate_secret

_EPHEMERAL_SECRET: str | None = None


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or `.env`."""

    database_url: str = "sqlite:///./data/multimodal_knowledge.db"
    # Apply pending migrations when the API starts. Turn off when several API processes share a
    # database, and run `alembic upgrade head` once per release instead.
    auto_migrate: bool = True
    processing_version: str = "v1"
    media_root: str = "./data/media"
    max_upload_bytes: int = 100 * 1024 * 1024
    # Pasted text is stored as a file like any upload; this bounds a single paste.
    max_text_characters: int = 500_000
    # Link fetching runs in the worker. Private and loopback addresses are refused unless this is
    # enabled, so a link cannot be used to make the server read its own network.
    fetch_allow_private_hosts: bool = False
    fetch_timeout_seconds: float = 20.0
    max_fetch_bytes: int = 20 * 1024 * 1024
    fetch_max_redirects: int = 5
    fetch_user_agent: str = "MultimodalKnowledgeEngine/0.1 (+personal knowledge base; fetches links its user added)"
    # Comma-separated, in order of preference. Any other language is used when none of these exists.
    youtube_transcript_languages: str = "en"
    # Processors split sources into segments no longer than this, at natural boundaries.
    segment_max_characters: int = 1800
    # A PDF page with less extractable text than this is treated as scanned and sent to OCR.
    pdf_ocr_min_characters: int = 20
    pdf_ocr_dpi: int = 200
    max_document_pages: int = 1000
    # Transcripts (YouTube, audio) are grouped into windows of about this many seconds.
    transcript_window_seconds: float = 60.0
    # Retrieval chunks: consecutive segments are packed up to this size, and each chunk repeats the
    # end of the previous one so a passage cut at a boundary is still found whole.
    chunk_max_characters: int = 1500
    chunk_overlap_characters: int = 200
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
    qdrant_collection: str = "source_content"
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
    # The chatbot: "extractive" (no model, quotes passages), "claude", or "ollama" (local).
    chat_provider: str = "extractive"
    chat_model: str = "claude-opus-5-5"
    # Claude Opus 5.5 defaults to medium effort; it is set explicitly so the choice is visible.
    chat_effort: str = "medium"
    chat_max_tokens: int = 16000
    # Earlier messages sent with each question, so follow-ups are understood.
    chat_history_messages: int = 8
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    # Ollama's default context is 2048 tokens, too small for a question plus eight passages.
    ollama_num_ctx: int = 8192
    ollama_timeout_seconds: float = 120.0
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
