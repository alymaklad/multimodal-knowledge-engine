# How the Multimodal Knowledge Engine Works

A technical guide to the internals: how a source becomes cited evidence, how questions are answered
from it, and why each piece is built the way it is. [README.md](README.md) covers setup, the API,
and configuration; this document covers the reasoning underneath.

---

## The principles everything follows

> **Process each source once, and answer from what processing stored.** A question never sends raw
> files to a model. It searches knowledge that was extracted, located, and indexed in advance.

> **Every claim must be traceable to its evidence.** Passages remember where in the source they came
> from; answers cite passages, not whole sources; a citation that points at nothing is removed; an
> extracted value that nothing corroborates is marked as inferred and sent for review.

> **Failure is recorded, never hidden.** A stage that fails keeps what the others produced and says
> so. A chat answer that fails, is refused, or is cut off by a disconnect is stored as exactly that.

---

## 1. The life of a source

Every source has one `ProcessingJob`, and the job's `current_stage` is a small state machine. The
HTTP request only creates the job; workers move it forward.

```text
                    ┌──────────── web / youtube ────────────┐
                    │                                        ▼
ingestion ──────────┤                                     fetching ──┐
 (queued)           │                                                │
                    ├── document / image / audio / text ─────────────┤
                    │                                                ▼
                    │                                       awaiting_processor ──▶ content_extraction ──▶ completed
                    │
                    └── video ──▶ media_preparation ──▶ ocr_and_transcription ──▶ multimodal_analysis ──▶ completed
                                                         (queued)                                       or needs_review

any stage ──▶ failed  (with the reason, on the job and the source)
```

`start_source_job` (`app/workers/pipeline.py`) is the single entry point for every kind; the Celery
task `sources.start` and the local runner both call it. Every stage is **retry-safe**: it replaces
its own earlier output, so a job can be run again after a crash without duplicating anything. The
local runner relies on this to resume work at startup.

### Ingestion

- **Uploads** stream to `MEDIA_ROOT` while being hashed with SHA-256. The extension decides the kind
  (`app/services/source_types.py`); the first chunk must carry that format's signature (`%PDF-`,
  `PK\x03\x04`, `\x89PNG`, `ftyp`, …), text is decoded as UTF-8 incrementally as it streams, and a
  DOCX must contain `word/document.xml`. A mismatch is a `415`, and the partial file is deleted.
- **Links** are normalised before the duplicate check (`app/services/links.py`). Fragments and
  default ports go; every YouTube form becomes `https://www.youtube.com/watch?v=<id>`. Links with
  credentials, non-HTTP schemes, or obviously private hosts are refused up front.
- **Pasted text** is written to a file, so every kind is processed from a file on disk.
- Duplicates are detected **per owner, across the whole library** - by content hash for files and
  text, by normalised URL for links - because a source belongs to the library, not to a collection.

### Fetching

`app/services/fetching.py` runs in the worker, because opening a connection to a URL the user
supplied must have timeouts and network checks, and must not block a request.

- **Web pages.** Redirects are followed manually, one hop at a time, so each `Location` is checked
  again. Before every hop, the host is resolved and **every** returned address must be globally
  routable (IPv4-mapped IPv6 and link-local zones included); otherwise the fetch stops. Bodies are
  streamed with a hard size cap and limited to HTML, text, Markdown, and PDF. The `<title>` (or
  `og:title`) replaces the placeholder title unless the user typed one.
- **YouTube.** The caption track is fetched through `youtube-transcript-api`, choosing written
  captions in a preferred language, then generated ones, then any language, and saved as JSON with
  timestamps. oEmbed supplies the title and channel. Nothing is downloaded.

The resolution check and the connection are separate DNS lookups; pinning the connection to the
checked address would close the remaining rebinding window. For a personal install the check is the
proportionate defence, and the limitation is stated rather than hidden.

---

## 2. Processors: from a file to located segments

A processor (`app/processors/`) turns a stored file into **segments**: a passage of text, its
modality, and a **locator** saying where it came from.

