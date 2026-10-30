# Multimodal Knowledge Engine

A FastAPI backend for a personal research notebook, in the spirit of NotebookLM. You add documents,
web pages, YouTube videos, images, audio, and video to a library, group them into collections, and
ask questions. The answers come only from the sources in the collection, and every claim cites the
passage it came from: "p. 4", "12:30–13:45", "§ Setup".

One rule runs through the whole design: **every answer must be traceable to the evidence that
produced it.** Passages keep their location in the source, answers cite passages rather than whole
sources, and a citation the model invents is removed instead of shown.

> The web app lives in `frontend/` (React and TypeScript); `/docs` serves the interactive API reference.
> [HOW_IT_WORKS.md](HOW_IT_WORKS.md) explains the internals in depth.
> The project began as an engine for Instagram Reels;
> [the original architecture](instagram_multimodal_knowledge_engine_architecture.md) is kept for
> history.

---

## Contents

1. [What it does](#what-it-does)
2. [Architecture](#architecture)
3. [Sources and collections](#sources-and-collections)
4. [Processing](#processing)
5. [Chunking and retrieval](#chunking-and-retrieval)
6. [The chatbot](#the-chatbot)
7. [Providers](#providers)
8. [Storage, tasks, and migrations](#storage-tasks-and-migrations)
9. [Security and operations](#security-and-operations)
10. [API overview](#api-overview)
11. [Configuration](#configuration)
12. [The web app](#the-web-app)
13. [Running](#running)
14. [Testing](#testing)
15. [Project layout](#project-layout)
16. [Limitations](#limitations)

---

## What it does

```text
add a source ──▶ fetch or verify ──▶ process into located segments ──▶ chunk ──▶ index
  file, link,      web page, YouTube       pages, sections,               overlapping,   BM25 +
  pasted text      transcript, upload      time windows, image text        located        vectors
                                                                                            │
ask in a conversation ──▶ rewrite the follow-up ──▶ hybrid retrieval ──▶ streamed answer ◀──┘
                          using the history         over the chosen       citing [1], [2] …
                                                    sources               resolved to passages
```

- **A shared library.** Sources belong to you, not to a collection. A collection is a selection
  over your library, so one source can be in many collections and is processed only once.
- **Every common format.** PDF (with OCR for scanned pages), Word, text, Markdown, web pages,
  YouTube captions, images (OCR plus an optional description), audio (transcribed), and video
  (transcript, on-screen text, and visual analysis).
- **Located citations.** Search results and answers name the page, section, or moment they quote.
- **Conversations.** Follow-up questions are understood in context, answers stream as they are
  written, and a conversation can be limited to some of a collection's sources.
- **Your choice of model.** Answers can come from no model at all (quoted passages), Claude, or a
  local model through Ollama.

---

## Architecture

```text
                 ┌──────────────────────────────────────────────────────────────┐
  Client  ────▶  │  FastAPI (app/main.py)                                        │
  (API, /docs)   │   request ids · rate limiting · CORS · migrations at startup │
                 │   routers app/api/*  ──▶  services app/services/*            │
                 └──────────────┬──────────────────────────────┬────────────────┘
                                │ 202 Accepted + job            │ search, ask, chat (SSE)
                                ▼                               ▼
                 ┌──────────────────────────────┐   ┌──────────────────────────────┐
                 │ Task runner                   │   │ Retrieval                    │
                 │  TASK_RUNNER=celery → Redis   │   │  BM25 + dense, fused per     │
                 │  TASK_RUNNER=local  → thread  │   │  chunk, reranked             │
                 └──────────────┬───────────────┘   └──────────────┬───────────────┘
                                ▼                                  │
                 ┌──────────────────────────────┐                  │
                 │ Pipeline (app/workers)        │                  │
                 │  route by kind: fetch links,  │                  │
                 │  prepare video, or extract    │──── chunks ─────▶│
                 │  content with a processor     │                  │
                 └──────────────┬───────────────┘                  │
                                ▼                                  ▼
       ┌───────────────────┐ ┌───────────────────┐ ┌──────────────────────────────┐
       │ SQL database       │ │ Object store       │ │ Vector store                 │
       │ SQLite / Postgres  │ │ local disk / S3    │ │ SQL table / Qdrant           │
       │ (Alembic)          │ │                    │ │                              │
       └───────────────────┘ └───────────────────┘ └──────────────────────────────┘
```

| Layer | Package | Responsibility |
|---|---|---|
| HTTP | `app/api` | Routing, validation (`app/schemas`), ownership checks. No business logic. |
| Services | `app/services` | Ingestion, fetching, chunking, retrieval, chat, storage, evaluation. |
| Processors | `app/processors` | One per kind of content: turn a stored file into located segments. |
| Providers | `app/providers` | Swappable models behind contracts, chosen by configuration in `factory.py`. |
| Persistence | `app/models`, `app/db` | SQLAlchemy 2.0 models; schema versioned with Alembic. |

The HTTP request never processes a source. Adding one stores it and a durable `ProcessingJob`,
returns `202 Accepted`, and a worker does the rest.

---

## Sources and collections

| Add | Endpoint | Notes |
|---|---|---|
| A file | `POST /api/sources/upload` | PDF, DOCX, TXT, Markdown, PNG, JPEG, WebP, MP3, WAV, M4A, Ogg, FLAC, MP4, MOV, WebM. |
| A link | `POST /api/sources/url` | Web pages (HTML, PDF, text) and single YouTube videos. |
| Pasted text | `POST /api/sources/text` | Stored as a text source; `MAX_TEXT_CHARACTERS` bounds it. |

Each accepts an optional `collection_id` to add the new source straight into a collection.

- **Uploads are verified by content.** The extension decides the kind and the file's first bytes
  must match it, so a renamed file is refused with `415` before the rest is written. Text must be
  UTF-8; a DOCX must contain a document body. The stored MIME type is the verified one.
- **Duplicates are detected across your library**, by content hash for files and by normalised
  link for URLs. Every YouTube form (`youtu.be/…`, `/shorts/…`, `watch?v=…&t=…`) is one video.
  A `409` names the existing source, so a client can add it to another collection instead.
- **Collections are selections.** `POST /api/collections/{id}/sources` adds library sources,
  `DELETE /api/collections/{id}/sources/{source_id}` removes one. Deleting a collection keeps its
  sources; deleting a source removes it from every collection; cloning a collection shares them.

Links are fetched by the worker, never in the request. Every redirect hop is checked again, every
address a host resolves to must be public (unless `FETCH_ALLOW_PRIVATE_HOSTS` is on), and responses
are size-capped and limited to types a processor can read. YouTube sources fetch the caption track
through the optional `youtube-transcript-api` (written captions in a preferred language first).

---

## Processing

A source's first worker stage routes it by kind: links are fetched, video goes through FFmpeg
preparation and multimodal analysis, and everything else goes to the processor for its kind.

| Kind | Processor | Cited as |
|---|---|---|
| PDF | Text per page with PDFium; a page with no text layer is rendered and sent to OCR | page |
| Word | Body in document order, headings kept, tables as rows | section, paragraph |
| Text, Markdown, pasted text | Heading-aware splitter | section, paragraph |
| Web page | Main content (`trafilatura` if installed, else a built-in extractor) as Markdown | section, paragraph |
| YouTube | Captions grouped into windows of about a minute | time range |
| Audio | FFmpeg to 16 kHz mono, then the speech provider, windowed the same way | time range |
| Image | OCR text, plus a description from Claude when `VISION_PROVIDER=claude` | image |
| Video | Transcript, on-screen text, and a visual description, plus a key-points record | time range |

Processors return **segments**: a passage, its modality (`text`, `transcript`, `ocr`, `visual`), and
a locator. A problem that does not stop processing (one unreadable page) becomes a warning and
the stage is marked `degraded`; a source with nothing extractable fails with the reason.

### Video analysis

Video keeps the multi-stage pipeline the project started with. FFmpeg extracts audio and samples
frames; then four stages run and **fail independently**: transcription, OCR, vision, and
extraction. A failure in one keeps the others' results, and the run is marked degraded.

Extraction produces one domain-neutral **key-points record** per video (`topic`, `key_points`,
`entities`), and every field is tagged with the evidence it came from (`visual`, `ocr`,
`transcript`, `metadata`, `inferred`). Tags are re-derived by matching values against the stored
OCR and transcript text rather than trusted from the model. The record's confidence then decides
whether a person needs to look:

| Confidence | Outcome |
|---|---|
| Above `AUTO_ACCEPT_CONFIDENCE` (0.85) | Accepted |
| Between the thresholds | Checked against the stored text: corroborated values are accepted, others go to review |
| Below `AUTO_REVIEW_CONFIDENCE` (0.60), or any stage failed | Review queue (`GET /api/sources/review/queue`) |

Corrections (`POST /api/sources/{id}/corrections`) are stored separately from model output, so the
original extraction stays auditable.

---

## Chunking and retrieval

Segments are packed into chunks of at most `CHUNK_MAX_CHARACTERS`, each starting with the end of
the previous one (`CHUNK_OVERLAP_CHARACTERS`), so a passage cut at a boundary is still found whole.
Packing never mixes evidence streams or crosses a heading, but does span pages and time windows;
the chunk's locator records the span ("pp. 3–4"). Each chunk is embedded and stored with its
locator.

| `POST /api/search` mode | How it ranks |
|---|---|
| `lexical` | BM25 over the chunks of the sources in scope; good for exact terms and names |
| `semantic` | Vector similarity over the same chunks |
| `hybrid` | Reciprocal rank fusion of both rankings per chunk, then the reranker |

Results group chunks by source and carry up to three passages, each with its locator and label.
Filters: `content_type`, `author`, `topic`, `minimum_score`, and `source_ids` to search part of a
collection. `POST /api/collections/{id}/ask` answers one question from hybrid, reranked,
chunk-level evidence; `GET /api/collections/{id}/duplicates` finds near-identical sources.

---

## The chatbot

Conversations belong to a collection and, optionally, to a subset of its sources.

```text
POST   /api/collections/{id}/conversations          start one (title, source_ids optional)
GET    /api/collections/{id}/conversations          list them
GET    /api/conversations/{id}                      read it, with every message
PATCH  /api/conversations/{id}                      rename, or change its sources (null = all)
POST   /api/conversations/{id}/messages             ask, and wait for the answer (JSON)
POST   /api/conversations/{id}/messages/stream      ask, and receive the answer as it is written
```

A turn stores the question, rewrites a follow-up into a standalone query using the conversation,
retrieves passages, and streams the answer. The model cites passages as `[1]`, `[2]`; those markers
are resolved to the passages they name (source, chunk, locator, label, snippet), and markers that
point at nothing are removed from the text. The streaming endpoint sends Server-Sent Events:

| Event | Data |
|---|---|
| `user_message` | The stored question |
| `retrieval` | The query actually searched and how many passages were found |
| `delta` | The next fragment of the answer |
| `done` | The stored answer with its citations |
| `error` | `failed` or `refused`, with a message; the stream then ends |

Failures, refusals, and a client that disconnects mid-answer are all stored as such. A conversation
answers one question at a time (`409` while it is busy).

---

## Providers

Every model sits behind a contract in `app/providers/base.py`, chosen by configuration in
`app/providers/factory.py`. The defaults need no model, network, or API key.

| Setting | Default | Alternatives |
|---|---|---|
| `CHAT_PROVIDER` | `extractive` (quotes the best sentence of each passage) | `claude`, `ollama` |
| `ANSWER_PROVIDER` (`/ask`) | `extractive` | `claude`, `ollama` |
| `EMBEDDING_PROVIDER` | `hashing` (lexical, 256-d) | `sentence_transformer` (BGE-M3) |
| `RERANKER` | `none` | `cross_encoder` (BGE reranker) |
| `OCR_PROVIDER` | `tesseract` | `paddleocr` (RapidOCR / PP-OCR) |
| `SPEECH_PROVIDER` | `none` | `faster_whisper` |
| `VISION_PROVIDER` | `evidence` (records which evidence exists) | `qwen` (local Qwen2.5-VL), `claude` |
| `EXTRACTION_PROVIDER` | `rule_based` (key points in the evidence's own words) | `vision_fields`, `claude` |
| `VECTOR_STORE` | `database` (exact scan) | `qdrant` |

- **Claude chat** uses `claude-opus-5-5` (`CHAT_MODEL`), streams, sets effort explicitly
  (`CHAT_EFFORT`), and enables the server-side refusal fallback (`fallbacks: "default"`), so a
  question a safety classifier declines is retried on Anthropic's recommended model in the same
  stream. A refusal that survives it is discarded rather than kept as an answer.
- **Ollama** runs locally (`OLLAMA_MODEL`, default `qwen2.5:7b`) with the context window set
  explicitly (`OLLAMA_NUM_CTX`), since Ollama's 2048-token default would cut off the passages.
- Every provider gets the same prompts: answer only from the passages, cite each claim, and say so
  when the collection does not cover the question.

---

## Storage, tasks, and migrations

- **Working files** live under `MEDIA_ROOT` (uploads, fetched pages, extracted audio and frames).
  **Durable copies** go to the object store under `users/{user_id}/sources/{source_id}/…`: the
  original file for every kind, plus audio and frames for video. `OBJECT_STORE=local` serves them
  through `GET /api/sources/{id}/media/{object_id}`; `s3` (S3 or MinIO) returns presigned URLs.
  A fetched HTML page is only ever served as a sandboxed download.
- **Task runner.** `TASK_RUNNER=celery` uses Redis-backed Celery tasks (`sources.start`,
  `sources.analyze_media`, `sources.extract_content`) with retries. `TASK_RUNNER=local` runs one
  job at a time on a background thread, so local models never compete for one GPU, and resumes
  unfinished jobs at startup. Jobs are dispatched with `POST /api/jobs/{id}/dispatch`.
- **Migrations.** The schema is managed by Alembic (`app/db/migrations`). The API applies pending
  migrations at startup (`AUTO_MIGRATE=true`); with several API processes, turn that off and run
  `alembic upgrade head` once per release. To change the schema, edit the models and draft a
  migration:

  ```bash
  .venv/Scripts/python -m alembic revision --autogenerate -m "describe the change"
  ```

  `tests/test_migrations.py` fails whenever the models and migrations disagree. A database created
  before migrations existed is adopted automatically.

---

## Security and operations

- **Auth.** With `AUTH_REQUIRED=false` (default) a bearer token is used when present, otherwise the
  `X-User-Id` header is a development identity. With it on, only a valid bearer token counts.
  Passwords are PBKDF2-HMAC-SHA256 with a per-user salt; tokens are HMAC-signed with an expiry, and
  `SECRET_KEY` is required when auth is on. Every resource is scoped to its owner.
- **Untrusted input.** Uploads are type-checked by content and size-capped (`MAX_UPLOAD_BYTES`);
  links are refused for private addresses and re-checked on every redirect; fetched HTML is never
  rendered on the API's origin.
- **Rate limiting.** `RATE_LIMIT_PER_MINUTE` enables a per-process fixed-window limiter.
- **Observability.** One JSON log line per event with a request id; `GET /health` and `GET /ready`;
  per-stage latency and failure rates at `GET /api/system/metrics/pipeline`; every job's history at
  `GET /api/jobs/{id}/events`; `GET /api/capabilities` reports what is configured and what that
  configuration cannot do.
- **Evaluation.** Annotated cases (`/api/evaluations/cases`) and benchmark runs
  (`/api/evaluations/runs`) compare extraction accuracy across configurations over the cases every
  run scored, which is how an ablation such as OCR on versus off is measured.

---

## API overview

The full, interactive reference is at `/docs`.

| Area | Endpoints |
|---|---|
| Library | `POST /api/sources/upload`, `/url`, `/text`; `GET /api/sources`; `GET/PATCH/DELETE /api/sources/{id}`; `POST /api/sources/{id}/reprocess` |
| Collections | `POST/GET /api/collections`; `GET/PATCH/DELETE /api/collections/{id}`; `POST/DELETE /api/collections/{id}/sources`; `/clone`; `/export`, `/export.csv` |
| Processing | `POST /api/jobs/{id}/dispatch`, `/retry`, `/cancel`; `GET /api/jobs`, `/api/jobs/{id}`, `/events`; `GET /api/sources/{id}/analysis` |
| Search and answers | `POST /api/search`; `POST /api/collections/{id}/ask`, `/index`; `GET /api/collections/{id}/duplicates`, `/facets`; saved searches at `/api/saved-searches` |
| Chat | `/api/collections/{id}/conversations`; `/api/conversations/{id}`; `/api/conversations/{id}/messages`, `/messages/stream` |
| Review | `GET /api/sources/review/queue`; `POST /api/sources/{id}/review`; `/api/sources/{id}/corrections` |
| Media | `GET /api/sources/{id}/media`, `/media/{object_id}`; `POST /api/sources/{id}/publish` |
| Accounts and system | `/api/auth/register`, `/login`, `/me`; `/api/system/metrics`, `/metrics/pipeline`, `/export`; `/api/capabilities`; `/health`, `/ready` |
| Evaluation | `/api/evaluations/cases`, `/summary`, `/runs`, `/runs/compare` |

---

## Configuration

Settings come from environment variables or `.env`; [.env.example](.env.example) lists every one
with its default. The ones you are most likely to change:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/multimodal_knowledge.db` | Use `postgresql+psycopg://…` in deployment |
| `TASK_RUNNER` | `celery` | `local` runs jobs on a thread, no Redis needed |
| `CHAT_PROVIDER`, `ANSWER_PROVIDER` | `extractive` | `claude` or `ollama` for model-written answers |
| `CHAT_MODEL`, `CHAT_EFFORT` | `claude-opus-5-5`, `medium` | Claude chat model and effort |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_NUM_CTX` | `http://localhost:11434`, `qwen2.5:7b`, `8192` | Local chat model |
| `EMBEDDING_PROVIDER`, `RERANKER` | `hashing`, `none` | Dense embeddings and reranking |
| `SPEECH_PROVIDER`, `OCR_PROVIDER`, `VISION_PROVIDER` | `none`, `tesseract`, `evidence` | Perception for audio, images, video |
| `CHUNK_MAX_CHARACTERS`, `CHUNK_OVERLAP_CHARACTERS` | `1500`, `200` | Chunk size; reindex after changing |
| `FETCH_ALLOW_PRIVATE_HOSTS` | `false` | Allow links to local network addresses |
| `AUTH_REQUIRED`, `SECRET_KEY` | `false`, empty | Real authentication |
| `AUTO_MIGRATE` | `true` | Apply migrations at API startup |

`ANTHROPIC_API_KEY` is read by the Anthropic SDK for any Claude provider.

---

## The web app

`frontend/` is a React 19 and TypeScript app built with Vite. It has three panes: the collection's
sources, which double as the conversation's scope (ticked sources are the ones a question can draw
on); the conversation, where answers stream in and each citation is a highlighter mark; and a
passage panel that opens beside it when you follow a citation, showing the cited passage with the
sentence that supports the claim highlighted. The Library lists everything you have added and the
collections built from it. On a phone the panes become tabs.

The app talks only to the public API, and uses no state or router library beyond React. Answers
stream with `fetch` rather than `EventSource`, because the stream endpoint is a POST.

---

## Running

### Requirements

- Python 3.12
- FFmpeg and ffprobe on `PATH` (or `FFMPEG_BINARY` / `FFPROBE_BINARY`) for audio and video
- Tesseract for the default OCR provider
- Optional: Redis (Celery), PostgreSQL, Qdrant, MinIO, Ollama, a CUDA GPU for local models

### Local (Windows)

Double-click `run.bat`, or run `run.bat 8055` for another port. It uses `.venv`, checks
dependencies, starts Uvicorn with reload, and opens the API reference once `/health` responds.

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

Then open `http://127.0.0.1:8000/docs`. Without Redis, set `TASK_RUNNER=local` in `.env`.

The web app needs Node 20 or later. To work on it, run the API as above and, in a second terminal:

```bash
cd frontend && npm install && npm run dev
```

and open `http://127.0.0.1:5173`; requests to `/api` are proxied to the API (`API_URL` overrides the
default `http://127.0.0.1:8000`). To have the API serve it instead, build it once:

```bash
cd frontend && npm run build
```

The API then serves the app at `/`, and `run.bat` opens it there.

Optional backends (Whisper, BGE-M3, Qdrant, the Anthropic SDK, boto3, RapidOCR, trafilatura,
youtube-transcript-api, and Transformers for Qwen) are in `requirements-optional.txt`:

```bash
.venv/Scripts/pip install -r requirements-optional.txt
```

### Docker Compose

```bash
docker compose up --build
```

This starts the API with the web app (`:8000`), a Celery worker, PostgreSQL 16, Redis 7, MinIO
(`:9000`), and Qdrant (`:6333`). The image builds the web app in a Node stage and includes FFmpeg and
Tesseract; provider settings pass through from your shell.

---

## Testing

```bash
.venv/Scripts/python -m pytest
```

The suite runs with no network, no model weights, and no API key: model clients are replaced by
fakes, HTTP by mock transports, and test PDFs and Word files are generated on the fly. It covers
ingestion and type checking, link safety, every processor, chunking and located retrieval, the
chatbot and its providers, video analysis and confidence routing, storage, migrations, and auth.

---

## Project layout

```text
app/
  main.py          FastAPI app, middleware, routers, startup migration and job resume, health
  api/             HTTP routers, one per resource area
  core/            settings, structured logging, auth tokens, password hashing, rate limiter
  db/              engine, session factory, Alembic migrations, migration runner
  models/          SQLAlchemy models
  schemas/         Pydantic request and response models
  processors/      one processor per kind of content, producing located segments
  providers/       model contracts, factory, and the local / Claude / Ollama / Qwen / Whisper /
                   PaddleOCR implementations
  services/        ingestion, links and fetching, chunking, retrieval, chat, analysis,
                   verification, storage, evaluation
  workers/         Celery app, tasks, pipeline stages
frontend/          React web app (src/components: workspace, sources, chat, passage, library)
tests/             pytest suite
data/              SQLite database, working media, local object store (git-ignored)
alembic.ini, Dockerfile, docker-compose.yml, run.bat, requirements*.txt, .env.example
```

---

## Limitations

- **Jobs are started by the client.** Adding a source queues it, and the web app then dispatches
  it; a client using the API directly must call `POST /api/jobs/{id}/dispatch` itself.
- **The web app has no sign-in screen yet.** It uses the development identity, so turn on
  `AUTH_REQUIRED` only for API clients until one is added.
- **The defaults are baselines.** Without configuration, answers quote passages instead of
  composing them, embeddings are lexical, audio cannot be transcribed, and images are searchable
  by their OCR text only. Configure a chat model, dense embeddings, and perception providers for
  real use.
- **Live models are untested here.** Claude and Ollama are exercised through fake clients that fix
  the request shape and the grounding rules, not the quality of real answers. The same is true of
  Whisper, PaddleOCR on scanned pages, and Claude image descriptions.
- **YouTube transcripts use an unofficial library**, which can break when YouTube changes and may be
  refused from cloud IP ranges.
- **Link fetching** checks resolved addresses before connecting but does not pin the connection to
  them, so a rebinding DNS server could still steer a fetch inward.
- **Migrations are verified on SQLite only**, not yet against PostgreSQL.
- **Single-process rate limiting**, and no Prometheus or OpenTelemetry export.
