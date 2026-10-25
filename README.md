# Multimodal Personal Knowledge Engine for Instagram Reels

A FastAPI service that turns saved short-form videos (Instagram Reels) into a searchable,
evidence-backed knowledge base. Each video goes through FFmpeg preparation, then speech
transcription, OCR, multi-frame vision analysis, and structured extraction. The results are
embedded per modality for lexical, semantic, and hybrid retrieval, and questions are answered only
from retrieved evidence, with citations.

One rule runs through the whole design: **every value the system shows must be traceable to the
evidence that produced it.** Extracted fields are tagged with their source modality. Confidence
decides whether a human needs to look. Every analysed Reel records the exact model set that
produced its knowledge.

> Further reading: [HOW_IT_WORKS.md](HOW_IT_WORKS.md) is a long-form technical and user guide.
> [instagram_multimodal_knowledge_engine_architecture.md](instagram_multimodal_knowledge_engine_architecture.md)
> is the original architecture specification, and [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)
> tracks the implementation against it.

---

## Contents

1. [Architecture](#architecture)
2. [Processing pipeline](#processing-pipeline)
3. [Provider abstraction](#provider-abstraction)
4. [Confidence routing and verification](#confidence-routing-and-verification)
5. [Indexing and retrieval](#indexing-and-retrieval)
6. [Grounded answering and reports](#grounded-answering-and-reports)
7. [Data model](#data-model)
8. [Storage](#storage)
9. [Task execution](#task-execution)
10. [Security](#security)
11. [Observability and resilience](#observability-and-resilience)
12. [Evaluation and benchmarking](#evaluation-and-benchmarking)
13. [API surface](#api-surface)
14. [Web interface](#web-interface)
15. [Configuration](#configuration)
16. [Running](#running)
17. [Testing](#testing)
18. [Project layout](#project-layout)
19. [Limitations](#limitations)

---

## Architecture

```text
                 ┌────────────────────────────────────────────────────────────┐
  Browser  ───▶  │  FastAPI (app/main.py)                                     │
  /app SPA       │   middleware: request-id correlation, rate limiting, CORS  │
  curl / API     │   routers:    app/api/*  →  services: app/services/*       │
                 └───────────────┬───────────────────────────┬────────────────┘
                                 │ 202 Accepted + job row     │ search / ask / reports
                                 ▼                            ▼
                 ┌──────────────────────────────┐   ┌──────────────────────────┐
                 │ Task runner                   │   │ Retrieval                │
                 │  TASK_RUNNER=celery → Redis   │   │  lexical · dense · RRF   │
                 │  TASK_RUNNER=local  → thread  │   │  → optional reranker     │
                 └───────────────┬───────────────┘   └────────────┬─────────────┘
                                 ▼                                │
                 ┌──────────────────────────────┐                 │
                 │ Pipeline (app/workers)        │                 │
                 │  1. media_preparation (FFmpeg)│                 │
                 │  2. multimodal_analysis       │──── embeds ────▶│
                 │     speech · OCR · vision ·   │                 │
                 │     extraction                │                 │
                 │  3. indexing · provenance ·   │                 │
                 │     confidence verification   │                 │
                 └───────────────┬───────────────┘                 │
                                 ▼                                 ▼
        ┌──────────────────┐ ┌──────────────────┐ ┌───────────────────────────┐
        │ SQL database      │ │ Object store      │ │ Vector store              │
        │ SQLite / Postgres │ │ local disk / S3   │ │ SQL table / Qdrant        │
        └──────────────────┘ └──────────────────┘ └───────────────────────────┘
```

The code is split into four layers:

| Layer | Package | Responsibility |
|---|---|---|
| HTTP | `app/api` | Routing, validation (Pydantic schemas in `app/schemas`), ownership checks. Holds no business logic. |
| Services | `app/services` | Ingestion, analysis orchestration, indexing, retrieval, RAG, reporting, storage, evaluation. |
| Providers | `app/providers` | Swappable model implementations behind abstract contracts, chosen by configuration in `factory.py`. |
| Persistence | `app/models`, `app/db` | SQLAlchemy 2.0 ORM models; SQLite by default, PostgreSQL via `DATABASE_URL`. |

The HTTP request never runs the pipeline. Ingestion writes a durable `ProcessingJob` row and
returns `202 Accepted`. The work happens later in a worker, either a Celery process or an
in-process background thread.

---

## Processing pipeline

### Ingestion

- `POST /api/reels/upload` takes a `.mp4`, `.mov`, or `.webm` file. The upload is **SHA-256 hashed
  while it streams to disk**. The hash is stored as the Reel's `external_id`, so the same content
  cannot be ingested twice into one collection, even under a different filename.
- `POST /api/reels` records an Instagram Reel URL (validated to `instagram.com/.../reel/...`).
  Duplicate URLs are blocked by a `(collection_id, instagram_url)` unique constraint. The system
  does **not** scrape Instagram: a URL-only Reel stays a reference until media is supplied.
- `POST /api/jobs/{job_id}/dispatch` hands a job to the configured task runner.

### Stage 1: media preparation (`app/workers/pipeline.py::prepare_reel_media`)

`FFmpegVideoProcessor` (`app/services/video.py`):

1. `ffprobe` reads duration, dimensions, and whether an audio stream exists.
2. If there is audio, FFmpeg extracts **mono 16 kHz WAV**, the input format speech models expect.
3. Frames are sampled at `FRAME_SAMPLE_FPS` (default 1 fps), capped at `MAX_SAMPLED_FRAMES`
   (default 120).
4. Audio and frames are saved as `MediaAsset` rows with sequence numbers and timestamps, replacing
   any earlier output so the stage is retry-safe.
5. The source video, audio, and frames are published to the object store (see [Storage](#storage)).

FFmpeg binaries are resolved to absolute paths up front (`resolve_binary`). A missing binary then
shows up in `/api/capabilities` before anyone uploads, instead of failing deep in a worker.

### Stage 2: multimodal analysis (`app/services/analysis.py::run_analysis`)

There are four perception stages. **Each one fails independently** and records a `StageMetric`
with its status, duration, model, and item count:

| Stage | Contract | Output table |
|---|---|---|
| `transcription` | `SpeechProvider.transcribe(audio)` | `transcripts` (text, language, timed segments) |
| `ocr` | `OCRProvider.extract(frames)` | `ocr_results` (per-frame text, boxes, confidence, timestamp) |
| `vision` | `VisionProvider.analyze_video(frames, transcript, ocr_text)` | `visual_analyses` |
| `extraction` | `ExtractionProvider.extract(vision, transcript, ocr_text)` | `extracted_content` (typed JSON records) |

Perception and normalisation are deliberately separate. The vision model reports what it sees
across **evenly spaced frames from the whole clip**, because a movement cannot be identified from a
single image. The extraction stage then turns that perception, plus OCR and transcript text, into
typed records such as workout exercises or AI concepts. Both are told to return `null` rather than
invent a value.

Every extracted field carries an evidence tag in `data.evidence`: `visual`, `ocr`, `transcript`,
`metadata`, or `inferred`. Silent Reels still work: OCR supplies sets and reps from on-screen text,
and the vision model identifies the movement.

How failures degrade:

- An OCR failure does not throw away a good transcript, and a transcription failure does not throw
  away on-screen text.
- A vision failure falls back to `EvidenceVisionProvider` before giving up.
- An extraction failure leaves the previous run's knowledge in place instead of erasing it.
- If any stage failed outright, the run is marked `degraded` and goes to human review, however
  confident the surviving extraction is.

### Stage 3: indexing, provenance, routing (`analyze_reel`)

After analysis the worker:

1. **Indexes** the Reel. Each modality becomes its own embedded chunk (see
   [Indexing](#indexing-and-retrieval)).
2. **Records provenance** in `ProcessingProvenance`: the transcription, vision, embedding, OCR, and
   extraction models plus `EXTRACTION_PROMPT_VERSION`. These are read from what the run actually
   persisted, not from configuration as it stands later.
3. **Verifies confidence** and sets the Reel and job to `completed` or `needs_review`. The decision
   and its reason are written to the `JobEvent` history.

Job states: `queued → running → completed | needs_review | failed`. The `current_stage` field moves
`ingestion → media_preparation → ocr_and_transcription → multimodal_analysis → completed/needs_review`.

`POST /api/reels/{id}/reprocess` clears generated artifacts and knowledge, keeps the source media,
and requeues the Reel.

---

## Provider abstraction

`app/providers/base.py` defines abstract contracts (`SpeechProvider`, `OCRProvider`,
`VisionProvider`, `ExtractionProvider`, `AnswerProvider`), with embedding and reranking contracts
alongside. `app/providers/factory.py` is **the only place where configuration picks a concrete
class**. Heavy implementations are imported lazily inside the factory, so an install without the
optional ML packages still runs the whole pipeline on local defaults.

| Concern | Default (no weights, no API key) | Local models | Hosted |
|---|---|---|---|
| Speech | `none` | `faster_whisper` (`WHISPER_MODEL`, e.g. `distil-large-v3`) | — |
| OCR | `tesseract` | `paddleocr` (PP-OCR via RapidOCR / ONNX Runtime) | — |
| Vision | `evidence` (records what evidence exists; describes no visuals) | `qwen` (Qwen2.5-VL, 4-bit, constrained JSON) | `claude` |
| Extraction | `rule_based` (regex set/rep and AI-term patterns) | `vision_fields` (reads the VLM's structured fields, regex fallback) | `claude` |
| Embeddings | `hashing` (hashed bag-of-features, lexical only) | `sentence_transformer` (`BAAI/bge-m3`) | — |
| Reranking | `none` | `cross_encoder` (`bge-reranker-*`) | — |
| Answering | `extractive` (quotes evidence) | — | `claude` |

Notes on the less obvious ones:

- **Vision routing** (`VISION_ROUTING=true`, Claude only): `RoutingVisionProvider` sends simple clips
  (at most `ROUTING_SIMPLE_MAX_FRAMES`) to `ANTHROPIC_SMALL_MODEL`. It escalates to the strong model
  only when the small one is uncertain.
- **Qwen constrained decoding**: with `QWEN_CONSTRAINED_JSON=true`, `lm-format-enforcer` lets the
  model emit only tokens that keep the output a valid instance of the vision JSON schema, so
  malformed JSON cannot be produced at all. Free-text fields have `maxLength` bounds so the model
  cannot run out of tokens mid-string.
- **Prompt budgeting**: `VLM_MAX_OCR_CHARS` and `VLM_MAX_TRANSCRIPT_CHARS` limit how much text goes
  into the vision prompt. Attention cost grows quadratically with prompt length; one 84-second
  screen recording produced ~10.5k tokens of OCR and requested a 10 GiB attention tensor.
- **GPU co-existence** (`app/core/gpu.py`): on Windows, onnxruntime (RapidOCR) and CTranslate2
  (faster-whisper) ship their own CUDA libraries. If either loads before PyTorch, a later cuDNN call
  causes a *native* abort that Python cannot catch. `prefer_torch_cudnn()` warms PyTorch's cuDNN
  first. `RETRIEVAL_FP16` loads BGE-M3 and the reranker in fp16, which halves their VRAM.

---

## Confidence routing and verification

`app/services/verification.py` routes on the **lowest** extraction confidence in a run, using
three tiers:

| Lowest confidence | Decision |
|---|---|
| ≥ `AUTO_ACCEPT_CONFIDENCE` (0.85) | `accepted` |
| between the two thresholds | secondary verification → `verified` or `needs_review` |
| < `AUTO_REVIEW_CONFIDENCE` (0.60) | `needs_review` |

Secondary verification checks each mid-confidence scalar field against the stored OCR and
transcript text. A field passes if its evidence tag is `ocr` or `transcript`, or if its value
appears literally in that text. Any field that rests only on inference sends the Reel to a human.
A run with no extractions, or with no confidence values, also goes to review.

People resolve reviews with `POST /api/reels/{id}/review` and record field-level fixes with
`POST /api/reels/{id}/corrections`. A correction is stored next to the original model output and
never overwrites it.

---

## Indexing and retrieval

### Chunking (`app/services/embeddings.py`)

`build_reel_chunks` embeds each modality as its own chunk so every match can be traced to its
source: `transcript`, `ocr`, `visual`, one `extracted:<content_type>` per record, and a
whole-Reel `summary` (creator + caption + all of the above). Chunks are capped at 4,000 characters.

### Vector stores (`app/services/vector_store.py`)

- `database` (default): vectors are stored as JSON in `content_embeddings` and searched with an
  exact brute-force cosine scan. Recall is perfect and there is no second system to keep in sync.
- `qdrant`: uses `QDRANT_URL`, with payload filters scoped to the collection, for collections too
  large to scan.

Changing the embedding model changes the vector width (for example 256 → 1024). Old rows are then
skipped and logged. Rebuild them with `POST /api/collections/{id}/index`.

### Search modes (`app/services/retrieval.py`, `POST /api/search`)

| Mode | Mechanism |
|---|---|
| `lexical` (default) | Counts exact term occurrences across transcript, OCR, visual description, and extraction text. Handles technical strings like `QLoRA` or `BM25` that embeddings blur. |
| `semantic` | Embeds the query, searches the vector store, and keeps the best chunk per Reel (up to 3 snippets). |
| `hybrid` | **Reciprocal rank fusion** (`k = 60`) of the lexical and dense rankings, then optional reranking. |

RRF combines rankings rather than scores because occurrence counts and cosine similarities are on
different scales; only their orderings can be combined meaningfully. The reranker scores a wider
window (`RERANK_CANDIDATES`, default 30) than the page asked for, since its value is promoting
results that ranked just below the cut.

Results can be filtered by `content_type`, `creator`, and `topic`. Topics come from extracted
knowledge, not free-text tags. When filters are active, semantic search fetches 5× more candidates
so filtering cannot empty the page.

`GET /api/collections/{id}/duplicates` flags near-duplicate Reels when whole-content similarity is
at least `DUPLICATE_SIMILARITY_THRESHOLD` (0.92). This catches re-encodes and reposts that hash
checks miss.

---

## Grounded answering and reports

`POST /api/collections/{id}/ask` (`app/services/rag.py`):

1. Retrieves up to `RAG_EVIDENCE_LIMIT` chunks by dense similarity. If nothing is indexed yet, it
   falls back to lexical search so it can still cite real sources.
2. Passes the question and **only that evidence** to the `AnswerProvider`.
3. Grounding is enforced in code as well as in the prompt. The prompt forbids outside knowledge,
   and any cited Reel id the model returns that was not in the supplied evidence is **dropped**.
   With no relevant evidence, both providers say so instead of guessing.

Reports (`app/services/reporting.py`):

- `GET /api/reports/collections/{id}` returns deterministic aggregates: topic, technology, creator,
  and content-type counts, plus average extraction confidence.
- `GET /api/reports/collections/{id}/narrative` adds prose. The narrating model sees **only the
  precomputed counts, never the Reels**, so cost stays flat as a collection grows. Without a
  narration provider the endpoint returns `narrated: false` rather than invented prose.

`POST /api/workouts/generate` (`app/services/workout.py`) builds a source-cited plan from extracted
workout exercises and can filter by available equipment.

---

## Data model

All tables are created at startup with `Base.metadata.create_all`. There are no migrations yet.

```text
users
collections ─┬─< reels ─┬── processing_jobs ──< job_events
  (user_id)  │          │                    └─< task_dispatches
             │          ├─< media_assets        (audio | frame, sequence, timestamp)
             │          ├── transcripts         (text, language, segments, model)
             │          ├─< ocr_results         (frame, timestamp, text, boxes, confidence)
             │          ├── visual_analyses     (description, analysis_json, model, confidence)
             │          ├─< extracted_content   (content_type, data JSON + evidence map, confidence)
             │          ├─< content_embeddings  (chunk_key, content_type, vector, model, dims)
             │          ├── processing_provenance (processing_version, models JSON)
             │          ├─< stored_objects      (object-store keys)
             │          ├─< stage_metrics       (stage, status, duration_ms, model, items)
             │          └─< extraction_corrections
             └─< saved_searches
evaluation_cases ──< benchmark_predictions >── benchmark_runs
```

Every resource is scoped to `user_id` through its collection. Ownership checks live in
`app/services/authorization.py` and are applied in every router.

---

## Storage

Working files and durable storage are kept separate on purpose:

- **`MEDIA_ROOT`** holds the local working copies that FFmpeg, OCR, and the VLM read
  (`prepared/<reel_id>/audio.wav`, `prepared/<reel_id>/frames/frame_NNNNN.jpg`).
- **The object store** holds what is kept, under keys that are the same on every backend:

```text
users/{user_id}/reels/{reel_id}/video.mp4
users/{user_id}/reels/{reel_id}/audio.wav
users/{user_id}/reels/{reel_id}/frames/frame_00001.jpg
```

| `OBJECT_STORE` | Access |
|---|---|
| `local` (default) | Files under `OBJECT_STORE_ROOT`, streamed through `GET /api/reels/{id}/media/{object_id}` with the same ownership check as every other endpoint. |
| `s3` | S3 or MinIO (`S3_BUCKET`, `S3_ENDPOINT_URL`). `GET /api/reels/{id}/media` returns presigned URLs valid for `SIGNED_URL_TTL_SECONDS`. |

Publishing is idempotent: republishing replaces a Reel's previous object records. Deleting a Reel
or collection removes its key prefix.

---

## Task execution

| `TASK_RUNNER` | Mechanism | Trade-off |
|---|---|---|
| `celery` (default) | `reels.prepare_media` → `reels.analyze_media` Celery tasks on Redis, auto-retry with backoff (max 2). | Durable, horizontally scalable; needs Redis and a worker process. |
| `local` | One daemon thread with an in-memory queue (`app/services/local_runner.py`). | No infrastructure. Processes **one job at a time** so the VLM, Whisper, and embedder never compete for one 8 GB GPU. In-flight work is lost on restart. |

Two behaviours that came from real failures:

- **Broker probe.** Before `apply_async`, `app/services/dispatch.py` opens a plain socket to the
  broker with a 1-second timeout. Celery's own retry settings could not reliably stop a request
  from hanging for over a minute when Redis was down.
- **Resume on startup.** With the local runner, the FastAPI lifespan hook requeues jobs left
  `queued` or `running` at `ingestion`, `ocr_and_transcription`, or `multimodal_analysis`. Re-running
  a stage is safe because each stage replaces its own earlier output.

---

## Security

- **Two auth modes**, set by `AUTH_REQUIRED`:
  - Off (default): a bearer token is used when present; otherwise the `X-User-Id` header acts as a
    development identity.
  - On: only a valid bearer token counts, and `X-User-Id` is ignored completely.

  The authorization logic is the same in both modes; only how the caller is identified changes.
- **Passwords**: PBKDF2-HMAC-SHA256 with a per-user salt. The iteration count is stored inside the
  hash so it can be raised later without invalidating existing passwords (`app/core/passwords.py`).
- **Tokens**: HMAC-signed payloads with an explicit expiry, not JWTs. That avoids a dependency and
  JWT's algorithm-confusion pitfalls (`app/core/security.py`). `SECRET_KEY` is mandatory when auth
  is on. Without auth, an ephemeral per-process key is generated.
- **No account enumeration**: login gives the same response for a wrong password and an unknown
  account.
- **Rate limiting**: `RATE_LIMIT_PER_MINUTE` turns on a fixed-window limiter keyed by bearer token
  or client IP, returning `429` with `Retry-After`. It is **per process**: with N workers the
  effective limit is N times the setting.
- Uploads are capped at `MAX_UPLOAD_BYTES` (100 MB). The Docker image runs as a non-root user.

---

## Observability and resilience

- **Structured logs**: one JSON object per line (`app/core/logging.py`). The request id is carried
  in a `contextvars` variable, so a log line written deep inside a service still names the request
  that caused it. Responses echo `X-Request-Id`.
- **Health**: `GET /health` checks liveness; `GET /ready` runs `SELECT 1` and returns `503` if the
  database is unreachable.
- **Metrics**: `GET /api/system/metrics` returns counts and status breakdowns.
  `GET /api/system/metrics/pipeline` returns per-stage run counts, failure rate, average and p95
  latency, items processed, and models used. Skipped stages are left out of the failure rate.
- **Capabilities**: `GET /api/capabilities` reports the active providers, retrieval modes,
  thresholds, storage backend, auth mode, resolved FFmpeg paths, and a list of limitations derived
  from the actual configuration.
- **Job audit trail**: `GET /api/jobs/{id}/events` lists every stage transition, degradation, and
  routing decision with its reason.

---

## Evaluation and benchmarking

- **Cases**: `POST /api/evaluations/cases` stores manual ground truth.
  `POST /api/evaluations/cases/{id}/score` scores one prediction by exact field match.
  `GET /api/evaluations/summary` gives accuracy by category.
- **Benchmark runs**: `POST /api/evaluations/runs` registers a configuration as a free-form object,
  for example `{"vision_model": "qwen2.5-vl-3b", "uses_ocr": false}`. Predictions are submitted with
  optional latency and cost.
- **Comparison**: `GET /api/evaluations/runs/compare?run_ids=1,2` ranks runs by accuracy, with
  latency and cost alongside. It compares **only the cases every run scored**, so a difference in
  coverage is not reported as a difference in quality. With two runs it also returns a delta: the
  accuracy change, cases improved or regressed, and the settings that differed. This is how ablations
  such as OCR on vs. off are measured instead of assumed.

---

## API surface

Interactive OpenAPI docs are at `/docs`. All routes are under `/api`:

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `GET /auth/me` |
| Collections | `POST/GET /collections`, `GET/PATCH/DELETE /collections/{id}`, `GET /{id}/export`, `GET /{id}/export.csv`, `POST /{id}/clone`, `GET /{id}/facets` |
| Reels | `POST /reels`, `POST /reels/upload`, `GET /reels/{id}`, `PATCH/DELETE /reels/{id}`, `GET /reels/collection/{collection_id}`, `POST /reels/{id}/reprocess`, `GET /reels/review/queue`, `POST /reels/{id}/review` |
| Analysis & quality | `GET /reels/{id}/analysis`, `POST/GET /reels/{id}/corrections` |
| Media | `GET /reels/{id}/media`, `GET /reels/{id}/media/{object_id}`, `POST /reels/{id}/publish` |
| Jobs | `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/reel/{reel_id}`, `GET /jobs/{id}/events`, `POST /jobs/{id}/dispatch`, `GET /jobs/{id}/dispatches`, `POST /jobs/{id}/cancel`, `POST /jobs/{id}/retry` |
| Knowledge | `POST /search`, `POST /collections/{id}/index`, `POST /reels/{id}/index`, `POST /collections/{id}/ask`, `GET /collections/{id}/duplicates` |
| Saved searches | `POST/GET /saved-searches`, `POST /saved-searches/{id}/run`, `DELETE /saved-searches/{id}` |
| Reports | `GET /reports/collections/{id}`, `GET /reports/collections/{id}/narrative`, `POST /workouts/generate` |
| Evaluation | `/evaluations/cases`, `/evaluations/summary`, `/evaluations/runs`, `/evaluations/runs/{id}/predictions`, `/evaluations/runs/compare` |
| System | `GET /capabilities`, `GET /system/metrics`, `GET /system/metrics/pipeline`, `GET /system/export` |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/search -H "Content-Type: application/json" -H "X-User-Id: demo" -d "{\"collection_id\": 1, \"query\": \"romanian deadlift tempo\", \"mode\": \"hybrid\", \"limit\": 10}"
```

---

## Web interface

`GET /app` serves a single-page client from `app/web/static`: plain HTML, CSS, and JavaScript with
no framework and no build step. It calls the same public API as any other client. Its eight views
are Dashboard, Collections, Search, Ask, Workout, Review Queue, Pipeline, and Account.

The main interaction is the **Reel audit drawer**, which opens wherever a Reel appears. It shows
the extracted fields with their evidence tags, the confidence and the routing tier that was applied,
a timestamped OCR and transcript timeline, and the model provenance. `inferred` values are styled
deliberately weaker. Amber means "a human should look"; red means hard failure. Static asset URLs
carry a version query string, so bump it when editing `app.js` or `style.css`.

---

## Configuration

All settings come from environment variables or `.env`, through `pydantic-settings`
(`app/core/config.py`). [.env.example](.env.example) documents every option. The most important
ones:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/instagram_knowledge.db` | Use `postgresql+psycopg://…` in deployment. |
| `TASK_RUNNER` | `celery` | `local` for a single-user install without Redis. |
| `SPEECH_PROVIDER` / `OCR_PROVIDER` | `none` / `tesseract` | See [Provider abstraction](#provider-abstraction). |
| `VISION_PROVIDER` / `EXTRACTION_PROVIDER` | `evidence` / `rule_based` | `qwen`, `claude`, `vision_fields`. |
| `EMBEDDING_PROVIDER` / `VECTOR_STORE` | `hashing` / `database` | `sentence_transformer`, `qdrant`. |
| `RERANKER` / `ANSWER_PROVIDER` | `none` / `extractive` | `cross_encoder`, `claude`. |
| `AUTO_ACCEPT_CONFIDENCE` / `AUTO_REVIEW_CONFIDENCE` | `0.85` / `0.60` | Routing thresholds. |
| `FRAME_SAMPLE_FPS` / `MAX_SAMPLED_FRAMES` / `VLM_MAX_FRAMES` | `1` / `120` / `8` | Frame sampling and how many frames the VLM sees. |
| `OBJECT_STORE` | `local` | `s3` for S3/MinIO. |
| `AUTH_REQUIRED` / `SECRET_KEY` | `false` / empty | A key is required when auth is on. |
| `ANTHROPIC_API_KEY` | — | Read directly by the SDK; needed only for `claude` providers. |

`ENV_FILE` redirects which env file is loaded. The test suite uses it so a developer's own `.env`
(with real models configured) cannot change what the tests assert.

---

## Running

### Requirements

- Python 3.12 (the Docker image uses `python:3.12-slim`)
- FFmpeg and ffprobe on `PATH`, or set `FFMPEG_BINARY` / `FFPROBE_BINARY`
- Tesseract for the default OCR provider
- Optional: Redis (Celery runner), PostgreSQL, Qdrant, MinIO, a CUDA GPU for local models

### Local (Windows)

Double-click `run.bat`, or run `run.bat 8055` to choose a port. It uses `.venv` when present,
checks dependencies, starts Uvicorn with reload, opens `/app` once `/health` responds, and streams
one JSON log line per request.

By hand:

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r requirements.txt
```

```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```

Then open `http://127.0.0.1:8000/app` for the UI or `/docs` for the API. Without Redis, set
`TASK_RUNNER=local` in `.env`.

Optional model backends (Whisper, BGE-M3, Qdrant client, Anthropic SDK, boto3, RapidOCR,
Transformers + bitsandbytes for Qwen) are in `requirements-optional.txt`. For Qwen, install a CUDA
build of PyTorch first.

```bash
.venv/Scripts/pip install -r requirements-optional.txt
```

### Docker Compose

```bash
docker compose up --build
```

This starts the API (`:8000`), a Celery worker, PostgreSQL 16, Redis 7, MinIO (`:9000`, console
`:9001`), and Qdrant (`:6333`). The image includes FFmpeg and Tesseract. Provider choices pass
through from your shell environment (`VISION_PROVIDER`, `ANTHROPIC_API_KEY`, …).

---

## Testing

```bash
.venv/Scripts/python -m pytest
```

There are 157 test functions in 23 modules under `tests/`. They cover the API, auth, pipeline stages, video
preparation, every provider (model clients replaced by fakes), retrieval and reranking, RAG
grounding, object storage, degradation and metrics, the local runner, benchmarking, and the web
client. They run deterministically with no network, no model weights, and no API key.

---

## Project layout

```text
app/
  main.py            FastAPI app, middleware, router registration, startup resume, /health, /ready
  api/               HTTP routers, one per resource area
  core/              config, structured logging, auth tokens, password hashing, rate limiter, GPU init
  db/                engine and session factory
  models/            SQLAlchemy ORM models
  schemas/           Pydantic request/response models
  providers/         model contracts (base.py), factory, and local / Qwen / Claude / Paddle / Whisper implementations
  services/          ingestion, analysis, indexing, retrieval, RAG, verification, storage, reporting, evaluation
  workers/           Celery app, task definitions, pipeline stage functions
  web/static/        single-page client (index.html, app.js, style.css)
tests/               pytest suite
data/                SQLite database, working media, local object store (git-ignored)
Dockerfile, docker-compose.yml, run.bat, requirements*.txt, .env.example
```

---

## Limitations

- **Default providers are baselines, not perception.** With no configuration, vision records only
  which evidence exists, extraction is regex-based, and the hashing embedder is lexical. Real visual
  understanding and semantic retrieval need `qwen`/`claude` and `sentence_transformer`.
- **Claude providers are tested only against fake clients.** The tests fix the request shape and the
  grounding rules, not prompt quality against live output.
- **Local VLM confidence is optimistic.** Qwen2.5-VL-3B often reports 0.95–1.0 even on thin
  evidence, which can leave the review queue empty. Benchmark before trusting it.
- **No Instagram download.** URL ingestion only records a reference; media has to be uploaded.
- **No database migrations** (no Alembic). Schema changes need a fresh database.
- **Single-process rate limiting**, and no Prometheus or OpenTelemetry export.
- Pose estimation, which the architecture lists as optional, is not implemented.
