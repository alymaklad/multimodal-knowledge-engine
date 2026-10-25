# Implementation Status

## Completed — initial Phase 1 (2026-08-28)

Implemented the first backend foundation from the architecture document:

- Created a FastAPI application with a `GET /health` endpoint.
- Added SQLAlchemy persistence with SQLite as the local default. The configuration supports switching to PostgreSQL with `DATABASE_URL`.
- Added `Collection` and `Reel` models, including processing state, processing version, timestamps, metadata placeholders, and a collection-scoped duplicate constraint.
- Implemented collection creation, listing, and retrieval endpoints.
- Implemented Reel ingestion. It validates that a submitted URL is an Instagram Reel URL, verifies the target collection exists, creates a durable `pending` Reel record, and responds with `202 Accepted`.
- Added duplicate detection for the same normalized Reel URL within a collection; repeated submissions return `409 Conflict`.
- Added endpoints to inspect a Reel and list the Reels in a collection.
- Added initial API tests covering successful ingestion, invalid URLs, and duplicate protection.
- Added local setup and API usage guidance in `README.md`, plus dependency and environment templates.

## Completed — ingestion expansion (2026-08-28)

- Added `POST /api/reels/upload` for user-provided `.mp4`, `.mov`, and `.webm` media, streamed to a generated path under `MEDIA_ROOT`. The configurable size limit defaults to 100 MiB.
- Added durable `processing_jobs` records. Every URL submission and media upload now creates a queued job with stage, retry count, timestamps, and error fields.
- Added `GET /api/jobs/{job_id}` and `GET /api/jobs/reel/{reel_id}` status endpoints.
- Added tests for queued-job creation and local video upload.

The job table is a stable handoff boundary: a later Celery worker can claim queued jobs without changing the public ingestion API.

## Completed — media preparation service (2026-08-28)

- Added a worker-ready FFmpeg/ffprobe service that probes duration, dimensions, and audio presence.
- Added audio extraction to 16 kHz mono WAV only when audio is present.
- Added bounded fixed-rate JPEG frame sampling; defaults are 1 frame/second and at most 120 frames, both configurable.
- Added FFmpeg failure handling and tests for metadata parsing and missing media.

This service is deliberately not called from an HTTP request. The worker stage claims the queued job, invokes preparation, and leaves its frame/audio artifacts ready for OCR and transcription.

## Completed — durable media-preparation worker stage (2026-08-28)

- Added persistent audio/frame media-asset records, including frame sequence and inferred sample timestamp.
- Added a retry-safe worker function that claims a job, runs media preparation, persists the generated artifacts, updates Reel duration, and advances the job to `ocr_and_transcription`.
- Worker failures preserve the Reel and write an error to both the Reel and job; retries replace the old preparation artifacts rather than duplicating them.
- Added a pipeline test covering job progression and artifact persistence.

## Completed — multimodal knowledge and retrieval foundation (2026-08-28)

- Added durable models for timestamped transcripts, OCR observations, visual analysis, and extracted structured content. Extracted content retains a searchable textual representation alongside machine-readable JSON and confidence.
- Added provider contracts for speech-to-text, OCR, video understanding, and structured extraction, so Whisper, PaddleOCR, OpenAI, Gemini, or local alternatives can be swapped without changing persistence or worker orchestration.
- Added a provider-backed analysis worker stage that persists all perception and extraction outputs, then marks the Reel and its job completed. A provider failure marks both failed with its error preserved.
- Added `POST /api/search`, a collection-scoped lexical baseline over transcript, OCR, visual, and extracted knowledge. It returns Reel URLs and matching evidence snippets.

## Superseded note — what was still required at that point

Concrete speech, OCR, vision, and extraction-provider implementations must be selected and configured. The completed provider boundary is intentionally independent of that choice; the next practical phase is to add the selected providers (for example, Whisper + PaddleOCR + a VLM) and enqueue the worker stages with Celery/Redis.

## Completed — AI reports and workout planning baselines (2026-08-28)

- Added collection reports for AI content, aggregating Reel count, analyzed-Reel count, topics, technologies, and creators from extracted knowledge.
- Added a source-cited workout planner that uses only structured exercise records already extracted from the user's collection.
- Added available-equipment filtering and sensible cycling when a requested plan needs more slots than eligible exercises.
- Added `GET /api/reports/collections/{collection_id}` and `POST /api/workouts/generate` endpoints.
- Added tests for reporting aggregation and equipment-filtered workout generation.

## Completed — containerized development foundation (2026-08-28)

- Added a Dockerfile for the FastAPI application and a Docker Compose stack with PostgreSQL, Redis, and persistent database/media volumes.
- Added PostgreSQL driver support and environment configuration for the containerized API.
- Redis is provisioned as the queue backend for asynchronous worker dispatch.

## Completed — asynchronous dispatch and job observability (2026-08-28)

- Added Celery/Redis configuration plus a containerized worker service.
- Added the `reels.prepare_media` Celery task, which invokes the media-preparation worker stage using a database session.
- Added `POST /api/jobs/{job_id}/dispatch` to enqueue newly ingested local-media jobs for preparation.
- Added append-only job events and `GET /api/jobs/{job_id}/events` so stage starts, completions, and failures remain inspectable after the task finishes.

## Completed — duplicate detection and reprocessing (2026-08-28)

- Added streamed SHA-256 hashes for user-uploaded video, preventing the same media from being uploaded twice into one collection even when filenames differ.
- Added `POST /api/reels/{reel_id}/reprocess`, which removes all derived artifacts and knowledge, retains source media, resets the Reel/job state, and records a requeue event.
- Reprocessing assigns the configured processing version, making model/prompt upgrades traceable through a fresh pipeline run.

## Completed — executable local multimodal baseline (2026-08-28)

- Added a local Tesseract OCR provider and container support for its native binary.
- Added an evidence-only local vision fallback that records frame/text availability without inventing visual facts.
- Added deterministic, evidence-first extraction for basic workout set/rep patterns and selected AI terms.
- Chained the Celery preparation task into the local analysis task, allowing an uploaded video to advance automatically through the complete currently configured pipeline.

The next quality phase is a concrete speech-to-text provider and a VLM provider; their contracts and persistence paths already exist. (Both were added in the slices below: speech with the retrieval work, the VLM with the visual-understanding work.)

## Completed — auditable knowledge inspection (2026-08-28)

- Added `GET /api/reels/{reel_id}/analysis`, scoped to the owner.
- The endpoint returns transcript segments, timestamped OCR observations, visual analysis metadata, and all structured extracted content with confidence values.
- This supplies the provenance/audit surface needed for source-aware search answers and human review.

## Completed — ten API and operational hardening phases (2026-08-28)

1. Added collection update support.
2. Added collection deletion.
3. Added owner-scoped Reel deletion with constrained local-media cleanup.
4. Added offset/limit pagination for collection and Reel listings.
5. Added automatic `needs_review` routing for absent or low-confidence extracted knowledge.
6. Added job retry for failed or review-required jobs.
7. Added a dispatch audit event containing the Celery task ID.
8. Added a database-backed readiness endpoint alongside liveness health.
9. Added request correlation IDs on every HTTP response.
10. Added a capability-discovery endpoint documenting active providers and limitations.

## Completed — ten collection, review, and operations phases (2026-08-28)

1. Added Reel creator/caption metadata updates.
2. Added Reel status filtering.
3. Added explicit accept/reject review decisions for `needs_review` Reels.
4. Added review events to the existing append-only job history.
5. Added user-scoped queue/job listing.
6. Added event-history pagination.
7. Added collection JSON export including structured knowledge.
8. Improved collection deletion to remove owned local media before database cascade deletion.
9. Added configurable CORS allowlisting for browser clients.
10. Added configured processing confidence review controls through `AUTO_REVIEW_CONFIDENCE`.

## Completed — twenty evaluation, quality, and workflow phases (2026-08-28)

1. Added a persistent evaluation-case dataset.
2. Added per-user evaluation-case creation.
3. Added owner-checked linking of cases to collections.
4. Added owner-checked linking of cases to Reels.
5. Added prediction submission for a case.
6. Added deterministic exact-field scoring.
7. Added aggregate evaluation accuracy summaries.
8. Added category-level evaluation accuracy.
9. Added paginated evaluation-case listing.
10. Added `scored`/`pending` evaluation state.
11. Added content-type constrained search.
12. Added minimum-score search filtering.
13. Added target-muscle workout filtering.
14. Added no-repeat workout-plan behavior by default.
15. Added a user-scoped manual-review queue.
16. Added review-queue size to operational metrics.
17. Added extraction-content type counts to reports.
18. Added average extraction confidence to reports.
19. Added job-event filtering by event type.
20. Added bounded Celery retry configuration for preparation and analysis tasks.

