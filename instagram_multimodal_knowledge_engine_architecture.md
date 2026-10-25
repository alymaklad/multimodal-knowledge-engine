# Multimodal Personal Knowledge Engine for Instagram Reels

## 1. Project Overview

### Working title

**Multimodal Personal Knowledge Engine for Short-Form Video**

### Core idea

Build a personal AI system that turns Instagram Reels supplied by the user into a searchable, structured, multimodal knowledge base.

The system has two primary use cases:

1. **Workout Collection**
   - The user has a collection such as `Workout`.
   - The system analyzes every supplied Reel.
   - It identifies exercises, equipment, target muscles, sets, reps, technique instructions, and other available information.
   - It can generate a workout program using exercises and information extracted from the user's saved Reels.
   - It should cite the source Reel for generated exercises.

2. **AI Collection**
   - The user has a collection such as `AI`.
   - The system analyzes every supplied Reel.
   - It extracts creator, topic, transcript, visual information, OCR text, technologies, concepts, and summary.
   - The user can semantically search the collection based on the actual content of the videos rather than only captions or filenames.
   - The system can generate reports covering all videos or subsets of videos.

### Core architectural principle

Do not make Instagram the application's database.

Instead:

> **Instagram Reel -> multimodal ingestion -> structured knowledge -> vector index -> retrieval -> AI reasoning**

Instagram is a content source. The application maintains its own knowledge representation after ingestion.

---

# 2. Important Instagram / Meta Constraint

Meta's official Instagram APIs are not designed to expose a user's private **Saved Collections** as a general-purpose API resource.

Do not design the system around assumed endpoints such as:

```text
GET /me/saved
GET /me/saved_collections
GET /me/saved_collections/{collection_id}/media
```

The project should therefore separate:

### Content ingestion

How a Reel enters the application.

from:

### AI processing

What the application does after it has the Reel.

The processing architecture should remain independent of the ingestion mechanism.

Possible ingestion approaches:

- User submits a Reel URL.
- User shares a Reel into the application.
- User uploads/provides the media.
- A future officially supported Meta integration, if Meta exposes the required capability.

Avoid making unauthorized Instagram login automation or scraping the foundation of the product. Browser automation can be brittle, trigger authentication challenges/rate limits, and may conflict with platform policies depending on implementation.

---

# 3. High-Level Architecture

```text
                         USER
                           |
                           v
                    +--------------+
                    |   FastAPI    |
                    |  REST API    |
                    +------+-------+
                           |
                     Create Job
                           |
                           v
                    +--------------+
                    | Redis / Queue |
                    +------+-------+
                           |
                           v
                    +--------------+
                    | Celery Worker |
                    +------+-------+
                           |
              +------------+-------------+
              |            |             |
              v            v             v
          Metadata       Video         Audio
                            |             |
                            v             v
                         FFmpeg        Whisper
                            |
                            v
                         Frames
                            |
                 +----------+----------+
                 |                     |
                 v                     v
                OCR                   VLM
                 |                     |
                 +----------+----------+
                            |
                            v
                    LLM Extraction
                            |
                            v
                    Structured JSON
                            |
                +-----------+-----------+
                |           |           |
                v           v           v
           PostgreSQL     Qdrant      MinIO/S3
                |           |
                +-----+-----+
                      |
                      v
                AI Application
                      |
          +-----------+-----------+
          |                       |
          v                       v
   Semantic Search          Workout Planner
          |                       |
          v                       v
     AI Collection          Workout Collection
```

---

# 4. Technology Stack

## Backend

- Python
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic

## Asynchronous processing

- Celery
- Redis

## Relational database

- PostgreSQL

## Vector database

- Qdrant

## Object storage

- MinIO locally
- S3-compatible storage in production

## Video processing

- FFmpeg
- OpenCV where useful

## Speech

- Whisper
- Possible implementations:
  - local Whisper
  - faster-whisper
  - hosted speech-to-text API

## OCR

- PaddleOCR

## Vision-Language Model

Possible providers/models:

- Qwen-VL / Qwen video-capable VLM variants
- Gemini video-capable multimodal models
- OpenAI multimodal models where the selected API/model supports the required input
- Other hosted VLM providers
- Hugging Face-hosted models

Use a provider abstraction so the system is not locked to one VLM.

## Embeddings

- BGE-M3

## LLM

Use an LLM capable of:

- structured JSON output
- reasoning over retrieved documents
- source-aware answers
- function/tool calling if the agent layer is added

---

# 5. Hardware Strategy

The development machine has an NVIDIA RTX 4070 Laptop GPU with 8 GB VRAM.

This is sufficient for experimentation with smaller/quantized local models, but not ideal for large VLMs.

Recommended strategy:

### MVP

Use APIs for the most demanding multimodal model calls.

### Local experimentation

Benchmark a smaller/quantized Qwen-VL model locally.

### Production abstraction

Implement:

```python
class VisionProvider:
    async def analyze_video(...):
        ...
```

Then provide implementations such as:

```text
GeminiVisionProvider
QwenVLProvider
OpenAIVisionProvider
```

Similarly:

```text
WhisperLocalProvider
WhisperAPIProvider
```

and:

```text
OpenAIProvider
GeminiProvider
QwenProvider
```

This allows models to be changed without rewriting the application.

---

# 6. Project Directory Structure

