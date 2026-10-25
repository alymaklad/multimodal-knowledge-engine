"""Local vision-language understanding with Qwen2.5-VL.

The same contract as the Claude vision provider, run on your own GPU. Qwen2.5-VL is used because it
takes multiple images in one turn natively, which is what the architecture's multi-frame reasoning
needs: a movement cannot be identified from a single frame.

**Loading is expensive, so the model is cached per process.** A Celery worker analysing many Sources
must not reload several gigabytes of weights per task.

Memory note for 8 GB cards: frames dominate the KV cache far more than the prompt does. If you hit
out-of-memory, lower `VLM_MAX_FRAMES` before reaching for a smaller model — eight high-resolution
frames cost more than the difference between a 3B and a 7B checkpoint.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.providers.base import VisionProvider, VisionResult
from app.providers.claude import NO_INVENTION_RULE, VISION_SCHEMA

logger = logging.getLogger(__name__)

# Mild: enough to break a degenerate loop, low enough that repeated legitimate tokens
# (JSON punctuation, recurring field names) are not distorted.
REPETITION_PENALTY = 1.05

SYSTEM_PROMPT = (
    "You analyse videos for a personal knowledge base. You report only what the "
    "supplied evidence shows. A missing value is always better than a guessed one. "
    "Reply with a single JSON object and nothing else."
)


@lru_cache(maxsize=2)
def _load(model_name: str, max_pixels: int) -> tuple[Any, Any]:
    """Load model and processor once per process."""
    try:
        import torch
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
    except ImportError as error:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Install torch and transformers to use VISION_PROVIDER=qwen, "
            "or set VISION_PROVIDER=evidence"
        ) from error

    logger.info("Loading vision model", extra={"model": model_name})
    load_kwargs: dict[str, Any] = {"torch_dtype": "auto", "device_map": "auto"}

    settings = get_settings()
    if settings.qwen_load_in_4bit:
        try:
            from transformers import BitsAndBytesConfig

            load_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
        except ImportError:
            logger.warning("bitsandbytes is unavailable; loading in full precision")

    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_name, **load_kwargs)
    # Capping pixels per frame is the main lever on VRAM for multi-image prompts.
    processor = AutoProcessor.from_pretrained(model_name, max_pixels=max_pixels)
    return model, processor


def release_vision_memory(*, unload: bool = False) -> None:
    """Hand VRAM back before the next model loads.

    Two levels, because they answer different problems:

    - `unload=False` frees the allocator's cached activation blocks and keeps the weights resident.
      Prefill on a multi-frame prompt reserves far more than the weights do, and that reservation
      otherwise survives until the process exits. Cheap, so it runs after every vision stage.
    - `unload=True` also drops the model itself. Reserved for a stage that failed on an allocation:
      once the vision stage has exhausted the device, the *next* model to load is the one that dies,
      which is exactly what happened here — the OOM was caught and reported correctly, then the
      embedding model loaded into the wreckage and took the worker process down with it.

    Never raises: releasing memory must not be able to fail a job that otherwise succeeded.
    """
    if unload:
        _load.cache_clear()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as error:  # pragma: no cover - depends on local hardware
        logger.debug("Could not release vision memory: %s", error)


class QwenVisionProvider(VisionProvider):
    """Multi-frame visual understanding on local hardware."""

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model or settings.qwen_vision_model
        self._max_frames = settings.vlm_max_frames
        self._max_pixels = settings.qwen_max_pixels
        self._max_new_tokens = settings.qwen_max_new_tokens

    def analyze_video(self, frame_paths: list[Path], transcript: str, ocr_text: str) -> VisionResult:
        from app.providers.claude import select_frames

        frames = select_frames(frame_paths, self._max_frames)
        if not frames:
            return VisionResult(
                description="No frames were sampled from this source, so no visual analysis is possible.",
                analysis={"frame_count": 0, "evidence": {}},
                confidence=0.0,
                model=f"{self._model_name}-vision",
            )

        model, processor = _load(self._model_name, self._max_pixels)
        # Resolve first: media asset paths are stored relative to the project root.
        resolved = [frame_path.resolve() for frame_path in frames]
        content: list[dict[str, Any]] = []
        for index, frame_path in enumerate(resolved, start=1):
            content.append({"type": "text", "text": f"Frame {index} of {len(resolved)}:"})
            content.append({"type": "image", "image": str(frame_path)})
        content.append({"type": "text", "text": _instruction(transcript, ocr_text)})

        messages = [
            {"role": "system", "content": [{"type": "text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": content},
        ]
        payload = _generate(model, processor, messages, resolved, self._max_new_tokens)
        parsed = _parse_json(payload)

        if parsed is None:
            # A model that did not return usable JSON must not be laundered into a confident record.
            return VisionResult(
                description=payload.strip()[:2000] or "The vision model returned no usable output.",
                analysis={"frame_count": len(frames), "evidence": {}, "unparsed": True},
                confidence=None,
                model=f"{self._model_name}-vision",
            )

        description = parsed.pop("description", "").strip()
        confidence = parsed.pop("confidence", None)
        parsed["evidence"] = _prune_evidence(parsed.get("evidence"), parsed)
        parsed["frame_count"] = len(frames)
        parsed["frames_available"] = len(frame_paths)
        return VisionResult(
            description=description or "The vision model returned no description.",
            analysis=parsed,
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            model=f"{self._model_name}-vision",
        )


def _fit_to_budget(text: str, max_chars: int, label: str) -> str:
    """Trim evidence text to a character budget, on line boundaries.

    Attention is quadratic in prompt length, so unbounded evidence is a hard failure rather than a
    slow one. Measured on an 84-second screen recording: 1,144 *distinct* on-screen lines, 32,597
    characters, ~10,533 tokens — 96% of the text prompt. The resulting prefill asked for a 10.32 GiB
    attention tensor on an 8 GiB card and the stage died. Deduplication cannot help when every line
    genuinely differs; only a bound can.

    Truncation is chronological because the lines arrive in frame order and the model is asked to
    describe what happens *across* the frames. The marker is deliberate: the model is told the
    evidence is partial rather than being left to treat a truncated list as the whole story — the
    same reason this pipeline prefers a null field to a guessed one.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    lines = text.splitlines()
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    dropped = len(lines) - len(kept)
    kept.append(f"[... {dropped} further {label} lines omitted: evidence exceeded the context budget]")
    logger.info(
        "Truncated %s evidence for the vision prompt",
        label,
        extra={"kept_lines": len(kept) - 1, "dropped_lines": dropped, "budget_chars": max_chars},
    )
    return "\n".join(kept)