## Completed — twenty human-quality and portability phases (2026-08-28)

1. Added append-only human extraction corrections.
2. Preserved original model values when recording corrections.
3. Added correction notes.
4. Added owner-scoped correction listings.
5. Added collection content-type facets.
6. Added AI topic facets.
7. Added technology facets.
8. Added workout muscle facets.
9. Added collection CSV export.
10. Enriched JSON exports with content type and confidence.
11. Added evaluation-case category filtering.
12. Added evaluation-case deletion.
13. Added safer dispatch rejection for URL-only records without local media.
14. Added filtering-friendly review queue support.
15. Added correction provenance without overwriting extracted JSON.
16. Added data-quality notes to project documentation.
17. Added portable knowledge export documentation.
18. Added content confidence to exported knowledge.
19. Added owner checks for all correction workflow actions.
20. Added owner checks for all facet and export actions.

## Completed — twenty repeatable retrieval and operational-control phases (2026-08-28)

1. Added saved-search persistence.
2. Added saved-search creation.
3. Added saved-search listing.
4. Added saved-search execution.
5. Added saved-search deletion.
6. Preserved saved content-type filters.
7. Preserved saved minimum-score filters.
8. Added Celery dispatch-record persistence.
9. Added per-job dispatch inspection.
10. Added queued-task cancellation.
11. Added cancellation history events.
12. Added dispatch cancellation state.
13. Added retry dispatch records.
14. Added collection metadata cloning.
15. Added full user JSON export.
16. Added portable user backup documentation.
17. Added explicit URL-only dispatch rejection.
18. Added worker task-name provenance.
19. Added owner checks for all saved-search actions.
20. Added owner checks for all dispatch-control actions.

## Completed — embeddings, vector retrieval, and grounded answering (2026-08-28)

This slice closes the largest remaining architecture gap: sections 27, 28, 30, 32, 43, and 44 were
specified but had no implementation, and section 46's near-duplicate case was unaddressed.

**Embedding pipeline (§27)**

- Added `content_embeddings`, storing each vector next to the text it was generated from, its
  dimensions, and the producing model, so an index built by one model is never silently mixed with
  another's.
- Added an `EmbeddingProvider` contract with two implementations: a dependency-free hashed
  vectoriser (word unigrams plus character 4-grams, L2 normalised) and a sentence-transformers
  provider that loads BGE-M3. The hashed provider is deliberately labelled a *lexical* signal
  projected into vector space — it makes indexing, fusion, and retrieval executable and testable
  without model weights, and it is not a substitute for a semantic model.
- Each modality is embedded as its own chunk — transcript, OCR, visual, every extracted content
  type, and a whole-Reel summary — so a retrieval hit can be attributed to the evidence that
  produced it rather than to the Reel as an opaque blob.

**Vector store boundary (§28)**

- Added a `VectorStore` abstraction with a relational backend and a Qdrant backend.
- The relational backend performs exact brute-force cosine search. For a personal collection that
  is the better trade than an approximate index: recall is perfect and there is no second system to
  keep consistent with PostgreSQL.
- The Qdrant backend uses the architecture's collection-scoped payload filters and mirrors chunk
  text into the database, so evidence snippets and answering keep working regardless of backend.

**Hybrid retrieval and search modes (§30, §43)**

- `POST /api/search` gained a `mode` field: `lexical` (unchanged default), `semantic`, and `hybrid`.
- Hybrid combines the two rankings with reciprocal rank fusion rather than adding scores, because
  lexical occurrence counts and cosine similarities are not on a comparable scale — only their
  orderings can be fused meaningfully.
- `SearchResult.score` became a float and gained `match_type`, so a caller can tell which retriever
  produced a result.
- Saved searches now persist their retrieval mode and rerun the way they were tuned.

**Grounded answering (§32, §44)**

- Added an `AnswerProvider` contract and a local extractive implementation that composes an answer
  only from retrieved evidence, cites the Reel URL behind every line, and returns an explicit
  "no evidence" response instead of guessing when the collection holds nothing relevant.
- Added `POST /api/collections/{collection_id}/ask`, returning the answer, a `grounded` flag, the
  answering model, and the cited sources.
- Evidence retrieval falls back to the lexical baseline when a collection has not been indexed yet,
  so answering still cites real sources rather than failing.

**Transcription provider (§16)**

- Added a `faster-whisper` speech provider that preserves per-segment start/end times. Timed
  segments matter downstream: OCR observations already carry frame timestamps, so both modalities
  can be aligned to the same moment in a Reel.
- The worker now resolves its speech provider from configuration instead of hardcoding "disabled".
  `SPEECH_PROVIDER` defaults to `none`, which keeps the pipeline runnable with no model weights.

**Near-duplicate detection (§46)**

- Added `GET /api/collections/{collection_id}/duplicates`, comparing whole-Reel summary vectors.
- Exact re-uploads were already blocked at ingestion by URL and media hash; this catches what that
  cannot — the same content saved from a different post, or a re-encoded copy.

**Index lifecycle**

- The analysis worker stage indexes a Reel as part of completing it.
- `POST /api/collections/{collection_id}/index` backfills a collection after a model change;
  `POST /api/reels/{reel_id}/index` re-indexes one Reel.
- Reprocessing deletes a Reel's vectors alongside its other derived knowledge, and Reel deletion
  removes them by cascade, so a stale vector can never outlive the evidence it came from.

**Operations and documentation**

- `GET /api/capabilities` now reports the configured providers and derives its limitations list
  from actual configuration, so a client is never told a capability is available when it is not.
- Added a Qdrant service to Docker Compose, retrieval settings to `.env.example`, and
  `requirements-optional.txt` for the heavier model dependencies, keeping the default install fast.
- Added 10 tests covering embedding determinism and normalisation, semantic and hybrid ranking,
  grounded answering, refusal without evidence, near-duplicate pairing, ownership isolation on the
  new endpoints, and vector cleanup on reprocess and delete.

## Completed — visual understanding, structured extraction, and confidence routing (2026-08-28)

This closes the gap flagged as largest at the end of the previous slice. Sections 19, 20, 21, 22,
23, 25, and 26 were specified but unimplemented: the pipeline could see that frames existed but not
what was in them.

**Vision-language provider (sections 19-21)**

- Added a Claude-backed `VisionProvider` that sends multiple frames in one request with the OCR text
  and transcript alongside them.
- Frames are selected evenly across the whole Reel rather than taken from its opening. A movement
  (standing, descending, bottom, standing) cannot be identified from one image, and the first N
  frames would only cover the first few seconds. `VLM_MAX_FRAMES` bounds the request size.
- The response is constrained by a JSON schema through `output_config`, so the worker never parses
  prose into fields.
- With no frames sampled, the provider returns zero confidence and says so rather than calling the
  model with nothing to look at.

**Prompt strategy (section 20)**

- The prompt asks for specific fields, not "what is in this video".
- "Do not invent information" and "return null rather than guessing" are stated in both the vision
  and extraction prompts, and the schema types every optional field as nullable so the model has a
  legal way to say "not stated".

**Evidence provenance (section 22)**

- Every extracted field is tagged with the evidence type behind it: visual, ocr, transcript,
  metadata, or inferred.
- The tags live in the record's `data.evidence` map, which the existing
  `GET /api/reels/{reel_id}/analysis` audit endpoint already exposes - no schema change needed.
- The deterministic local extractor now tags its fields the same way, so provenance is not a
  property only the paid path has.

**LLM structured extraction (section 25)**

- Added a Claude-backed `ExtractionProvider` that normalises vision output plus OCR and transcript
  into `workout_exercise` and `ai_knowledge` records, each with a text representation for embedding
  and its own confidence.
- Perception and normalisation stay separate stages, as the architecture specifies, so either can
  be swapped without touching the other.

**Confidence-aware processing (section 26)**

- Replaced the single review threshold with three tiers: accept above `AUTO_ACCEPT_CONFIDENCE`
  (0.85), verify between the thresholds, review below `AUTO_REVIEW_CONFIDENCE` (0.60).