```text
instagram-ai/
|
+-- app/
|   |
|   +-- main.py
|   |
|   +-- api/
|   |   +-- reels.py
|   |   +-- collections.py
|   |   +-- search.py
|   |   +-- workouts.py
|   |   +-- jobs.py
|   |
|   +-- models/
|   |   +-- reel.py
|   |   +-- collection.py
|   |   +-- transcript.py
|   |   +-- visual_analysis.py
|   |   +-- extracted_content.py
|   |   +-- job.py
|   |
|   +-- schemas/
|   |   +-- reel.py
|   |   +-- collection.py
|   |   +-- workout.py
|   |   +-- search.py
|   |   +-- analysis.py
|   |
|   +-- services/
|   |   +-- ingestion.py
|   |   +-- video.py
|   |   +-- audio.py
|   |   +-- transcription.py
|   |   +-- ocr.py
|   |   +-- vision.py
|   |   +-- extraction.py
|   |   +-- embeddings.py
|   |   +-- storage.py
|   |   +-- retrieval.py
|   |   +-- workout.py
|   |   +-- reporting.py
|   |
|   +-- workers/
|   |   +-- celery_app.py
|   |   +-- tasks.py
|   |
|   +-- db/
|   |   +-- database.py
|   |   +-- repositories/
|   |
|   +-- providers/
|   |   +-- vision/
|   |   |   +-- base.py
|   |   |   +-- qwen.py
|   |   |   +-- gemini.py
|   |   |
|   |   +-- llm/
|   |   |   +-- base.py
|   |   |   +-- openai.py
|   |   |   +-- gemini.py
|   |   |
|   |   +-- speech/
|   |       +-- base.py
|   |       +-- whisper.py
|   |
|   +-- core/
|       +-- config.py
|       +-- security.py
|
+-- tests/
|   +-- unit/
|   +-- integration/
|   +-- evaluation/
|
+-- docker-compose.yml
+-- Dockerfile
+-- requirements.txt
+-- .env
+-- README.md
```

---

# 7. Data Model

## 7.1 Users

```text
users
-----
id
email
created_at
```

If authentication is added later, this becomes the owner of collections and media.

---

## 7.2 Collections

```text
collections
-----------
id
user_id
name
description
created_at
updated_at
```

Examples:

```text
1 | user_123 | Workout
2 | user_123 | AI
```

---

## 7.3 Reels

```text
reels
-----
id
collection_id
instagram_url
external_id
creator_username
creator_name
caption
duration
thumbnail_path
media_path
status
processing_version
created_at
updated_at
```

Possible statuses:

```text
pending
downloading
processing
completed
failed
needs_review
```

---

## 7.4 Transcripts

```text
transcripts
-----------
id
reel_id
text
language
segments
model
created_at
```

Store timestamps.

Example:

```json
{
  "segments": [
    {
      "start": 0.0,
      "end": 4.2,
      "text": "Today we're training chest."
    },
    {
      "start": 4.2,
      "end": 8.7,
      "text": "Start with incline dumbbell press."
    }
  ]
}
```

---

## 7.5 OCR Results

```text
ocr_results
-----------
id
reel_id
frame_id
timestamp
text
bounding_boxes
confidence
created_at
```

Example:

```json
{
  "text": "BENCH PRESS 4 X 8-10",
  "timestamp": 5.6,
  "confidence": 0.97
}
```

---

## 7.6 Video Frames

```text
frames
------
id
reel_id
frame_number
timestamp
path
scene_id
created_at
```

Frames may be stored temporarily rather than permanently.

---

## 7.7 Visual Analysis

```text
visual_analysis
---------------
id
reel_id
description
analysis_json
model
confidence
created_at
```

Example:

```json
{
  "description": "Person performing a barbell back squat.",
  "model": "qwen-vl",
  "confidence": 0.96
}
```

---

## 7.8 Extracted Content

```text
extracted_content
-----------------
id
reel_id
content_type
data
confidence
created_at
updated_at
```

This should contain normalized machine-readable information.

Example workout data:

```json
{
  "exercise": "Barbell Back Squat",
  "sets": 4,
  "reps": "8-10",
  "equipment": [
    "barbell",
    "squat rack"
  ],
  "muscles": [
    "quadriceps",
    "glutes"
  ],
  "evidence": {
    "exercise": "visual",
    "sets": "ocr",
    "reps": "ocr"
  }
}
```

---

# 8. Object Storage

Store large media outside PostgreSQL.

Use:

```text
MinIO
```

during local development and:

```text
S3
```

or another S3-compatible service in production.

Suggested structure:

```text
bucket/
|
+-- users/{user_id}/
    |
    +-- reels/{reel_id}/
        |
        +-- video.mp4
        +-- audio.wav
        +-- thumbnail.jpg
        +-- frames/
            +-- frame_001.jpg
            +-- frame_002.jpg
```

Do not store video blobs directly in PostgreSQL.

---

# 9. Reel Ingestion API

Endpoint:

```http
POST /api/reels
```

Request:

```json
{
  "collection_id": 1,
  "instagram_url": "https://www.instagram.com/reel/..."
}
```

FastAPI should immediately create a database record and enqueue processing.

Example:

```python
@router.post("/reels")
async def create_reel(
    request: ReelCreate,
    db: AsyncSession = Depends(get_db)
):
    reel = await create_reel_record(db, request)

    process_reel.delay(reel.id)

    return {
        "reel_id": reel.id,
        "status": "processing"
    }
```