| Locator key | Meaning | Example label |
|---|---|---|
| `page`, `page_end` | PDF page or span | `p. 4`, `pp. 3–4` |
| `section` | Heading path | `§ Guide > Install` |
| `paragraph` | First paragraph of the passage within its section | `¶ 2` |
| `start`, `end` | Seconds into a recording | `12:30–13:45` |
| `region` | The whole image | `image` |

**Markdown is the common format.** Word documents and web pages are converted to Markdown so that
one heading-aware splitter (`app/processors/text.py`) serves documents, pages, and pasted text
alike. A segment never crosses a heading and stays under `SEGMENT_MAX_CHARACTERS`; an overlong
section is split between paragraphs, an overlong paragraph between sentences, and an overlong
sentence at the limit.

- **PDF** (`documents.py`): PDFium extracts text per page. PDFium marks a hyphen it inserted at a
  line end with `\x02` and drops the line break; the marker is removed so "com-bines" rejoins. A page
  with fewer than `PDF_OCR_MIN_CHARACTERS` of text is a scan: it is rendered at `PDF_OCR_DPI` and
  sent to the OCR provider. A page whose OCR fails is a warning, not a failed document.
- **Web pages** (`web.py`): `trafilatura` extracts the main content when installed. The built-in
  fallback drops `script`, `style`, `nav`, `header`, `footer`, `aside`, `form`, and `title`, and
  counts only the tag that opened a skipped region, because real pages leave `<li>` and `<p>`
  unclosed and counting every tag would never return to zero.
- **Transcripts** (`media.py`): YouTube captions and Whisper output are grouped into windows that
  close at `TRANSCRIPT_WINDOW_SECONDS` or the size limit, so a citation points at roughly a minute
  of a recording.
- **Images**: OCR text plus, with `VISION_PROVIDER=claude`, a prose description (charts are asked
  for what they measure and their values). Either one alone is enough; neither is a failure with a
  hint about what to configure.

Providers are resolved **lazily** through `ProcessorContext`: a Markdown file never loads an OCR or
speech model. `process_source_content` replaces the source's segments, records a stage metric
(`degraded` when there were warnings), records provenance including the processor's models, indexes
the source, and completes it.

---

## 3. Video: multimodal analysis

Video keeps the richer pipeline the project began with.

1. **Media preparation.** `ffprobe` reads duration and whether there is audio; FFmpeg extracts mono
   16 kHz WAV and samples frames at `FRAME_SAMPLE_FPS` (capped at `MAX_SAMPLED_FRAMES`).
2. **Four perception stages that fail independently** (`app/services/analysis.py`):

   | Stage | Contract | Stored as |
   |---|---|---|
   | transcription | `SpeechProvider.transcribe(audio)` | `transcripts`, with timed segments |
   | OCR | `OCRProvider.extract(frames)` | `ocr_results`, per frame with timestamp |
   | vision | `VisionProvider.analyze_video(frames, transcript, ocr)` | `visual_analyses` |
   | extraction | `ExtractionProvider.extract(vision, transcript, ocr)` | `extracted_content` |

   A failure in one stage keeps the others: a broken OCR install does not discard a good transcript.
   A vision failure falls back to the evidence-only provider. If any stage failed outright, the run
   is `degraded` and goes to review regardless of confidence.
3. **Vision sees the whole clip.** Frames are selected evenly across the video, not from its first
   seconds, and are sent together, because what happens in a video is a sequence, not a still.

### The key-points record and its evidence

Extraction produces one domain-neutral record per video: `topic`, `key_points`, `entities`. Every
field carries an **evidence tag**: `visual`, `ocr`, `transcript`, `metadata`, or `inferred`.

Tags are **re-derived, not trusted**. Small vision models tag everything `visual`, including text
they read off the screen. `VisionFieldExtractionProvider` therefore matches each value against the
stored OCR and transcript text (lowercase alphanumerics only, because OCR drops spaces in stylised
captions) and attributes it to where it can actually be found. Values shorter than four characters
never claim corroboration, since they appear in almost any text by chance. A value nothing
corroborates keeps the model's `visual` or `metadata` claim, or becomes `inferred`.