- Of the verification strategies the architecture lists, this implements the cheapest honest one:
  mid-confidence values are checked against the literal OCR and transcript text already stored. A
  value spelled out on screen or spoken aloud is corroborated and the Reel completes; one resting on
  inference alone goes to a human. Running a second, stronger model fits the same interface.
- The decision and its reason are recorded as a `confidence_verification` job event, so a routing
  outcome can be explained after the fact rather than inferred from the final status.

**Silent video handling (section 23)**

- With no speech provider the transcript is simply empty and the pipeline proceeds on OCR plus
  vision, which is the case the architecture calls out. A test covers a silent Reel whose sets and
  reps exist only as on-screen text: it is corroborated by OCR and completes without review.

**Provider resolution**

- Added a single factory that resolves OCR, vision, and extraction providers from configuration, so
  the worker no longer imports a concrete implementation. An install without the optional model
  packages still runs the entire pipeline on its local defaults.
- Model imports are deferred into the factory branch that needs them, so a missing `anthropic`
  package cannot break startup for someone running the local baseline.

**Operations and documentation**

- `GET /api/capabilities` now reports the active vision and extraction providers, the evidence
  types, and the configured confidence thresholds.
- Added provider settings to `.env.example`, the `anthropic` SDK to `requirements-optional.txt`, and
  provider environment passthrough to Docker Compose.