Do not run the complete video pipeline inside the HTTP request.

---

# 10. Background Processing

Use Celery + Redis.

Basic architecture:

```text
FastAPI
   |
   v
Redis
   |
   v
Celery Worker
```

For production, split the pipeline into separate tasks.

Example:

```text
ingest_reel
     |
     v
prepare_media
     |
     v
extract_audio
     |
     v
transcribe
     |
     +----> sample_frames
                  |
                  +----> OCR
                  |
                  +----> VLM
     |
     v
structured_extraction
     |
     v
embedding
     |
     v
persist_results
```

This allows individual steps to be retried.

---

# 11. End-to-End Processing Pipeline

## Step 1 — Validate Reel

Validate:

- URL
- collection ownership
- duplicate Reel
- supported content type

If the same Reel has already been processed, do not process it again unnecessarily.

---

# 12. Step 2 — Acquire Media

The exact acquisition mechanism depends on the supported ingestion method.

The processing layer should receive a local media path such as:

```text
/media/reels/123/video.mp4
```

Then the rest of the pipeline does not need to care where the Reel came from.

---

# 13. Step 3 — Extract Metadata

Extract:

- creator username
- creator name where available
- caption
- URL
- duration
- media dimensions
- timestamps where available

Store metadata in PostgreSQL.

---

# 14. Step 4 — FFmpeg Processing

Use FFmpeg to extract:

```text
video.mp4
   |
   +-- audio.wav
   |
   +-- frames
```

Example:

```text
video
 |
 +-- audio
 |
 +-- frame_001.jpg
 +-- frame_002.jpg
 +-- frame_003.jpg
 ...
```

---

# 15. Step 5 — Determine Whether Audio Exists

If the Reel contains no audio:

```text
Audio absent
    |
    v
Skip Whisper
```

If audio exists:

```text
Audio
  |
  v
Speech detection
  |
  +-- Speech exists -> Whisper
  |
  +-- No speech -> skip or retain audio metadata
```

This avoids unnecessary transcription.

---

# 16. Step 6 — Whisper Transcription

For speech:

```python
result = whisper_model.transcribe(
    audio_path,
    language="en"
)

transcript = result["text"]
```

Prefer timestamped segments.

Example:

```json
{
  "segments": [
    {
      "start": 0,
      "end": 4.2,
      "text": "Today we're training chest."
    }
  ]
}
```

The system should support multilingual content if needed.

---

# 17. Step 7 — Video Frame Sampling

Do not send every frame to the VLM.

For an initial implementation:

```text
2-4 frames/second
```

For a 30-second Reel:

```text
30 × 3 = approximately 90 frames
```

This can later be optimized.

Better strategies:

- fixed-rate sampling
- scene-change detection
- keyframe extraction
- adaptive sampling around movement
- lower sampling for static videos
- higher sampling for exercise movement

---

# 18. Step 8 — OCR

Use PaddleOCR on selected frames.

Example text:

```text
BENCH PRESS
4 SETS
8-10 REPS
REST 90 SEC
```

Store OCR results with:

- text
- frame
- timestamp
- bounding box
- confidence

Example:

```json
{
  "text": "BENCH PRESS 4 X 8-10",
  "frame": 17,
  "timestamp": 5.6,
  "confidence": 0.97
}
```

OCR is particularly important for workout videos because creators frequently put sets/reps directly on screen.

---

# 19. Step 9 — Vision-Language Model

Use the VLM to understand the visual content.

Interface:

```python
class VisionProvider:
    async def analyze_video(
        self,
        frames,
        transcript,
        ocr_text
    ) -> dict:
        ...
```

Possible implementations:

```text
QwenVLProvider
GeminiVisionProvider
OpenAIVisionProvider
```

The model should receive multiple frames rather than a single image when temporal understanding is required.

---

# 20. VLM Prompt Strategy

Do not ask:

```text
"What is in this video?"
```

Use a structured extraction prompt.

Example:

```text
Analyze this workout video.

Determine:

1. Exercise being performed
2. Equipment
3. Target muscle groups
4. Sets
5. Repetitions
6. Duration
7. Rest period
8. Tempo, if explicitly stated
9. Technique instructions
10. Warnings or contraindications

Use the following evidence:

VIDEO FRAMES:
...

OCR:
...

TRANSCRIPT:
...

Do not invent information.

If sets/reps are not explicitly visible or spoken,
return null.

Return structured JSON.
```

The instruction:

> Do not invent information.

is critical.

---

# 21. Multi-Frame Reasoning

A workout exercise often cannot be identified reliably from one frame.

Example:

```text
Frame 1 -> person standing
Frame 2 -> descending
Frame 3 -> bottom of squat
Frame 4 -> standing
Frame 5 -> next repetition
```

The VLM can infer the movement sequence.

This is why a video-capable VLM is preferred over a basic image classifier.

---

# 22. Evidence and Provenance

Every extracted field should ideally include its source.

Example:

```json
{
  "exercise": {
    "name": "Barbell Back Squat",
    "confidence": 0.96,
    "evidence": "visual"
  },
  "sets": {
    "value": 4,
    "confidence": 0.99,
    "evidence": "ocr"
  },
  "reps": {
    "value": "8-10",
    "confidence": 0.99,
    "evidence": "ocr"
  }
}
```