Without a model, `RuleBasedExtractionProvider` uses the evidence's own words: distinct on-screen
text lines, then the opening transcript sentences. It never infers a topic or entities.

### Confidence routing

`app/services/verification.py` decides whether a person needs to look:

```text
confidence ≥ AUTO_ACCEPT_CONFIDENCE (0.85)         → accepted
AUTO_REVIEW_CONFIDENCE (0.60) ≤ confidence < 0.85  → secondary verification:
                                                      every scalar value corroborated by OCR or
                                                      transcript → verified, otherwise → review
confidence < 0.60, no confidence, or no record     → review
```

The decision and its reason are written to the job's event history. Reviewers accept or reject
through `POST /api/sources/{id}/review`; corrections are stored separately from model output.

---

## 4. Chunking

`app/services/chunking.py` builds retrieval chunks from a source's **passages**. For processed
kinds, passages are the segments. For video, they are the timed transcript lines, the on-screen text
(a caption visible across many frames collapses into one span with a start and end), and the visual
description.

```text
passages ─▶ group: same stream and same section ─▶ pack up to CHUNK_MAX_CHARACTERS
                                                    each chunk starts with the last
                                                    CHUNK_OVERLAP_CHARACTERS of the previous
```

- Packing **never mixes streams** (a transcript chunk never contains OCR text) and **never crosses a
  heading**, but it does cross pages and time windows. The merged locator spans them.
- A single passage longer than the limit becomes overlapping windows that end at word boundaries.
- Extracted records stay one chunk each, keyed `extracted:<type>`.
- A **summary chunk** (title, author, description, then the content, capped) is stored for
  near-duplicate detection. It is **excluded from retrieval**, because it restates the other chunks
  and would spend evidence slots on repetition.

Each chunk is stored in `content_embeddings` with its text, vector, `locator`, and `position`. The
Qdrant payload carries the locator too. Indexing a source replaces all of its chunks.

---

## 5. Retrieval

All three modes rank the **same stored chunks**, which is what lets them be fused chunk by chunk.

- **Scope.** A collection is searched through its member source ids. `source_ids` narrows that to a
  subset; ids outside the collection are a `404` that names them. Author and topic filters narrow
  the eligible sources before anything is ranked.
- **Lexical** is BM25 (k1 = 1.2, b = 0.75) over the chunks in scope, computed on the fly. It is the
  right tool for exact names and terms, which dense vectors blur.
- **Semantic** asks the vector store for the nearest chunks among the eligible sources.
- **Graph** ranks the chunks that mention an entity named in the question (weight 2) or an entity
  linked to one (weight 1), each scaled by how rare the entity is across the sources in scope, like
  IDF. An entity every source mentions says little about which one answers.
- **Hybrid** fuses the rankings with reciprocal rank fusion (k = 60). Ranks are fused, not scores,
  because BM25 scores, cosine similarities, and graph weights are on unrelated scales.
- **Reranking** runs over a wider candidate window (`RERANK_CANDIDATES`) than the page asked for,
  because its value is promoting what earlier stages ranked just below the cut.

Search results group chunks by source and keep up to three passages, each with its locator and a
label from `describe_locator`. Answer evidence stays at chunk level: hybrid, reranked, and cut to
`RAG_EVIDENCE_LIMIT` passages.