- Added 15 tests: frame selection spanning the Reel, both Claude providers driven through a fake
  client (asserting frame count, media types, schema, the no-invention instruction, and that a
  silent Reel's transcript is labelled as absent), empty-record handling, every confidence tier,
  corroborated and uncorroborated mid-confidence routing, factory defaults and rejection, and two
  end-to-end worker tests covering the verified and review paths.

## Completed — composed answering, aggregate reports, and processing provenance (2026-08-28)

Sections 32, 41, 44, and 45. The retrieval and perception layers were in place; what remained was
the language layer on top of them, plus the record of which models produced any given result.

**Claude answering provider (sections 32, 44)**

- Added a second `AnswerProvider` implementation that composes over retrieved evidence rather than
  quoting one sentence per Reel. Both providers satisfy the same contract; `ANSWER_PROVIDER` selects.
- Grounding is enforced structurally, not by prompt alone. The retrieved evidence is the only
  material supplied, the system prompt forbids outside knowledge, and **the citations the model
  returns are intersected with the evidence that was passed in** - a Reel id the retriever never
  produced is dropped, and an answer left with no valid citation is downgraded to ungrounded.
- With nothing retrieved the provider skips the request entirely: there is no answer that could be
  grounded, so spending a call to discover that is waste.
- The refusal text is shared with the extractive provider, so both paths decline identically.

**Aggregation-first report generation (section 45)**

- Added `GET /api/reports/collections/{collection_id}/narrative`, which builds the existing database
  aggregates and then narrates *only those aggregates*.
- The model never receives the Reels themselves, so report cost stays flat as a collection grows -
  the specific property section 45 asks for. A test asserts transcript text does not reach the
  prompt while the aggregate keys do.
- Without a narration provider the endpoint still returns the aggregates, marked `narrated: false`,
  rather than padding them with invented prose.

**Processing provenance (section 41)**

- Added a `processing_provenance` record per Reel: transcription model, vision model, embedding
  model, OCR and extraction providers, and the extraction prompt version, alongside the processing
  version.
- The record is built by reading back what the stage persisted rather than what configuration says,
  so it reflects the run that happened, not the configuration as it stands when someone inspects it
  later. This is what makes an evaluation result attributable to the setup that produced it.
- Delivered as a new table rather than columns on `reels`, because this project creates schema with
  `create_all` and has no migration tool: a new table applies cleanly to an existing database, a new
  column would not.
- Reprocessing deletes the provenance of the discarded run alongside its other derived artifacts,
  and `GET /api/reels/{reel_id}/analysis` returns it.

**Consolidation**

- Answer-provider resolution moved into the single provider factory, so every provider kind is now
  resolved in one place.
- Renamed the shared Claude client and structured-response helpers to public names, since a second
  module needs them.

**Operations and documentation**

- `GET /api/capabilities` now reports the answering provider, whether narration is available, and
  the provenance fields recorded per Reel.
- Added `ANSWER_PROVIDER` and `EXTRACTION_PROMPT_VERSION` to `.env.example` and Docker Compose.
- Added 12 tests: citation filtering (unretrieved id dropped, partial subset kept), the model's own
  ungrounded verdict honoured, the no-evidence short circuit, factory default and rejection, report
  narration receiving aggregates but not transcripts, the un-narrated fallback, owner scoping,
  end-to-end provenance capture through the worker, and provenance cleanup on reprocess.

## Completed — durable object storage (2026-08-28)

Architecture section 8, plus the "signed object-storage URLs" requirement from section 47.

**The design decision that shaped this**

FFmpeg, Tesseract, and the vision model all need real local files, so an object store cannot simply
replace `MEDIA_ROOT`. Working files and durable storage are treated as different things: `MEDIA_ROOT`
holds the copies a stage reads while it runs, and the object store holds what survives, what gets
served, and what a worker on another host could fetch. Publication happens at the end of the
preparation stage, once the artifacts exist.

**Object store boundary**

- Added an `ObjectStore` abstraction with a local filesystem backend and an S3 backend that also
  covers MinIO.
- Keys follow the documented layout — `users/{user_id}/reels/{reel_id}/video.mp4`, `audio.wav`,
  `frames/frame_00001.jpg` — and are **identical across backends**, so moving from local disk to S3
  does not rewrite them.
- The local backend refuses a key that would resolve outside its root. Keys are built internally, so
  this is defence in depth rather than a live threat, but a traversal would write outside the store.
- The S3 backend issues presigned URLs bounded by `SIGNED_URL_TTL_SECONDS`. The local backend
  returns `None` instead of pretending, and the API falls back to streaming the object through its
  own ownership check — so an object is never reachable without authorization on either backend.

**Publication**

- Added `stored_objects`, recording key, backend, content type, size, and frame sequence per
  published artifact. Kept separate from `media_assets`, which records the *working* local files;
  conflating the two would lose the distinction that motivated the design.
- Delivered as a new table for the same reason as the provenance record: this project creates schema
  with `create_all` and has no migration tool, so a new table applies cleanly to an existing database
  where a new column would not.
- Publication is idempotent. Republishing replaces the Reel's prior object records rather than
  accumulating entries pointing at frames a newer run has already overwritten.
- A working file that no longer exists is counted as skipped, not failed: a stage can fail part way,
  and that is not a publication error.
- The preparation stage records a `media_publication` job event with the object count and backend.

**API and lifecycle**

- `GET /api/reels/{reel_id}/media` lists a Reel's objects with a signed URL where the backend can
  issue one, and an API path where it cannot.
- `GET /api/reels/{reel_id}/media/{object_id}` streams an object under the same ownership check as
  every other endpoint.
- `POST /api/reels/{reel_id}/publish` republishes after a backend change or a failed publication.
- Reel and collection deletion remove the stored prefix; reprocessing discards the previous run's
  object records. Storage cleanup on delete is best effort — a backend outage must not block a user
  from deleting their own data.

**Operations and documentation**

- Added a MinIO service and a separate object volume to Docker Compose, storage settings to
  `.env.example`, and `boto3` to `requirements-optional.txt`.
- `GET /api/capabilities` reports the active backend, the key layout, and whether signed URLs are
  available.
- Added 11 tests: key layout and content-type mapping, local round trip and delete, traversal
  refusal, absent-prefix deletion, factory rejection, full publication, the skipped-file path,
  republication idempotency, the media endpoints with ownership enforcement on all three, and
  object cleanup on reprocess. Updated the preparation-stage test for the new publication event.

## Completed - graceful degradation and pipeline observability (2026-08-28)

Architecture sections 49 and 50. Until now a single provider exception failed the whole Reel, which
directly contradicts "do not discard the entire Reel because OCR failed".

- Rewrote the analysis stage so transcription, OCR, vision, and extraction each fail independently.
  A failing OCR pass no longer discards a Reel whose speech transcribed perfectly, and vice versa.
- Vision failure falls back to the local evidence provider before giving up; when both fail, the
  remaining stages still run on OCR and transcript evidence.
- A failed extraction leaves the previous run's knowledge in place. Erasing good knowledge because a
  later run broke would be a worse outcome than a stale record.
- A run where any stage failed outright goes to review regardless of the surviving extraction's
  confidence. Partial evidence should not be indistinguishable from a clean run.
- A silent Reel records transcription as *skipped*, not failed, so an intentional absence never
  looks like a fault.
- Added `stage_metrics`, appended per attempt rather than overwritten, so a retry's improvement over
  the previous run stays visible.
- Added `GET /api/system/metrics/pipeline`: per-stage run counts, failure rates, average and p95
  latency, items processed, and models used. Skipped stages are excluded from the failure-rate
  denominator, and the percentile is nearest-rank because interpolating over a handful of runs would
  invent precision.
- Added structured JSON logging with the request id carried in a context variable, so a line written
  deep in a service still identifies the request that caused it.

**Fixed: the test suite was not isolated.** It ran against `data/instagram_knowledge.db`, the
developer's own database, and accumulated rows across every invocation - `test_api.py` deleted a
`data/test_knowledge.db` the application never used. Any aggregate assertion therefore depended on
how many times pytest had been run. Added `tests/conftest.py`, which points the database, media
root, and object store at a throwaway directory before `app.db.database` is imported. Consecutive
runs now produce identical results, and the development database is left alone.

## Completed - search filters, reranking, and model routing (2026-08-28)

- Added `creator` and `topic` filters to all three search modes, completing the "filter by
  collection/creator/topic" bullet in the architecture's own definition of done. Topics come from
  extracted knowledge, so a filter matches what analysis actually found rather than a free-text tag.
- Results now carry their creator and topics back, so a client renders filter chips without a second
  request. Creator matching ignores a leading `@`.
- Semantic search over-fetches when a filter is active, because filtering after retrieval would
  otherwise return a fraction of a page.
- Saved searches persist the new filters alongside the mode.
- Added the reranking stage that follows fusion in section 43: a `RerankProvider` contract, an
  identity default that preserves fusion order, and a cross-encoder implementation. Reranking scores
  a wider candidate window than the requested page, because its whole value is promoting something
  ranked just below the cut. A provider returning a mismatched score vector is ignored rather than
  trusted.
- Added model routing (section 52). A cheap classifier over evidence that already exists - frame
  count, presence of speech, presence of on-screen text - picks the tier before any model is called,
  because a router costing a model call to decide which model to call defeats its own purpose.
  Escalation to the stronger model is capped at one retry: a second opinion is worth paying for, a
  third usually pays twice to learn the same thing. The decision and its reason are recorded in the
  visual analysis.

## Completed - authentication, rate limiting, and the web interface (2026-08-28)

Architecture sections 47 and 65.

**Authentication**

- Added accounts with PBKDF2-HMAC-SHA256 password hashing and a per-user salt, using only the
  standard library so the project stays installable without a compiler. The iteration count is
  stored inside the hash, which is what allows raising it later without invalidating passwords.
- Tokens are HMAC-signed payloads with an explicit expiry rather than JWTs - the same guarantees for
  this use, no dependency, and no algorithm-confusion footgun. Signature comparison is constant time.
- `AUTH_REQUIRED` selects the mode. Off, a bearer token is honoured and `X-User-Id` remains the
  zero-setup development identity. On, `X-User-Id` is ignored entirely, because a header any client
  can set freely is not authentication. Every token is re-checked against a live, active account, so
  a token cannot outlive the account it names.
- `SECRET_KEY` is required when authentication is on, and startup fails loudly without it rather
  than signing with a per-process key that would silently break across workers.
- Login answers identically for a wrong password and an unknown account, so it cannot be used to
  enumerate registered addresses.
- Added a fixed-window rate limiter, disabled by default. It is documented as per-process: with N
  workers the effective limit is N times the configured one, so multi-instance deployment needs the
  counter in Redis. Stating that here beats discovering it in production.

**Web interface**

- Added `GET /app`, a dependency-free single-page client: collections with Reel counts, Reel
  submission, recent processing, search with mode and filter controls, grounded answering with
  sources, reports, workout generation with source links, sign-in, and pipeline metrics.
- It calls the same public API as any other consumer, so there is no private rendering path to keep
  in sync. All interpolated values are HTML-escaped.
- Verified end to end in a real browser against a running server: collections rendered, hybrid
  search returned the expected Reel with creator and topic chips, answering returned a grounded
  answer with sources, the report rendered its un-narrated notice, workout generation produced a
  plan linking back to its source Reel, and register/sign-out switched identity correctly. No
  console errors; no horizontal overflow at 375px.
- **Fixed an N+1 found during that verification**: the client issued one count request per
  collection. `GET /api/collections` now returns `reel_count` from a single grouped query. No
  existing test covered that endpoint's response body - the new test does.

## Completed - model benchmarking and ablation evaluation (2026-08-28)

Architecture sections 54 and 55, which are step 25 of the recommended implementation order. Found by
auditing the project against that order rather than by assuming the previous slice had finished it:
the previous status note claimed the definition of done was met while this was still missing.

**Why the existing evaluation was not enough**

Cases stored one prediction each, with no record of which model or configuration produced it. That
is sufficient to answer "is extraction accurate" and insufficient for either question these sections
ask - which configuration is best, and what did a given change buy.

**What was added**

- `benchmark_runs`: a named configuration, with the model set and ablation flags as free-form JSON,
  unique per label per user.
- `benchmark_predictions`: one answer per case per run, with its score, latency, and cost.
  Re-submitting a case replaces its answer rather than duplicating it.
- `GET /api/evaluations/runs/{run_id}` reports accuracy, per-category accuracy, average latency, and
  total cost - the metrics section 54 asks to benchmark alongside accuracy.
- `GET /api/evaluations/runs/compare?run_ids=...` produces the section 54 table, ranked by accuracy.
- With exactly two runs it also returns an ablation delta: accuracy change, improved/regressed/
  unchanged case counts, and the settings that differed. Running the same silent workout cases with
  `uses_ocr` on and off is what turns section 55's question into a number.

**The decision worth naming**

Comparisons are computed over the **intersection** of cases both runs scored. Comparing a run that
covered 50 cases against one that covered 5 would otherwise report a difference in coverage as a
difference in quality - the most likely way a benchmark quietly lies. A test covers exactly that:
two runs both perfect on their one shared case report a zero delta even though one covered twice as
many cases.

Added 11 tests: accuracy/latency/cost recording, re-submission replacing rather than duplicating,
duplicate label rejection, the section 54 ranking table, the section 55 OCR ablation, the shared
subset rule, regression reporting, the two-run minimum, owner scoping across all four operations,
refusal of a prediction for another user's case, and cascade deletion.

## Completed - client rebuilt against the exported design system (2026-08-28)

Designed the interface in Google Stitch, exported the HTML, and rebuilt `/app` against it. The
exports are static mockups with hardcoded data; the client is a working application, so the designs
were used as the source of the visual language rather than pasted in.

**What was taken from the exports**

- The design tokens: dark canvas, lifted panels, hairline borders, and the status palette where
  amber is degradation and red is hard failure.
- Space Grotesk / Inter / IBM Plex Sans, with tabular figures everywhere a number is compared.
- The component vocabulary: stat tiles, status pills, filter chips, match-type chips, evidence
  snippets with a left accent bar, the timestamped timeline, and the metric table.
- The `inferred` evidence tag rendered weaker than the others - dashed border, lower contrast.

**What was rebuilt rather than copied**

- The exports load Tailwind from a CDN. The client keeps its hand-authored CSS so it stays
  dependency-free and works offline; only Google Fonts is external, with full fallback stacks.
- Every screen is wired to the live API. Nothing on screen is hardcoded.

**New in the client**

- Sidebar navigation replacing the previous top tabs, with three views the old client lacked:
  Review Queue (accept/reject on `needs_review` Reels), Pipeline (stage latency, failure rates, and
  the stage-sequence strip), and a fuller Account view including configured capabilities.
- A **Reel audit drawer** reachable from every surface that shows a Reel, rather than a separate
  page. Provenance should be one click from wherever the Reel appeared.
- The drawer's confidence block mirrors the worker's three tiers exactly, so the UI never implies a
  different decision than the one the pipeline made.
- Search gained the mode segmented control with a per-mode explanation, the creator/topic/content
  filters, and an index rebuild action.

**Two fixes found while verifying in a browser**

- Asset URLs are now versioned. The static mount is cached aggressively, and the first reload served
  fresh markup with a stale script - which fails in a way that looks like a code bug rather than a
  cache one.
- The workout view first labelled repeated exercises "not enough eligible exercises". That
  misattributed the cause: an eligible exercise was going unused. With `allow_repeats` off the
  planner reuses the same top slice on every day regardless of how many candidates remain, so the
  label now states the fact ("Repeated from an earlier day") without inventing a reason.

Verified against the running demo database: search returned results with match-type, creator, and
topic chips; the audit drawer showed five evidence tags with one dashed `inferred`, confidence 0.90
marked Accepted, the three-row OCR timeline, and all seven provenance fields; pipeline rendered
three tiles, four stage rows, and four sequence nodes; the review queue and workout plan rendered
from live responses. No console errors, all requests 200, no horizontal overflow at 375px.

## Completed - local models installed and wired (2026-08-28)

The provider contracts finally have real models behind them, running on local hardware
(RTX 4070 Laptop, 8 GB VRAM). Everything below was measured on that machine, not assumed.

**What now runs**

| Slot | Model | Measured |
|---|---|---|
| OCR | PP-OCRv4 via RapidOCR | 0.979 confidence, ~2.2 s/frame |
| Speech | distil-large-v3 (faster-whisper) | 1.6 s for 8 s audio on CUDA; 42.2 s on CPU |
| Vision | Qwen2.5-VL-3B, 4-bit | 3.6 GB peak VRAM, ~13 s/Reel warm |
| Embeddings | BAAI/bge-m3 | 1024-dim |
| Reranker | BAAI/bge-reranker-v2-m3 | 0.877 vs 0.000 discrimination |

**New providers**

- `app/providers/paddle_ocr.py` - scene-text OCR with two backends, preferring RapidOCR because
  PaddlePaddle installs badly on Windows. Caption lines are kept newline-separated (they are separate
  claims, not one sentence) and lines below 0.5 confidence are dropped so decorative artefacts do not
  reach the extractor looking authoritative.
- `app/providers/qwen_vision.py` - local multi-frame vision behind the existing `VisionProvider`
  contract, reusing the Claude provider's no-invention rule so both paths ask for the same thing.

**Chose 3B over the 7B originally recommended.** On 8 GB, 7B in 4-bit is ~5.5 GB and leaves little
room for frame KV cache. A model that always fits beats a larger one that OOMs on a long Reel; 7B is
one environment variable away.

**Four bugs found only by running the models**

1. `Path.as_uri()` raised on relative paths - and `MediaAsset.path` is stored relative, so this would
   have failed in the real pipeline, not just in testing. The URI round-trip was removed entirely.
2. Pasting the JSON Schema into the prompt made the model echo the schema shape back
   (`{"type": "object", "properties": {...}}`), so every field parsed as empty.
3. Replacing the schema with a worked example caused a **worse** bug: the model copied the example's
   values, reporting `reps: "8-10"` when the OCR clearly read `4 x 8 REPS`. That is fabricated
   knowledge - the one failure this pipeline exists to prevent. The prompt is now a typed field guide
   with no plausible values to copy, and the parser unwraps a schema-shaped reply as defence in depth.
4. The model tagged evidence on fields it left null, claiming a source for a fact never extracted.
   Evidence tags are now pruned to fields that actually hold a value.

**Two silent failure modes closed**

- Switching `EMBEDDING_PROVIDER` changes vector width (256 -> 1024). `cosine_similarity` returns 0.0
  on a length mismatch, so every previously indexed chunk would have vanished from results - looking
  like "no matches" rather than "this collection needs reindexing". The vector store now skips
  mismatched rows and logs how many, naming the reindex endpoint.
- The new `.env` leaked into the test suite and broke three tests that assert default provider
  behaviour. The env file path is now indirected through `ENV_FILE`, and `conftest.py` points it at a
  file that does not exist, so tests are isolated from local model configuration the same way they
  are isolated from the developer's database.

**Performance**

Heavy model loads are cached per process (`lru_cache`) in the embedding, reranker, speech, and vision
providers. Before this, `get_embedding_provider()` reloaded BGE-M3 on every search and every indexed
Reel, because the factory constructs a provider per call.

**Known model-quality limits, not code defects**

- Qwen2.5-VL-3B reported `confidence: 1.0` on a crude synthetic clip. Confidence drives the
  three-tier routing, so a model that always claims certainty makes the review queue inert. Treat its
  confidence as unvalidated until benchmarked.
- It also volunteered a technique note ("keep your back straight") that was not visible in the
  frames. Sensible advice, but not evidence - which is what the benchmark harness and review queue
  exist to catch.

Extraction and answering remain the deterministic local paths; the vision model fills the structured
fields directly. A local LLM extraction provider is the remaining gap.

## Completed - extraction reads the vision model's fields (2026-08-28)

The vision model already returned `exercise`, `sets`, `reps`, `equipment` and the rest as typed
fields, and the rule-based extractor threw them away and ran regexes over the description prose. On a
real Reel that produced `exercise: "performed by an animated character"` - a fragment lifted out of a
caption. `app/providers/structured_extraction.py` reads the fields directly, removing a lossy round
trip through English.

**Evidence tags are re-derived, not trusted.** Qwen2.5-VL-3B labelled every field `visual`, including
reps that came from on-screen text and a technique note quoted verbatim from the narration. Since the
OCR and transcript text are both available at extraction time, each value is now checked against them
and attributed to where it can actually be found; the model's own claim is used only when neither
corroborates it, and otherwise the field is marked `inferred`. A value nothing corroborates is
precisely the one a reviewer should question, so it must not inherit a confident-looking tag.

**Attribution is whitespace-insensitive.** PP-OCR read a caption as `BULGARIANSPLITSQUAT` while the
model wrote `Bulgarian Split Squat`. Those are the same evidence, and a plain substring test called
the second unattributable - mislabelling an on-screen value as `visual`. Comparison now normalises to
lowercase alphanumerics, because spacing is an OCR artefact rather than a difference in meaning.
Values shorter than four characters are still never treated as corroborated: `3` appears in almost
any transcript, and matching on it would invent provenance.

**Result on the end-to-end Reel** - three modalities, each correctly attributed:

| Field | Value | Evidence |
|---|---|---|
| exercise | Bulgarian Split Squat | `ocr` |
| reps | 12 each leg | `ocr` |
| technique_notes | Keep the front shin vertical... | `transcript` |
| sets / equipment / target_muscles | 3 / elevated platform / glutes, quads, hamstrings | `visual` |

Confidence is passed through from the vision model unchanged. It is a claim, not a measurement - this
model reports high confidence readily - which is why the evidence tags are derived independently.

The regex extractor remains as the fallback: a vision provider that returns only a description (the
local `evidence` provider) still needs something to read. Selected with
`EXTRACTION_PROVIDER=vision_fields`; 12 tests cover field reading, re-attribution, the spacing case,
the short-value guard, both record types, and the fallback path.

## Tested on a real Instagram Reel (2026-08-28)

First run against genuine content rather than synthetic fixtures: a 33-second vertical Reel about an
open-source AI job-search tool, downloaded by the user and uploaded through `/api/reels/upload`.

**Perception worked well.**

| Stage | Result | Time |
|---|---|---|
| Transcription | Accurate: "Some guy got laid off and he had Claude Code run his entire job search. Over 36,000 stars on GitHub, open sourced... It's three commands." | 16 s |
| OCR | Read the GitHub UI out of a screen recording: `sLorentzen/ai-job-search`, `Issues 3`, `Pull requests`, `master` | 70 s / 33 frames |
| Vision | Described the video accurately in prose | 88 s |

**Two bugs found and fixed**

1. *Valid output was being thrown away.* The vision model returned a well-formed object, but the
   parser rejected it with "Invalid control character": the model had quoted the narration into a
   field and left the newlines unescaped, which strict JSON refuses even though the structure is
   sound. Now parsed with `strict=False`, recovering a correct answer instead of discarding it over
   whitespace encoding.
2. *A fabricated topic.* The fallback extractor recorded `topic: "RAG"` for a video that never
   mentions RAG - it matched the term as a **substring** inside a word like "leverage". That is the
   precise failure this project exists to prevent: a confident record with no basis. Term matching
   now uses word boundaries.

**The remaining limitation is structured output, and it is a model limitation.**

Qwen2.5-VL-3B does not reliably emit valid JSON free-form. Across runs on the same Reel it produced,
in turn: a schema-shaped echo, a repetition loop, unescaped control characters, and finally
single-quoted strings with an unterminated array. Each individual break is patchable, but patching
them one at a time means writing an ad-hoc JSON repairer - and a repairer has to *guess at intent*,
which risks inventing values. That is worse than failing.

The correct fix is constrained decoding (lm-format-enforcer, Outlines, or XGrammar), which makes
schema-invalid output unrepresentable rather than detected after the fact. Not yet implemented.

**The safety design held.** With unparsable vision output the pipeline recorded `confidence: None`,
extracted **zero** records rather than guessing, and routed the Reel to `needs_review`. Nothing was
fabricated, and the failure is visible in the audit view rather than hidden behind a plausible
record. That is the intended behaviour under model failure.

## Completed - upload from the interface and an in-process worker (2026-08-29)

Two gaps that made the interface unable to reach its own pipeline.

**The UI had no upload control.** "Add a Reel" posted only a URL, and a URL-only Reel has no media,
so it could never be processed - it sat at `ingestion` forever with nothing explaining why. Every
real analysis so far had gone through a script. Collections now has an **Upload a video** form
posting multipart to `/api/reels/upload`, and the URL form says plainly that it records a reference
only.

**Dispatch hung when the broker was down.** `POST /api/jobs/{id}/dispatch` never returned with Redis
stopped: Celery retried the connection with backoff and blocked the request thread past 90 seconds.
Its own retry settings (`broker_connection_max_retries`, `retry=False`) did not bound it reliably, so
the broker is now probed with a plain socket first and the endpoint returns 503 in about two seconds
with the reason and the command to fix it.

**`TASK_RUNNER=local` runs the pipeline on a background thread.** The architecture's rule is that the
HTTP request must not run the pipeline, not that the work must happen on another machine - so for a
single-user install a daemon thread satisfies it with no Redis and no Celery worker. Deliberately one
job at a time: the vision model, Whisper and the embedding model share one 8 GB GPU, and running two
Reels at once is the reliable way to hit out-of-memory.

Stated rather than hidden: in-process work is lost if the process stops. The `ProcessingJob` row
survives, and unfinished jobs are re-queued by a lifespan handler at startup, so nothing is silently
dropped - but a restart mid-analysis means redoing that Reel. `TASK_RUNNER=celery` remains the
durable path.

Also replaced the deprecated `@app.on_event("startup")` with a lifespan handler, which is what
surfaced when the new startup hook doubled the suite's warning count.

**Verified end to end**: the real 33-second Instagram Reel uploaded through the API, dispatch
returned in 0.04 s with a `local-` handle, and the background thread carried it from
`queued/ocr_and_transcription` to `running/multimodal_analysis` to a terminal state in 211 s with no
further prompting. Transcription and OCR were correct; the vision model again returned unparsable
JSON, so the Reel landed in `needs_review` with zero extracted records rather than a guess. That is
the known structured-output limitation, unchanged by this work, and the safety behaviour is correct.

7 new tests cover runner routing, the Celery default still probing the broker, a bad job not killing
the worker, and resume skipping finished jobs and media-less Reels.

## Completed - constrained decoding for the local vision model (2026-08-29)

The vision model could not reliably emit valid JSON. Across runs on one real Reel it produced a
schema-shaped echo, a repetition loop, unescaped control characters, and single-quoted strings with
an unterminated array. Each was patchable individually, but patching them one at a time meant
writing a JSON repairer - and a repairer has to guess at intent, which risks inventing values.

`lm-format-enforcer` now filters the decoder so only tokens that keep the output a valid instance of
the vision schema can be chosen. Malformed JSON becomes unrepresentable rather than detected
afterwards. Enabled with `QWEN_CONSTRAINED_JSON` (default on); if the package is missing the
provider logs and falls back to unconstrained generation rather than failing.

**Two things had to be fixed before it worked, and the second was the real one.**

1. *A compatibility shim.* lm-format-enforcer imports `PreTrainedTokenizerBase` from
   `transformers.tokenization_utils`, where it lived through transformers 4; in 5.x it moved to
   `tokenization_utils_base`. That one failed import makes the integration raise
   `ImportError("transformers is not installed")` - which is actively misleading, since transformers
   *was* installed, and it sent the first diagnosis in the wrong direction. The old name is restored
   before the import and the shim disappears when upstream catches up.

2. *A length bound, which is load-bearing rather than cosmetic.* With the constraint engaged the
   output was **still** unparsable. The cause was not malformation but **truncation**: the model
   narrated the video for 5,881 characters and ran out of token budget mid-string. A prefix
   constraint guarantees every partial output remains *extendable* to something valid; only a length
   bound makes it *finishable*. `maxLength` on `description` (600) and the other free-text fields,
   plus `maxItems` on the arrays, closed it. Descriptions now land at exactly the bound.

**Result on the real Instagram Reel**, uploaded through the API and processed automatically by the
in-process runner - the first `completed` run on genuine content:

| Field | Value | Evidence |
|---|---|---|
| topic | AI Job Search Framework | `inferred` |
| technologies | Claude Code, LaTeX, Python | `ocr` |

Cost: about 58 s cold and 23 s warm for the vision stage, against roughly 54 s unconstrained. The
per-token filter is not free, but it converts an unreliable stage into a reliable one.

**Also fixed:** the demo runner had no `--reload`, so it kept serving stale code after an edit. That
produced two wrong diagnoses in this session - a run "failing" that had actually already been fixed.
It now reloads, with the `__main__` guard Windows requires for uvicorn's reload subprocess.

## Fixed - media binaries not resolvable inside the server process (2026-08-29)

Every upload through `run.bat` failed at the first stage with:

    Required binary was not found: ffprobe

**The cause was an environment mismatch, not a missing install.** `ffprobe` was present, on the
persisted user PATH, and ran fine from a shell - `shutil.which` found it immediately. Inside the
server process it resolved to nothing: that process runs with a PATH that does not include the
FFmpeg directory. A bare binary name is resolved by the OS at `subprocess` time, so the pipeline
inherited whatever PATH the launcher happened to have and failed several layers deep with a
`FileNotFoundError`.

Two wrong theories came first and are worth recording: a stale PATH in a long-running process
(disproved - FFmpeg was installed on 13 August, the server started on 29 August), and a missing
install (disproved - the binary was there and executable).

**Three changes, in order of how much they matter:**

1. `resolve_binary()` turns the configured name into an absolute path via `shutil.which` at
   construction, falling back to a sibling of a located `ffmpeg` since the two ship together. The
   pipeline no longer depends on the caller's environment.
2. `/api/capabilities` reports where the binaries actually resolved, and adds a limitation naming
   the environment variable to set when they do not. Previously the endpoint asserted
   `media_preparation: ["ffmpeg_audio", "frame_sampling"]` unconditionally - it claimed a capability
   the process did not have, which is exactly what that endpoint exists to prevent.
3. `FFMPEG_BINARY` and `FFPROBE_BINARY` are pinned to absolute paths in `.env`, so this machine does
   not depend on PATH at all.

The error message now names the environment variable to set rather than only the missing binary.

**Verified on the real failure**: the user's own uploaded Reel, in their own database, reprocessed
from `failed` to `completed` - transcript, 33 OCR rows, parsed vision output, and an `ai_knowledge`
record (`topic: AI Job Search Framework`, `technologies: Claude Code, LaTeX, Python`).

## Completed - browse and delete collection contents (2026-08-29)

A collection could be created and filled but never inspected or pruned from the interface. Each
collection row now expands in place to list its Reels - status pill, creator, caption, any error -
with per-Reel Evidence, Process, Open and Delete actions, and a Delete control on the collection
itself.

Both confirmations state the cascade rather than asking a bare "are you sure", because the blast
radius is the part the user cannot see:

    Delete the collection "AI"? This also deletes its 3 Reels and everything extracted from
    them, including downloaded media. This cannot be undone.

Verified in a real browser against a throwaway collection rather than the developer's own data:
cancelling preserved all four collections; deleting a Reel left the list open rather than collapsing
it; deleting a collection moved the count 5 -> 4; no horizontal overflow at 375 px.

## Completed - fp16 weights for the retrieval models (2026-08-29)

BGE-M3 and bge-reranker-v2-m3 both ship as fp32 - 2166 MB each on disk, and both were loaded with a
bare `SentenceTransformer(name)` / `CrossEncoder(name)`, so both landed on the GPU at full width.

**The problem is residency, not any single model.** Every model loader in this project is
`lru_cache`d and nothing is ever released, so a process that has run one pipeline and then served one
hybrid search holds all three GPU models at once:

    Qwen 4-bit 3.5 GB  +  BGE-M3 2.2 GB  +  reranker 2.2 GB  =  ~7.9 GB   on an 8.1 GB card

Measured at 7923 / 8188 MiB with the Windows desktop also drawing on it. The peak is the *sum*, not
the maximum, and the first hybrid search after a processing run is the likeliest moment to exhaust
it - not the vision stage, which is where the attention naturally goes.

`RETRIEVAL_FP16` (default on) loads both at half width, 1083 MB each. Passed as `from_pretrained`
kwargs rather than a post-load `.half()`, so the fp32 copy is never materialised; converting
afterwards would still peak at the full size, which is the memory being reclaimed. The flag is part
of the `lru_cache` key, so a changed setting cannot be served a model in the stale dtype. It returns
no-op without CUDA: fp16 on CPU falls back to emulated kernels and would be a slowdown, not a saving.

Note `dtype`, not `torch_dtype` - transformers 5 renamed it and warns on the old spelling.

**Verified on the GPU through the real provider path**, not just the library: both report
`device=cuda:0 dtype=torch.float16` at 1083 MB each, 2166 MB total against 4332 MB before, and both
still work - 1024-dim finite vectors matching the declared contract, reranker ordering the relevant
passage first.

**No reindex needed, and that was checked rather than assumed.** Vectors already stored in fp32 stay
comparable with fp16-computed queries: cosine similarity between the two encodings of the same text
is 0.9998, while the highest similarity between two genuinely different texts is 0.53. The dtype
drift is two orders of magnitude below the smallest real semantic gap.

## Fixed - the vision stage could exhaust the GPU and kill the worker (2026-08-29)

An 84-second screen recording (a Google I/O talk) failed twice at the vision stage with
`CUDA out of memory`, and the worker process died both times. Three separate defects, found by
running the Reel and reading the logs.

**1. The prompt was unbounded.** The failed allocation was 10.32 GiB for a *single* attention
tensor, implying a ~18,600-token prefill. Frames were not the cause - `select_frames` caps them at
`VLM_MAX_FRAMES=4`, so visual tokens were bounded at ~2,048. The text was:

| | measured |
|---|---|
| OCR after `_distinct_lines` | 32,597 chars, 1,144 distinct lines |
| OCR as tokens | 10,533 - **96% of the text prompt** |
| total prefill | >=12,977 tokens -> ~5 GiB attention |

`_distinct_lines()` deduplicates, but deduplication cannot help when every line genuinely differs:
84 frames of a live OS demo produce 1,144 distinct on-screen lines. Attention is quadratic in prompt
length, so this is a cliff rather than a slope - the 32.8s Reel processed the day before, with a
third of the text, was fine.

`VLM_MAX_OCR_CHARS` (6000) and `VLM_MAX_TRANSCRIPT_CHARS` (4000) now bound the evidence, trimmed on
line boundaries in frame order and marked with the count of omitted lines - the model is told the
evidence is partial rather than left to treat a truncated list as complete. Prefill on the same Reel
fell from >=12,977 to **4,517 tokens**, and the attention matrix from 10.32 GiB requested to 0.61 GiB.

**2. A job that died mid-analysis was unrecoverable.** `resume_unfinished()` only re-queued jobs at
`ingestion` or `ocr_and_transcription`, so a worker killed inside `multimodal_analysis` left the job
at `running` with nothing that would ever collect it - the Reel spun in the interface permanently.
Restarting the server did not help, which is how it was found.

Adding the stage to the resume list was **half a fix**: `_process` also matched only the entry
stage, so a resumed job would have been re-queued and then silently ignored, looking handled while
staying just as stuck. Both now accept `multimodal_analysis`. Two tests cover it, one per half.

**3. A caught OOM still killed the process.** Stage isolation worked - the OOM was logged as a
`WARNING` and the stage marked failed. But the device was exhausted, so the *next* model to load,
the embedding model, took the worker down. `release_vision_memory()` now frees the allocator's
cached blocks after every vision stage, and drops the weights entirely when the stage failed on an
allocation.

**Verified by rerunning the same Reel**: `completed` in 341s, peak 6447 MiB, no crash. Every stage
succeeded - transcription 49s, OCR 227s, vision 57s, extraction, plus 6 embeddings.

**Also worth recording, and not fixed:** the run produced a `workout_exercise` record from a
conference talk - `target_muscles: ["speech"]`, `equipment: ["microphone"]` - at confidence 0.95,
routed to `completed` without review. The vision model classified the video correctly
(`content_kind: "ai_technical"`); `structured_extraction.py` never reads that field, and emits a
workout record whenever any workout-shaped field is non-empty. The classification exists and is
ignored.

A second note on the truncation: 885 of 1,144 OCR lines are dropped for this Reel. The model sees
23% of the on-screen text. That is the right trade against not running at all, but it is a real loss
of evidence, and per-frame `OCRResult` rows remain complete - only the prompt is bounded.

## Completed — collection ownership isolation (2026-08-28)

- Added a development identity boundary via `X-User-Id` (defaulting to `local-user`).
- Scoped collection, Reel, job, search, report, and workout endpoints to the owner so another identity receives `404` rather than cross-user data.
- Added an ownership-isolation API test.

This is deliberately a development authorization boundary, not production authentication. A real verified authentication provider remains required before public deployment.

## Completed — user-scoped operational metrics (2026-08-28)

- Added `GET /api/system/metrics`, which returns collection count plus Reel and job status breakdowns for the current development identity.
- Metrics are scoped through the same ownership boundary as the rest of the API.

## Deferred to the next implementation slices

Verified by a scripted audit against the architecture rather than by assertion:
**30/30 steps of the section 70 implementation order, and 29/29 items of the section 71 definition
of done.** The audit script checks live routes and source symbols, not a checklist kept by hand; an
earlier version of it produced a false negative on the frontend (which is deliberately excluded from
the OpenAPI schema), and the check was corrected rather than the result accepted.

Two caveats on what "done" means here. It means every capability the architecture specifies exists,
is wired end to end, and is covered by tests. It does not mean the system has been run against real
Instagram Reels with real model weights - see the first item below.

What remains is genuinely out of scope for this codebase, or needs something it cannot supply:

- **Live prompt and model validation.** Every Claude provider is tested through a fake client, which
  pins request shape and the grounding rules but not the prompts' actual quality. Tuning them needs
  a real API key and real Reels. Nothing here has been measured against live model output.
- **Pose estimation** (section 24), explicitly optional in the architecture.
- **Distributed rate limiting.** The limiter is per-process; a multi-instance deployment needs the
  same counter in Redis.
- **Database migrations.** Schema is created with `create_all`, which is why two additions in this
  project were delivered as new tables rather than new columns. Alembic is the natural next step
  before the first schema change to an existing table.
- **Prometheus/OpenTelemetry export.** Stage metrics are recorded and queryable over the API;
  shipping them to a metrics backend is a wiring exercise, not a design one.

## Design note

The service records ingestion requests without attempting Instagram login automation or scraping. This preserves the architecture's provider-independent, policy-conscious ingestion boundary.

## Completed — phase 1 of the NotebookLM-style rebuild: shared source library (2026-10-25)

The project is being turned from a Reels engine into a general multimodal notebook: a library of
sources (documents, web pages, YouTube, images, audio, video), collections built from that library,
and a chatbot that answers only from a collection's sources. It is now named **Multimodal Knowledge
Engine**. Agreed roadmap: (1) data model, (2) ingestion for every source type, (3) one processor per
kind with citation locators, (4) real chunking and source-scoped retrieval, (5) chat with history,
streaming, and chunk-level citations over switchable Claude and Ollama providers, (6) cleanup of the
Instagram-specific extraction and docs. The frontend is deferred.

Phase 1 delivered:

- `Reel` became `Source`, with `kind`, `title`, `original_filename`, `mime_type`, `author`,
  `description`, and a `details` JSON column. Every dependent table is keyed by `source_id`.
- Sources are owned by a user, not a collection. The new `collection_sources` table links them, so
  one source can sit in many collections and is processed and embedded once. Duplicate links and
  file hashes are rejected across the owner's whole library.
- Embeddings no longer carry `collection_id`; vector search takes the collection's member source ids
  (`MatchAny` in Qdrant).
- New endpoints: `POST /api/sources/url`, `POST /api/sources/upload` (collection optional),
  `GET /api/sources` (library, filterable by kind, status, and collection),
  `POST /api/collections/{id}/sources`, `DELETE /api/collections/{id}/sources/{source_id}`. Deleting
  a collection keeps its sources; deleting a source removes it everywhere; cloning shares sources.
- The search filter `creator` is now `author`; search results and citations carry a `title`.
- Removed the workout planner and the AI-concept reports with their narrator. Workout and AI fields
  inside the extraction prompts remain until phase 6.

Not yet done: only video uploads are accepted and processed; URL sources are recorded but not
fetched. The old single-page client under `app/web/static` still calls the Reel API and is stale.
The default SQLite file moved to `data/multimodal_knowledge.db`, because `create_all` cannot migrate
the old Reel-era tables.

## Completed — phase 2: ingestion for every source type (2026-10-25)

Every source kind can now enter the library and end up with its raw content on disk. Analysing that
content is phase 3.

- **Uploads of every type.** `POST /api/sources/upload` accepts PDF, DOCX, TXT, Markdown, PNG, JPEG,
  WebP, MP3, WAV, M4A, Ogg, FLAC, MP4, MOV, and WebM. The extension decides the kind (the declared
  content type is only a fallback for a file without one), and the first bytes must match it, so a
  renamed file is refused with 415 before the rest is written. Text must be UTF-8 without NUL bytes,
  checked while streaming; a DOCX must contain a document body. The stored MIME type is the verified
  one. Rejected uploads leave no file behind.
- **Pasted text.** `POST /api/sources/text` stores the text as a file, so it is processed like any
  upload. Identical text is a duplicate; `MAX_TEXT_CHARACTERS` bounds a paste.
- **Link normalisation** (`app/services/links.py`). Every YouTube form (`youtu.be`, `/shorts/`,
  `/embed/`, `watch?v=…&t=…`) reduces to one canonical link, so it deduplicates; channel and playlist
  links are refused. Web links lose the fragment and default port. Credentials in a link, non-HTTP
  schemes, and obviously private hosts are refused at ingestion with a 422 that says why.
- **Fetching in the worker** (`app/services/fetching.py`). Web pages are fetched with redirects
  followed one hop at a time; every hop is re-checked, and every address a host resolves to must be
  public unless `FETCH_ALLOW_PRIVATE_HOSTS` is set. Responses are size-capped while streaming and
  limited to HTML, plain text, Markdown, and PDF. The page title becomes the source title unless the
  user typed one. YouTube sources fetch the caption track (written captions in a preferred language
  first, then generated ones, then any language) through the optional `youtube-transcript-api`, plus
  the title and channel from oEmbed.
- **Routing by kind.** A single first stage, `start_source_job` (Celery task `sources.start`), sends
  links to fetching, video to media preparation, and every other kind to `awaiting_processor`. Links
  can now be dispatched without a file. The local runner resumes links that were never fetched.
- **Originals are published** to the object store for non-video sources (`StoredObjectKind.original`).
  A fetched HTML snapshot is served only as a sandboxed download (`Content-Security-Policy: sandbox`,
  `Content-Disposition: attachment`), since it is someone else's markup on this origin.

Limits, stated plainly: the address check and the connection are separate DNS lookups, so a
rebinding DNS server could still steer a fetch inward; pinning the connection to the checked
address would close that. `youtube-transcript-api` is unofficial and can break when YouTube changes,
and YouTube may refuse requests from cloud IP ranges. Neither fetcher has been run against the live
network in this phase; the tests use mock transports and fake transcript APIs.

## Completed — phase 3: a processor for every source kind (2026-10-25)

Every non-video source is now turned into searchable, citable evidence. Processors live in
`app/processors/` and produce `SourceSegment` rows: a passage of text, its modality (`text`,
`transcript`, `ocr`, `visual`), and a locator saying where in the source it came from.

| Kind | Processor | Locator |
|---|---|---|
| PDF | PDFium text per page (`pypdfium2`, chosen over PyMuPDF for its licence); pages with almost no text layer are rendered and sent to the configured OCR provider | `page` |
| DOCX | `python-docx`, body in document order, heading styles kept as Markdown headings, tables as rows | `section`, `paragraph` |
| TXT, Markdown, pasted text | Heading-aware splitter; code fences are not mistaken for headings | `section`, `paragraph` |
| Web page | `trafilatura` when installed, otherwise a built-in extractor that drops scripts, navigation, headers, footers, asides, and forms; fetched PDFs and text reuse the document processors | `section`, `paragraph` |
| YouTube | Stored captions grouped into windows of `TRANSCRIPT_WINDOW_SECONDS` | `start`, `end` |
| Audio | FFmpeg to 16 kHz mono, then the configured speech provider, windowed the same way | `start`, `end` |
| Image | OCR text, plus a prose description from Claude when `VISION_PROVIDER=claude` (new `ImageDescriber` contract; large images are downscaled first) | `region` |

Segments never cross a heading and stay under `SEGMENT_MAX_CHARACTERS`; an overlong section is
split between paragraphs, then sentences. Problems that do not stop processing (one unreadable page,
OCR failing on an image that still has a description) are recorded as warnings: the stage metric is
`degraded` and each warning is a job event. A source with nothing extractable fails with the reason.

Pipeline: after fetching or upload, a source waits at `awaiting_processor`, then
`process_source_content` (Celery task `sources.extract_content`) runs the processor, replaces the
segments, records a stage metric and provenance (`processor_models`), indexes, and completes the
source. Providers are built lazily, so a Markdown file never loads an OCR or speech model. Until
phase 4's chunker, each segment is embedded as one chunk and lexical search reads segments directly.

Verified: 252 tests, including real PDFs (generated in the test), real DOCX files, and an end-to-end
upload that is segmented, indexed, found by hybrid search, and cited by `/ask`. A live fetch of
example.com through the web processor produced clean body text. Not verified against live models:
OCR of scanned pages (tests use a fake OCR provider), Whisper on real audio, and Claude image
descriptions.

## Completed — phase 4: located chunks and chunk-level retrieval (2026-10-25)

- **Chunking** (`app/services/chunking.py`). Every source, video included, is reduced to ordered,
  located passages: processor segments, or a video's timed transcript lines, on-screen text (a
  caption visible across frames collapses into one timed span), and visual description. Passages
  are packed into chunks of at most `CHUNK_MAX_CHARACTERS`, each starting with the last
  `CHUNK_OVERLAP_CHARACTERS` of the previous one; an overlong passage becomes overlapping windows
  that end at word boundaries. Packing never mixes streams (transcript and OCR stay apart) or
  headings, but does span pages and time windows, and the merged locator records the span.
  Extracted records stay one chunk each. A summary chunk is still stored for near-duplicate
  detection but is excluded from retrieval, since it only restates the other chunks.
