"""Establish PyTorch's cuDNN before other native runtimes claim the process.

RapidOCR (onnxruntime) and faster-whisper (CTranslate2) each ship their own CUDA libraries. On
Windows the first one loaded wins the DLL search, and if one of those loads before PyTorch, the
later cuDNN call fails with:

    Could not load symbol cudnnGetLibConfig. Error code 127

That is a **native abort, not a Python exception** — the per-stage error handling in the analysis
pipeline cannot catch it, so it takes the whole worker down rather than degrading one stage.

Measured on this project: OCR then transcription then vision crashed reliably; warming torch's cuDNN
first made the identical sequence work. So any module that is about to import one of those runtimes
calls `prefer_torch_cudnn()` first.

Safe on machines with no GPU and no torch: it does nothing and never raises.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_warmed = False


def prefer_torch_cudnn() -> None:
    """Run one tiny cuDNN convolution so PyTorch's libraries are the ones already loaded."""
    global _warmed
    if _warmed:
        return
    _warmed = True
    try:
        import torch

        if not torch.cuda.is_available():
            return
        import torch.nn.functional as functional

        functional.conv2d(
            torch.zeros(1, 1, 8, 8, device="cuda"), torch.zeros(1, 1, 3, 3, device="cuda")
        )
        logger.debug("Warmed torch cuDNN ahead of other native runtimes")
    except Exception as error:  # pragma: no cover - depends on local hardware
        # Never fatal: a CPU-only or torch-less install is a supported configuration.
        logger.debug("Skipping cuDNN warm-up: %s", error)


def half_precision_model_kwargs(enabled: bool) -> dict[str, object]:
    """`from_pretrained` kwargs that load a retrieval model in fp16, when that is worth doing.

    BGE-M3 and bge-reranker-v2-m3 ship as fp32 (2166 MB each on disk). Held together with a 4-bit
    Qwen2.5-VL they exceed an 8 GB card, and because every loader here is `lru_cache`d nothing is
    ever released — so the peak is the sum, not the maximum. fp16 halves both to ~1083 MB.

    Returned as load kwargs rather than a post-load `.half()` so the fp32 copy is never materialised;
    converting afterwards would still peak at the full size, which is the memory we are trying to
    avoid.

    Returns `{}` unless CUDA is actually present: fp16 on CPU falls back to slow emulated kernels,
    so half precision is a GPU-only win.
    """
    if not enabled:
        return {}
    try:
        import torch

        if not torch.cuda.is_available():
            return {}
        return {"dtype": torch.float16}
    except Exception as error:  # pragma: no cover - depends on local install
        logger.debug("Skipping fp16 retrieval weights: %s", error)
        return {}