def _instruction(transcript: str, ocr_text: str) -> str:
    settings = get_settings()
    ocr_text = _fit_to_budget(ocr_text, settings.vlm_max_ocr_chars, "on-screen text")
    transcript = _fit_to_budget(transcript, settings.vlm_max_transcript_chars, "transcript")
    return (
        "The frames above are sampled in order from one video.\n\n"
        "ON-SCREEN TEXT (OCR):\n" + (ocr_text or "(none detected)") + "\n\n"
        "TRANSCRIPT:\n" + (transcript or "(this video has no speech)") + "\n\n"
        "Describe what happens across the frames as a sequence, then fill in only the fields the "
        "evidence actually supports.\n\n" + NO_INVENTION_RULE + "\n\n"
        "Return a single JSON object with exactly these keys, and no others:\n"
        + _FIELD_GUIDE
        + "\n\nEvery value must come from the frames, the on-screen text, or the transcript above. "
        "Use null for anything they do not support. Do not copy the placeholder wording."
    )


# Deliberately a typed field guide rather than a filled-in example. Two failure modes showed up in
# testing and both produce fabricated knowledge, which is the one thing this pipeline must not do:
#   - pasting the JSON Schema made the model echo {"type": "object", "properties": {...}} back;
#   - a worked example made it copy the example's own values ("8-10" reps) over the real evidence.
# Naming each field and its type avoids giving it anything plausible to copy.
_FIELD_GUIDE = "\n".join(
    [
        '  "description": string - what happens across the frames, in order',
        '  "topic": string or null - what the video is about, in a few words',
        '  "key_points": array of strings - the main things it shows or says, one claim each',
        '  "entities": array of strings - people, products, tools, places, or terms it names',
        '  "evidence": object mapping each field you filled to one of '
        '"visual", "ocr", "transcript", "metadata", "inferred"',
        '  "confidence": number between 0 and 1',
    ]
)