A **whole-collection question** is recognised by its wording ("main themes", "overview", "across my
sources") and its evidence is spread: the best chunk of each source first, then for any source still
missing, its most entity-dense chunk, then the rest in ranked order. Without this, "what do my
sources cover?" is answered from the two sources that happen to use those words.

### The knowledge graph

Built by `app/services/graph.py` at the end of indexing, from the same chunks that were just stored.

- The extractor (`GRAPH_PROVIDER`) reads each chunk once. The local one takes acronyms and
  identifiers (BM25, monoBERT, nDCG) and capitalised names that are not merely opening a sentence,
  and links entities named in the same or consecutive sentences. Claude and Ollama return entities
  with kinds and stated relationships ("BEIR evaluated DPR") through a JSON schema.
- An `Entity` belongs to the owner, so two sources naming DPR share one entity. Each
  `EntityMention` and `EntityRelation` records the source and chunk key it came from, which keeps
  everything the graph reaches citable.
- Reindexing replaces a source's mentions and relations; reprocessing and deletion remove them, and
  entities nothing mentions any more are pruned.
- A summary is written at the same time: the source's most entity-central sentences, verbatim, by
  default, or a model's two-to-four-sentence overview. It is shown to people and never given to the
  answer model as evidence.
- A failing extractor or summarizer is logged and skipped. The graph only adds a ranking signal, so a
  model outage leaves the source searchable rather than failing its job.

### Evaluation

`python -m app.evaluation.retrieval` runs twenty labelled questions over eight short sources in
`evals/retrieval/` through the real pipeline. Exact and paraphrase questions have one answering
passage; multi-hop questions need two passages from different sources; global questions are scored
by how many of the relevant sources appear at all. The test suite runs it and guards the current
numbers, so a chunking or ranking change that hurts retrieval fails a test.

---

## 6. Answering

### One-shot: `/ask`

`answer_collection_question` retrieves evidence and hands it to the `AnswerProvider`. Claude and
Ollama answer under the same JSON schema (`answer`, `grounded`, `cited_source_ids`); cited ids are
intersected with the ids actually retrieved, so a hallucinated source cannot reach the caller.

### Conversations

A turn (`app/services/chat.py`) runs in order and every step is an event a streaming client sees:

1. **Store the question.** The first question titles a new conversation.
2. **Rewrite the follow-up.** With history, the provider turns "and why summing?" into a standalone
   query. Old citation markers are stripped from the history first, since `[1]` meant a different
   passage in an earlier turn. A failed rewrite falls back to the question as asked.
3. **Retrieve** over the conversation's scope. Sources removed from the collection since the scope
   was set drop out of it silently.
4. **Stream the answer.** Passages are numbered `[1]…[n]` in the prompt; the model cites them inline.
5. **Resolve citations.** Each marker becomes a citation (source, chunk, locator, label, snippet) in
   order of first use. Markers outside the passage list are removed from the text: leaving them would
   show a reader a citation that leads nowhere.
6. **Store the outcome.** `complete`, `failed`, `refused`, or `interrupted`. A client that
   disconnects closes the generator; the partial answer is stored as interrupted.

One answer at a time per conversation: a second question while one is streaming is a `409`. A
message still `streaming` after ten minutes belongs to a dead worker and stops blocking.

The streaming endpoint opens its own database session, because the request's session may be closed
before the response body finishes streaming.

### The chat providers

All three receive the same prompts (`app/providers/chat_prompts.py`): answer only from the passages,
cite every claim with its passage number, say plainly when the collection does not cover the
question, and treat earlier turns as context rather than evidence.

- **`extractive`** quotes the most relevant sentence of each passage with its marker. Follow-ups
  are handled by prefixing a short question with the previous one.
- **`claude`** streams `claude-opus-5-5` through `client.beta.messages.stream` with effort set
  explicitly and `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`): a question a
  safety classifier declines is re-run on Anthropic's recommended fallback model inside the same
  stream. The final `stop_reason` is checked before the text is trusted; a refusal discards the
  partial answer and a `max_tokens` stop is reported as cut off. Rewrites run at `low` effort.
- **`ollama`** streams NDJSON from a local Ollama server with `num_ctx` set on every request,
  because Ollama's 2048-token default would silently truncate the passages. Its errors name the fix:
  `ollama pull <model>`, `ollama serve`, `OLLAMA_NUM_CTX`.

---

## 7. Data model