Possible evidence types:

```text
visual
ocr
transcript
metadata
inferred
```

Prefer explicit evidence over inference.

---

# 23. Silent Video Handling

A Reel can contain no speech.

The pipeline should still work:

```text
Silent Reel
     |
     +-- No Whisper
     |
     v
Frame Sampling
     |
     +-- OCR
     |
     +-- VLM
     |
     v
Structured Extraction
```

Example:

Video shows someone performing a Bulgarian split squat.

On-screen text says:

```text
Bulgarian Split Squat
3 × 10 each leg
```

OCR extracts the sets/reps.

VLM identifies the movement and equipment.

Final result:

```json
{
  "exercise": "Bulgarian Split Squat",
  "sets": 3,
  "reps": "10 each leg",
  "equipment": [
    "dumbbells",
    "bench"
  ],
  "evidence": {
    "exercise": "VLM",
    "sets": "OCR",
    "reps": "OCR"
  }
}
```

---

# 24. Optional Pose Estimation

Pose estimation should not be required for the MVP.

Potential future pipeline:

```text
Video
 |
 v
Pose Estimation
 |
 v
Joint positions over time
 |
 v
Movement analysis
 |
 v
Rep counting
```

This can help with:

- squats
- lunges
- curls
- lateral raises
- shoulder presses
- bench press
- other repetitive movements

Use this when accurate rep counting or movement analysis is required.

Do not add it to every Reel initially because it increases complexity.

---

# 25. LLM Structured Extraction

The VLM provides perception.

The LLM normalizes and structures the information.

Conceptually:

```text
VLM + OCR + Whisper
        |
        v
Evidence
        |
        v
LLM
        |
        v
Normalized JSON
```

For example:

```json
{
  "exercise": "Barbell Back Squat",
  "sets": 4,
  "reps": "8-10",
  "equipment": ["barbell"],
  "primary_muscles": [
    "quadriceps",
    "glutes"
  ],
  "secondary_muscles": [
    "hamstrings"
  ],
  "technique": [
    "Maintain a neutral spine",
    "Control the descent"
  ]
}
```

The LLM should not invent missing values.

---

# 26. Confidence-Aware Processing

Use confidence thresholds.

Example:

```text
confidence > 0.85
    |
    v
automatic acceptance

0.60 - 0.85
    |
    v
secondary verification

< 0.60
    |
    v
needs review
```

For uncertain cases:

- run a second VLM
- ask a stronger model
- use OCR/transcript evidence
- ask the user to verify

Store confidence in the database.

---

# 27. Embedding Pipeline

After extracting content, create a textual representation.

Example:

```text
Barbell back squat.
4 sets.
8-10 repetitions.
Targets quadriceps and glutes.
Uses a barbell.
Technique: controlled descent and neutral spine.
```

Generate BGE-M3 embedding:

```python
embedding = bge_model.encode(extracted_text)
```

Store the vector in Qdrant.

---

# 28. Qdrant Payload

Example:

```json
{
  "reel_id": 123,
  "collection_id": 1,
  "creator": "@example",
  "topic": "legs",
  "exercise": "barbell back squat"
}
```

Vector:

```text
[0.013, -0.081, 0.442, ...]
```

Use metadata filters to restrict searches to a specific collection.

---

# 29. AI Collection

Example:

```text
AI Collection

Reel 1 -> RAG
Reel 2 -> Fine-tuning
Reel 3 -> RAG evaluation
Reel 4 -> Computer Vision
Reel 5 -> LoRA
...
```

Each Reel can contain:

- creator
- URL
- caption
- transcript
- OCR
- visual description
- topic
- technologies
- concepts
- summary
- embedding

---

# 30. Semantic Search

User asks:

> Find videos where someone explains hybrid search.

API:

```http
POST /api/search
```

Request:

```json
{
  "collection": "AI",
  "query": "videos explaining hybrid search"
}
```

Pipeline:

```text
User query
    |
    v
BGE-M3
    |
    v
Query vector
    |
    v
Qdrant
    |
    v
Top-K Reels
    |
    v
LLM
    |
    v
Answer + sources
```

The system should return source Reel URLs.

---

# 31. AI Collection Report

The system should support a report containing:

| Creator | Topic | Summary |
|---|---|---|
| @creator1 | RAG | Explains hybrid retrieval |
| @creator2 | Agents | Discusses ReAct |
| @creator3 | Computer Vision | Explains ViT |
| @creator4 | LLMs | Explains KV cache |

The report can also contain:

- total videos
- topic distribution
- most frequent technologies
- creators
- summaries
- key concepts
- duplicate/near-duplicate videos
- learning themes
- source links

---

# 32. AI Collection Questions

Potential queries:

```text
"What did I save about RAG?"

"Show me every Reel about QLoRA."

"Which videos explain hybrid search?"

"What are the most common AI topics in my collection?"

"Compare the videos I saved about RAG and fine-tuning."

"Create a learning roadmap from everything I've saved about AI agents."

"Which creators do I save most frequently?"
```

---

# 33. Workout Collection

The Workout collection should create a structured exercise knowledge base.

Example:

```text
Reel #1
  Bench Press
  4 × 8-10
  Chest

Reel #2
  Lat Pulldown
  3 × 10-12
  Back

Reel #3
  Bulgarian Split Squat
  3 × 10
  Legs
```

