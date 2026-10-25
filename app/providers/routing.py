"""Model routing (architecture section 52) and the cost rules behind it (section 51).

The routing decision is made by a cheap classifier over evidence that already exists — frame count,
whether OCR found text, whether there is speech — rather than by a model call, because a router
that costs a model call to decide which model to call defeats its own purpose.

    simple / static content   -> the smaller vision model
    movement or thin evidence -> the stronger vision model
    low confidence afterwards -> escalate once to the stronger model
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.providers.base import VisionProvider, VisionResult


@dataclass(frozen=True)
class RoutingDecision:
    tier: str
    model: str
    reason: str


def classify_complexity(
    *, frame_count: int, has_transcript: bool, has_ocr_text: bool
) -> RoutingDecision:
    """Decide which tier a Reel needs before any model is called."""
    settings = get_settings()
    if frame_count > settings.routing_simple_max_frames:
        return RoutingDecision(
            "strong",
            settings.anthropic_model,
            f"{frame_count} frames suggests movement that needs temporal reasoning",
        )
    if not has_transcript and not has_ocr_text:
        return RoutingDecision(
            "strong",
            settings.anthropic_model,
            "No speech or on-screen text, so the visual channel is the only evidence",
        )
    return RoutingDecision(
        "small",
        settings.anthropic_small_model,
        "Short clip with textual evidence available",
    )


class RoutingVisionProvider(VisionProvider):
    """Runs the routed tier, then escalates once if the result comes back uncertain.

    Escalation is capped at a single retry: a second opinion is worth paying for, a third is
    usually paying twice to learn the same thing.
    """

    def __init__(
        self,
        *,
        small_provider: VisionProvider,
        strong_provider: VisionProvider,
        escalate_below: float | None = None,
    ) -> None:
        self._small = small_provider
        self._strong = strong_provider
        self._escalate_below = (
            escalate_below if escalate_below is not None else get_settings().auto_accept_confidence
        )
        self.last_decision: RoutingDecision | None = None
        self.escalated = False

    def analyze_video(self, frame_paths: list[Path], transcript: str, ocr_text: str) -> VisionResult:
        decision = classify_complexity(
            frame_count=len(frame_paths),
            has_transcript=bool(transcript.strip()),
            has_ocr_text=bool(ocr_text.strip()),
        )
        self.last_decision = decision
        self.escalated = False
        if decision.tier == "strong":
            return _tagged(self._strong.analyze_video(frame_paths, transcript, ocr_text), decision, False)

        result = self._small.analyze_video(frame_paths, transcript, ocr_text)
        if result.confidence is not None and result.confidence < self._escalate_below:
            self.escalated = True
            escalation = RoutingDecision(
                "strong",
                get_settings().anthropic_model,
                f"Escalated: the small model returned {result.confidence:.2f}",
            )
            self.last_decision = escalation
            return _tagged(
                self._strong.analyze_video(frame_paths, transcript, ocr_text), escalation, True
            )
        return _tagged(result, decision, False)


def _tagged(result: VisionResult, decision: RoutingDecision, escalated: bool) -> VisionResult:
    """Record why this tier ran, so a cost or quality question can be answered afterwards."""
    analysis = dict(result.analysis)
    analysis["routing"] = {"tier": decision.tier, "reason": decision.reason, "escalated": escalated}
    return VisionResult(
        description=result.description,
        analysis=analysis,
        confidence=result.confidence,
        model=result.model,
    )