```text
users
collections ─┬─ collection_sources ─┬─ sources ─┬─ processing_jobs ── job_events, task_dispatches
             │   (membership)        │  (library) ├─ source_segments        (processed kinds)
             │                       │            ├─ media_assets           (video audio and frames)
             │                       │            ├─ transcripts, ocr_results, visual_analyses
             │                       │            ├─ extracted_content ── extraction_corrections
             │                       │            ├─ content_embeddings     (chunks + vectors)
             │                       │            ├─ entity_mentions, entity_relations ── entities (per user)
             │                       │            ├─ stored_objects         (durable copies)
             │                       │            ├─ processing_provenance, stage_metrics
             └─ conversations ── chat_messages
saved_searches, evaluation_cases, benchmark_runs ── benchmark_predictions
```

- A `Source` belongs to a user and has a `kind`: `document`, `web`, `youtube`, `image`, `audio`,
  `video`, or `text`. `details` holds kind-specific facts (page count, transcript language, fetched
  URL) that do not deserve a column.
- `processing_provenance` records the models that produced a source's knowledge, read back from what
  the stages stored rather than from the configuration, so it reflects the run that happened.
- The schema is versioned with Alembic. A database built by `create_all` before migrations existed
  is **adopted**: the gaps between a phase 1–4 schema and the baseline are known exactly, so they are
  filled from frozen copies of the baseline definitions (never the live models) and the database is
  stamped at revision `0001`.

---

## 8. Storage

Working files and durable copies are kept apart on purpose. FFmpeg, OCR, and PDFium need real local
files, so `MEDIA_ROOT` holds the working copies; the object store holds what is kept and served:

```text
users/{user_id}/sources/{source_id}/original.pdf       the uploaded or fetched file
users/{user_id}/sources/{source_id}/video.mp4          a video source
users/{user_id}/sources/{source_id}/audio.wav          extracted audio
users/{user_id}/sources/{source_id}/frames/frame_00001.jpg
```

The keys are identical on the local and S3 backends. Media is served with `nosniff`, and a fetched
HTML page only as a sandboxed attachment, since it is someone else's markup on this origin.

---

## 9. Where the models live, and when they load

Models load **on first use** and stay loaded for the life of the process (every loader is cached).
The peak cost is the **sum** of everything a process has touched, not the largest single model.

```text
documents, pages, text      no model (PDFium, python-docx, the splitter)
scanned PDF pages, images   OCR provider                         CPU
audio, video speech         Whisper (SPEECH_PROVIDER)            CPU by default
video frames                Qwen2.5-VL (VISION_PROVIDER=qwen)    GPU, loaded inside the vision stage
indexing                    embedding model (BGE-M3)             GPU
hybrid search               reranker (bge-reranker)              GPU, first search only
chat with CHAT_PROVIDER=…   claude: remote, no local cost
                            ollama: a separate process with its own VRAM
```

On an 8 GB card, Qwen2.5-VL-3B in 4-bit (about 3.5 GB) plus the two retrieval models in fp16
(about 1.1 GB each, `RETRIEVAL_FP16`) fit together. A local chat model through Ollama competes for
the same card, so pair it with a small model or run analysis and chat at different times. The local
task runner processes one job at a time for the same reason.

---

## 10. What is real, and what is not

Verified by the test suite, with no network, model weights, or API key:

- ingestion and content-type checks, link normalisation and the network policy, fetching through
  mock transports;
- every processor on generated PDFs and Word files, chunking, BM25 and fused retrieval, located
  citations, source scoping;
- the chat turn end to end, including failures, refusals, and disconnects; the Claude and Ollama
  providers against fake clients (request shape, fallback and effort settings, stop reasons, NDJSON);
- video analysis with fake providers, evidence attribution, and confidence routing;
- migrations: no drift from the models, reversibility, and adoption of older databases.

Not verified here:

- the quality of real answers from Claude or Ollama, which needs an API key or a running model;
- Whisper on real audio, PaddleOCR on real scans, and Claude image descriptions;
- the migrations on PostgreSQL;
- YouTube's caption endpoint, which the unofficial library depends on.