The AI should use this structured information rather than simply giving raw videos to an LLM.

---

# 34. Workout Generation

Request:

```http
POST /api/workouts/generate
```

Example:

```json
{
  "collection_id": 1,
  "days_per_week": 4,
  "goal": "hypertrophy",
  "duration_minutes": 60,
  "equipment": [
    "barbell",
    "dumbbells"
  ]
}
```

Pipeline:

```text
Workout Collection
        |
        v
Exercise Database
        |
        v
Filter by constraints
        |
        v
Retrieve relevant exercises
        |
        v
LLM Workout Planner
        |
        v
Workout Program
```

---

# 35. Source Attribution for Workouts

Every generated exercise should be traceable.

Example:

```text
Incline Dumbbell Press
4 × 8-12

Source:
Reel #17 — @creator
Instagram URL
```

This allows the user to verify where the exercise came from.

---

# 36. Preventing Hallucinated Exercises

Avoid:

```text
Reels
  |
  v
LLM
  |
  v
Workout
```

Prefer:

```text
Reels
  |
  v
Exercise Extraction
  |
  v
Exercise Database
  |
  v
Retriever
  |
  v
LLM
  |
  v
Workout
```

The LLM should primarily compose the workout from verified extracted information.

If the user explicitly allows external exercises, those should be clearly marked as external recommendations.

---

# 37. Workout Constraints

The workout generator can eventually consider:

- days per week
- training goal
- available equipment
- session duration
- experience
- muscle groups
- preferred exercises
- disliked exercises
- injury limitations
- volume
- frequency
- recovery
- exercise overlap

The system should not make medical claims or treat its output as medical advice.

---

# 38. API Endpoints

Suggested endpoints:

```text
POST   /api/collections
GET    /api/collections
GET    /api/collections/{id}

POST   /api/reels
GET    /api/reels/{id}
GET    /api/reels/{id}/analysis

POST   /api/search

POST   /api/workouts/generate

GET    /api/jobs/{id}
```

Possible later endpoints:

```text
POST   /api/reels/{id}/reanalyze
POST   /api/reels/{id}/verify
DELETE /api/reels/{id}

GET    /api/collections/{id}/report
GET    /api/workouts/{id}
```

---

# 39. Job Status API

Long-running operations should expose status.

```http
GET /api/jobs/{id}
```

Response:

```json
{
  "job_id": 55,
  "status": "processing",
  "stage": "vision_analysis",
  "progress": 65
}
```

Possible stages:

```text
queued
downloading
metadata
audio
transcription
frame_sampling
ocr
vision
extraction
embedding
storage
completed
failed
```

---

# 40. Idempotency and Caching

Do not process the same Reel repeatedly.

Before processing:

```text
Does this Reel already exist?
        |
        +-- YES -> return existing analysis
        |
        +-- NO -> process
```

Store:

- Reel URL
- external ID where available
- processing version
- model versions

If the processing code changes, a new version can trigger reprocessing.

---

# 41. Versioning

Store model/version information.

Example:

```json
{
  "vision_model": "qwen-vl-version",
  "embedding_model": "BAAI/bge-m3",
  "transcription_model": "whisper-large",
  "extraction_prompt_version": "v2"
}
```

This is important for reproducibility and evaluation.

---

# 42. AI Provider Abstraction

Example:

```python
class VisionProvider(ABC):

    async def analyze_video(
        self,
        frames,
        transcript,
        ocr
    ):
        raise NotImplementedError
```

Implement:

```text
QwenVLProvider
GeminiVisionProvider
OpenAIVisionProvider
```

Likewise:

```python
class EmbeddingProvider(ABC):
    async def embed(self, text):
        ...
```

and:

```python
class SpeechProvider(ABC):
    async def transcribe(self, audio):
        ...
```

This makes model replacement straightforward.

---

# 43. Retrieval Architecture

Use hybrid retrieval for technical content.

The initial architecture can be:

```text
Dense retrieval
    +
Optional keyword/BM25 retrieval
    |
    v
Fusion
    |
    v
Reranking
    |
    v
LLM
```

Dense retrieval is good for semantic meaning.

Sparse retrieval is useful for exact technical terms such as:

```text
QLoRA
RAGAS
BM25
LoRA
PyTorch
Qdrant
Swin Transformer
```

A later version can use Qdrant's hybrid capabilities.

---

# 44. RAG Pipeline

```text
Question
   |
   +--> Dense embedding
   |
   +--> Sparse representation
   |
   v
Qdrant
   |
   v
Candidate Reels
   |
   v
Optional reranker
   |
   v
Top-K evidence
   |
   v
LLM
   |
   v
Grounded response
```

The final answer should include source Reel references.

---

# 45. Report Generation

Reports should not require sending every full video to the LLM every time.

Instead:

```text
PostgreSQL
    |
    v
Structured summaries
    |
    v
Aggregations
    |
    v
LLM
    |
    v
Report
```

This keeps token usage manageable.

---

# 46. Duplicate Detection

Useful future feature.

Two Reels may contain the same or nearly identical content.

Possible signals:

- same URL
- perceptual hash
- transcript similarity
- embedding similarity
- creator + caption similarity

Workflow:

```text
New Reel
   |
   v
Embedding
   |
   v
Qdrant similarity
   |
   v
Potential duplicate
   |
   v
Verify
```

---

# 47. Security

Never store Instagram passwords.

Use:

- authentication
- encrypted credentials where applicable
- signed object-storage URLs
- authorization checks
- per-user collection isolation
- rate limiting
- input validation
- secret management

Every database query should ensure that the current user owns the collection/resource.

---

# 48. Privacy

The system will potentially store:

- private user collections
- videos
- transcripts
- personal workout information
- saved knowledge

Therefore:

- encrypt sensitive data where appropriate
- use HTTPS
- restrict storage access
- provide deletion
- avoid sending unnecessary data to third-party AI APIs
- clearly disclose which providers receive media
- support local inference where privacy is important

---

# 49. Observability

Track:

- processing time
- VLM latency
- transcription latency
- OCR latency
- embedding latency
- API cost
- failure rates
- retry counts
- token usage
- number of processed frames
- model versions

Useful tools later:

- Prometheus
- Grafana
- structured logging
- OpenTelemetry

---

# 50. Error Handling

Each stage should be independently retryable.

Example:

```text
VLM fails
   |
   v
retry
   |
   +-- success -> continue
   |
   +-- failure -> fallback provider
   |
   +-- failure -> mark needs_review
```

Do not discard the entire Reel because OCR failed.

For example:

```text
Whisper failed
   |
   v
Continue with VLM + OCR
```

Likewise:

```text
OCR failed
   |
   v
Continue with VLM + transcript
```

The pipeline should degrade gracefully.

---

# 51. Cost Optimization

Avoid expensive model calls when unnecessary.

Strategies:

1. Skip Whisper if there is no speech.
2. Sample frames instead of sending the full video.
3. Use OCR before asking the VLM to infer text.
4. Cache analyses.
5. Store structured summaries.
6. Use smaller models for easy cases.
7. Use stronger models only for uncertain cases.
8. Batch embedding operations.
9. Use local models for high-volume workloads.
10. Do not reprocess unchanged Reels.

---

# 52. Model Routing

A later optimization:

```text
Incoming Reel
      |
      v
Cheap classifier
      |
      +-- Simple/static -> smaller VLM
      |
      +-- Complex/movement -> stronger VLM
      |
      +-- Low confidence -> verification model
```

This reduces cost while preserving quality.

---

# 53. Evaluation Strategy

A serious CV project should include an evaluation dataset.

Create a manually annotated set of perhaps:

```text
100-300 Reels
```

with ground truth for relevant fields.

## Workout evaluation

Measure:

- exercise identification accuracy
- exercise classification F1
- equipment accuracy
- muscle-group accuracy
- set extraction accuracy
- rep extraction accuracy
- OCR accuracy
- confidence calibration

## AI collection evaluation

Measure:

- topic classification F1
- entity extraction accuracy
- summary quality
- retrieval Recall@K
- Precision@K
- MRR
- answer faithfulness
- source attribution accuracy

---

# 54. Model Benchmarking

Benchmark multiple VLMs.

Example:

```text
                 Exercise Accuracy
Qwen-VL             92%
Gemini              95%
Model C             88%
```

Also benchmark:

- latency
- cost
- VRAM
- OCR performance
- silent-video performance
- temporal understanding

This turns the project into a stronger engineering/research portfolio piece.

---

# 55. Evaluation for Silent Videos

Create a specific subset:

```text
Silent workout videos
```

Measure:

```text
VLM only
VLM + OCR
VLM + OCR + pose estimation
```

This can answer:

> How much does OCR improve structured workout extraction?

This is a useful technical experiment.

---

# 56. Suggested Development Phases

## Phase 1 — Basic ingestion

Goal:

Process one Reel.

Implement:

- FastAPI
- PostgreSQL
- media input
- object storage
- basic Reel metadata

Output:

```text
Reel -> stored media + metadata
```

---

## Phase 2 — Video understanding

Add:

- FFmpeg
- frame sampling
- VLM

Output:

```text
Reel -> visual description
```

---

## Phase 3 — Multimodal ingestion

Add:

- Whisper
- OCR
- timestamped evidence
- multimodal VLM prompt

Output:

```text
Reel -> video + transcript + OCR + visual understanding
```

---

## Phase 4 — Structured extraction

Add:

- Pydantic schemas
- LLM structured output
- confidence
- evidence provenance

Output:

```text
Reel -> structured knowledge
```

---

## Phase 5 — Vector search

Add:

- BGE-M3
- Qdrant
- semantic search

Output:

```text
Question -> relevant Reels
```

---

## Phase 6 — AI Collection

Implement:

- topic extraction
- creator metadata
- summaries
- semantic search
- reports
- source citations

---

## Phase 7 — Workout Collection

Implement:

- exercise extraction
- equipment extraction
- sets/reps
- muscle groups
- exercise database
- workout planner
- source attribution

---

## Phase 8 — Async architecture

Add:

- Redis
- Celery
- job status
- retries
- task-level failures

This should be added before processing large collections.

---

## Phase 9 — Production engineering

Add:

- Docker
- authentication
- authorization
- monitoring
- rate limiting
- caching
- object storage
- provider abstraction
- evaluation

---

## Phase 10 — Advanced features

Potential additions:

- hybrid retrieval
- reranking
- duplicate detection
- pose estimation
- rep counting
- model routing
- multimodal agent
- learning roadmap generation
- workout progression tracking

---

# 57. MVP Definition

Do not attempt the entire architecture immediately.

The first meaningful MVP should be:

```text
User
  |
  v
Submit Reel
  |
  v
FastAPI
  |
  v
Video
  |
  +-- Frames
  +-- OCR
  +-- Whisper
  |
  v
VLM
  |
  v
Structured JSON
  |
  v
PostgreSQL
```

Then test it on approximately:

```text
20-50 Reels
```

Focus on extraction quality before building the agent.

---

# 58. Recommended MVP for Workout

Input:

```text
Reel
```

Output:

```json
{
  "exercise": "Incline Dumbbell Press",
  "sets": 4,
  "reps": "8-12",
  "equipment": ["dumbbells", "bench"],
  "target_muscles": ["chest"],
  "technique": [],
  "confidence": 0.94,
  "evidence": {
    "exercise": "visual",
    "sets": "ocr",
    "reps": "ocr"
  }
}
```

After reliable extraction works, add workout generation.

---

# 59. Recommended MVP for AI

Input:

```text
Reel
```

Output:

```json
{
  "creator": "@creator",
  "topic": "RAG",
  "summary": "Explains hybrid retrieval...",
  "technologies": [
    "BM25",
    "Qdrant",
    "embeddings"
  ],
  "concepts": [
    "dense retrieval",
    "sparse retrieval",
    "hybrid search"
  ],
  "transcript": "...",
  "ocr": ["HYBRID SEARCH"]
}
```

Then index it with BGE-M3 + Qdrant.

---

# 60. Example End-to-End Workout Flow

```text
User submits Reel
        |
        v
POST /api/reels
        |
        v
PostgreSQL record
        |
        v
Celery job
        |
        v
Media acquisition
        |
        v
FFmpeg
   +----+----+
   |         |
 audio     frames
   |         |
   v         +--------+
 Whisper              |
   |                OCR
   |                  |
   +--------+---------+
            |
            v
           VLM
            |
            v
    Structured extraction
            |
            v
       PostgreSQL
            |
            v
          BGE-M3
            |
            v
          Qdrant
            |
            v
   Workout generation request
            |
            v
       Retrieve exercises
            |
            v
           LLM
            |
            v
      Workout plan + sources
```

---

# 61. Example End-to-End AI Search Flow

```text
User asks:
"Find videos explaining hybrid search."
        |
        v
FastAPI
        |
        v
BGE-M3
        |
        v
Qdrant
        |
        v
Top-K relevant Reels
        |
        v
Optional reranker
        |
        v
LLM
        |
        v
Grounded answer
        |
        +--> Reel URL
        +--> Creator
        +--> Topic
        +--> Summary
```

---

# 62. Example AI Report Flow

```text
AI Collection
      |
      v
PostgreSQL
      |
      +--> creator statistics
      +--> topic statistics
      +--> technology statistics
      +--> summaries
      |
      v
LLM
      |
      v
Collection Report
```

Example report sections:

```text
AI Collection Report

Total Reels: 127

Top Topics:
1. RAG
2. AI Agents
3. LLMs
4. Computer Vision
5. Fine-tuning

Most Mentioned Technologies:
- Python
- PyTorch
- Qdrant
- LangChain
- Transformers

Top Creators:
...

Recommended Learning Order:
...
```

---

# 63. Future Agent Architecture

Once the basic system is reliable, add an agent.

Possible tools:

```text
search_collection()
get_reel()
get_reel_transcript()
get_reel_analysis()
find_exercises()
generate_workout()
generate_collection_report()
compare_reels()
```

Then:

```text
User
 |
 v
Agent
 |
 +--> search_collection()
 |
 +--> get_reel_analysis()
 |
 +--> find_exercises()
 |
 +--> generate_workout()
 |
 v
Final answer
```

The agent should retrieve information through tools rather than relying entirely on its model memory.

---

# 64. Example Agent Queries

```text
"Find all the Reels I saved about RAG evaluation and summarize the different approaches."

"Use my Workout collection to create a 4-day upper/lower program."

"Which exercises for chest appear most frequently in my saved videos?"

"Find the AI Reels that mention Qdrant."

"Create a learning roadmap from my AI collection."

"Compare the three Reels I saved about LoRA."

"Which workout videos recommend exercises I can perform with dumbbells?"
```

---

# 65. Frontend Concept

A simple frontend could have:

```text
Dashboard

+---------------------------------------+
| Collections                            |
|                                       |
| [ Workout ]       54 Reels            |
| [ AI ]            127 Reels           |
+---------------------------------------+

Search:
[ What do you want to find?          ]

Recent processing:
Reel #127  ✓
Reel #126  ✓
Reel #125  Processing...
```

Collection page:

```text
AI

Search:
[ hybrid search                       ]

Filters:
Topic | Creator | Technology

Reels:
---------------------------------------
@creator1
RAG Evaluation

Summary:
...

[Open Reel]
---------------------------------------
```

Workout page:

```text
Workout

Goal: Hypertrophy
Days: 4
Duration: 60 min

[Generate Workout]

Your extracted exercises:
...
```

---

# 66. Why This Is a Strong CV Project

The project demonstrates:

- Computer Vision
- Vision-Language Models
- multimodal AI
- speech recognition
- OCR
- structured extraction
- embeddings
- vector databases
- RAG
- LLM reasoning
- asynchronous backend architecture
- FastAPI
- PostgreSQL
- Redis
- Celery
- Docker
- model evaluation
- production-oriented AI engineering

The important CV framing is:

> **Multimodal Personal Knowledge Engine for Short-Form Video**

rather than simply:

> Instagram AI app.

The technical contribution is the multimodal ingestion and knowledge pipeline.

---

# 67. What Makes It Portfolio-Quality

A strong final version should demonstrate:

1. Multimodal ingestion
2. Structured extraction
3. Evidence/provenance
4. Semantic retrieval
5. Grounded generation
6. Asynchronous processing
7. Model/provider abstraction
8. Evaluation
9. Error handling
10. Cost optimization
11. Production deployment
12. Clear documentation

A project with these characteristics is significantly stronger than a basic LLM wrapper.

---

# 68. What Not to Overbuild Initially

Do not start with:

- full autonomous Instagram browser control
- complicated multi-agent architecture
- pose estimation for every Reel
- custom VLM training
- Kubernetes
- complex frontend
- dozens of tools
- large-scale distributed infrastructure

First prove:

```text
Reel
 ->
multimodal extraction
 ->
accurate structured data
 ->
semantic retrieval
 ->
useful AI result
```

Then add infrastructure as the workload requires it.

---

# 69. Recommended Final Architecture

```text
                         +----------------+
                         |      User      |
                         +-------+--------+
                                 |
                                 v
                         +---------------+
                         |    FastAPI    |
                         +-------+-------+
                                 |
                                 v
                         +---------------+
                         | Redis / Celery|
                         +-------+-------+
                                 |
                                 v
                    +--------------------------+
                    |    Ingestion Worker      |
                    +------------+-------------+
                                 |
                  +--------------+--------------+
                  |              |              |
                  v              v              v
              Metadata        FFmpeg          Video
                                |              |
                                v              v
                              Audio          Frames
                                |              |
                                v         +----+----+
                             Whisper      |         |
                                |        OCR        VLM
                                |         |         |
                                +---------+---------+
                                          |
                                          v
                                +-------------------+
                                | LLM Extraction    |
                                +---------+---------+
                                          |
                                          v
                                +-------------------+
                                | Structured Data   |
                                +---------+---------+
                                          |
                     +--------------------+--------------------+
                     |                    |                    |
                     v                    v                    v
                PostgreSQL             Qdrant              MinIO/S3
                     |                    |
                     +---------+----------+
                               |
                               v
                         +-----------+
                         | AI Agent  |
                         +-----+-----+
                               |
                   +-----------+-----------+
                   |                       |
                   v                       v
              AI Knowledge              Workout
                 Search                  Planner
                   |                       |
                   v                       v
              Reports / RAG          Programs / Sources
```

---

# 70. Final Implementation Order

The recommended order is:

```text
1. Create FastAPI project
2. Configure PostgreSQL
3. Create Collection/Reel models
4. Implement Reel ingestion endpoint
5. Add object storage
6. Add FFmpeg
7. Add frame sampling
8. Add OCR
9. Add Whisper
10. Add VLM provider
11. Define structured Pydantic schemas
12. Add LLM extraction
13. Persist analysis
14. Add BGE-M3
15. Add Qdrant
16. Implement semantic search
17. Implement AI collection reports
18. Implement workout exercise extraction
19. Implement workout generation
20. Add source attribution
21. Add Redis/Celery
22. Add retries and job status
23. Add provider abstraction
24. Add evaluation dataset
25. Benchmark VLMs
26. Add hybrid retrieval
27. Add authentication/security
28. Dockerize
29. Add monitoring
30. Polish frontend and documentation
```

---

# 71. Definition of Done

The project can be considered a strong first production/portfolio version when a user can:

### Ingestion

- Create collections.
- Submit Reels.
- See processing status.
- Avoid duplicate processing.

### Multimodal analysis

- Analyze spoken Reels using Whisper.
- Analyze silent Reels using VLM + OCR.
- Extract visual information.
- Extract on-screen text.
- Preserve evidence and timestamps.

### AI collection

- Search by semantic meaning.
- Filter by collection/creator/topic.
- Get source links.
- Generate collection reports.
- Ask questions grounded in saved Reels.

### Workout collection

- Extract exercises.
- Extract sets/reps when available.
- Identify equipment.
- Identify target muscles.
- Generate a workout from extracted exercises.
- Provide source Reel for each selected exercise.

### Engineering

- Async processing.
- Retries.
- Persistent database.
- Vector database.
- Object storage.
- Model abstraction.
- Evaluation.
- Docker deployment.
- Logging and monitoring.

---

# 72. Core Design Philosophy

The system should be built around four layers:

```text
1. PERCEPTION
   Video + audio + OCR + metadata

2. UNDERSTANDING
   VLM + Whisper + LLM

3. KNOWLEDGE
   PostgreSQL + Qdrant

4. REASONING
   RAG + LLM + tools/agent
```

This separation makes the system easier to test, replace, scale, and explain.

The most important architectural principle is:

> **Do not ask the final LLM to rediscover information from raw videos every time. Process each Reel once, turn it into structured multimodal knowledge, index that knowledge, and let the reasoning layer operate over the resulting knowledge base.**