- **Stored locators.** `content_embeddings` gains `locator` and `position`; the Qdrant payload
  carries the locator too. `describe_locator` turns one into a label: "p. 4", "pp. 3–4",
  "12:30–13:45", "§ Guide > Install".
- **Retrieval** (`app/services/retrieval.py`) ranks stored chunks in every mode. Lexical search is
  BM25 over the chunks of the sources in scope (`minimum_score` remains a count of query-term
  occurrences per source); dense search is the vector store; hybrid fuses the two per chunk with
  reciprocal rank fusion. Search results group chunks into sources and carry up to three
  `passages`, each with `locator` and `citation`. Evidence for `/ask` is now hybrid rather than
  dense-only, reranked at chunk level, and each answer source carries `chunk_key`, `locator`, and
  `citation`; both answer providers put the citation next to the source in their output.
- **Source subsets.** `/api/search` and `/ask` take `source_ids`; ids outside the collection are a
  404 that lists them.

Behaviour change: retrieval reads only indexed chunks, so the lexical fallback over raw evidence for
unindexed sources is gone. Every pipeline indexes on completion, and `POST /api/collections/{id}/index`
backfills. **Existing databases need the two new `content_embeddings` columns**: `create_all` does
not add columns, so a database created before this phase must be recreated (or the columns added by
hand) and reindexed. This is the point where migrations (Alembic) stop being optional.

Verified: 272 tests, including chunk packing, overlap, window splitting, video timestamps, locator
labels, BM25 ordering, page citations in search and answers, and scoped questions.