@lru_cache(maxsize=2)
def _schema_constraint(processor_id: int, tokenizer: Any) -> Any:
    """A token filter that makes schema-invalid output impossible to emit.

    Everything before this was detection after the fact: parse what came back, and discard it when
    it was malformed. Across runs on one real source this model produced a schema-shaped echo, a
    repetition loop, unescaped control characters, and single-quoted strings with an unterminated
    array. Each is individually patchable, but patching them one at a time means writing a JSON
    repairer - and a repairer has to guess at intent, which risks inventing values.

    Constraining the decoder removes the class of failure instead: at every step the model may only
    choose tokens that keep the output a valid instance of the schema.
    """
    _patch_tokenizer_base_location()
    from lmformatenforcer import JsonSchemaParser
    from lmformatenforcer.integrations.transformers import (
        build_transformers_prefix_allowed_tokens_fn,
    )

    return build_transformers_prefix_allowed_tokens_fn(tokenizer, JsonSchemaParser(VISION_SCHEMA))


def _patch_tokenizer_base_location() -> None:
    """Compatibility shim for transformers 5.

    lm-format-enforcer's transformers integration imports `PreTrainedTokenizerBase` from
    `transformers.tokenization_utils`, where it lived through transformers 4. In 5.x it moved to
    `transformers.tokenization_utils_base`, and that single failed import makes the integration
    raise `ImportError("transformers is not installed")` — which is exactly wrong and sent me
    looking in the wrong place at first.

    Restoring the old name is a smaller and more honest fix than reimplementing their token
    enforcer, and it disappears when upstream catches up.
    """
    import transformers.tokenization_utils as tokenization_utils

    if not hasattr(tokenization_utils, "PreTrainedTokenizerBase"):
        from transformers.tokenization_utils_base import PreTrainedTokenizerBase

        tokenization_utils.PreTrainedTokenizerBase = PreTrainedTokenizerBase


def _constraint_for(processor: Any) -> Any | None:
    if not get_settings().qwen_constrained_json:
        return None
    try:
        tokenizer = getattr(processor, "tokenizer", processor)
        return _schema_constraint(id(processor), tokenizer)
    except ImportError:
        logger.warning(
            "lm-format-enforcer is not installed; falling back to unconstrained generation. "
            "Install it to guarantee schema-valid output."
        )
    except Exception as error:  # pragma: no cover - depends on the installed version
        logger.warning("Could not build the schema constraint (%s); generating unconstrained", error)
    return None


def _generate(
    model: Any, processor: Any, messages: list[dict[str, Any]], frames: list[Path], max_new_tokens: int
) -> str:
    import torch
    from PIL import Image

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    # Opened directly from the resolved paths, in prompt order.
    images = [Image.open(frame).convert("RGB") for frame in frames]
    inputs = processor(text=[text], images=images, padding=True, return_tensors="pt").to(model.device)

    constraint = _constraint_for(processor)
    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            # Greedy, for reproducible extraction: the same source should yield the same record.
            do_sample=False,
            # A mild penalty against degenerate repetition. Still useful under constraint, which
            # forbids invalid *structure* but would happily allow a valid array of repeated strings.
            repetition_penalty=REPETITION_PENALTY,
            prefix_allowed_tokens_fn=constraint,
        )
    trimmed = [output[len(prompt):] for prompt, output in zip(inputs.input_ids, generated)]
    return processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]


def _prune_evidence(evidence: Any, fields: dict[str, Any]) -> dict[str, str]:
    """Keep evidence tags only for fields the model actually filled.

    Observed in testing: the model tags every key in the guide, including ones it left null. An
    evidence tag on a null value claims a source for a fact that was never extracted, which makes
    the audit view misleading in precisely the place it is meant to be trustworthy.
    """
    if not isinstance(evidence, dict):
        return {}
    return {
        field: str(source)
        for field, source in evidence.items()
        if fields.get(field) not in (None, "", [], {})
    }


def _parse_json(payload: str) -> dict[str, Any] | None:
    """Local models often wrap JSON in prose or a code fence; recover it without guessing content."""
    candidate = payload.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", candidate, re.S)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]
    try:
        # strict=False permits literal control characters inside string values. Observed on a real
        # source: the model quoted the narration into a field and left the newlines unescaped, which
        # strict JSON rejects outright even though the structure is sound. Tolerating that recovers
        # a usable record instead of discarding a correct answer over whitespace encoding.
        parsed = json.loads(candidate, strict=False)
    except ValueError:
        logger.warning("Vision model returned unparsable output", extra={"preview": payload[:200]})
        return None
    if not isinstance(parsed, dict):
        return None
    # Defence in depth: if the model mirrors the schema back anyway, take the instance out of it
    # rather than reporting an empty result.
    if parsed.get("type") == "object" and isinstance(parsed.get("properties"), dict):
        logger.info("Vision model returned a schema-shaped object; unwrapping properties")
        return parsed["properties"]
    return parsed
