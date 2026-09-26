"""Slot-segment scoring.

    S(i,j) = w_s*S_semantic + w_v*S_visual + w_m*S_motion
             + w_q*S_quality + w_e*S_emotion

Weights are configuration, not constants baked into logic, so the ablation in
block F can move them without touching the matcher.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..specs.edit_plan import Intent, Slot
from ..specs.segment import MaterialSegment
from .affinity import motion_affinity, role_affinity, scale_affinity

W_SEMANTIC = 0.35
W_VISUAL = 0.20
W_MOTION = 0.15
W_QUALITY = 0.20
W_EMOTION = 0.10

# Below this a segment is not a plausible filler for the slot at all, and
# offering it would produce a visibly wrong cut rather than a compromise.
VIABLE_THRESHOLD = 0.25


@dataclass(frozen=True)
class Weights:
    semantic: float = W_SEMANTIC
    visual: float = W_VISUAL
    motion: float = W_MOTION
    quality: float = W_QUALITY
    emotion: float = W_EMOTION


DEFAULT_WEIGHTS = Weights()


@dataclass
class Score:
    total: float
    semantic: float
    visual: float
    motion: float
    quality: float
    emotion: float

    def as_dict(self) -> dict[str, float]:
        return {
            "total": round(self.total, 4),
            "semantic": round(self.semantic, 4),
            "visual": round(self.visual, 4),
            "motion": round(self.motion, 4),
            "quality": round(self.quality, 4),
            "emotion": round(self.emotion, 4),
        }


def emotion_overlap(intent: Intent, segment: MaterialSegment) -> float:
    if not segment.emotion:
        return 0.5  # unknown is neutral, not bad
    wanted = {s.lower() for s in intent.style}
    have = {e.lower() for e in segment.emotion}
    if not wanted:
        return 0.5
    return len(wanted & have) / len(wanted) if wanted & have else 0.3


def duration_fit(slot: Slot, segment: MaterialSegment) -> float:
    """How comfortably the segment can fill the slot's length.

    A segment shorter than the slot needs speed or a shortened slot; one that
    is much longer is fine - it just gets trimmed. So the penalty is one-sided.
    """
    if segment.kind == "photo":
        # A still has no length of its own: it holds for exactly as long as
        # the slot wants. That makes photos unusually easy to place, which is
        # correct rather than a bug to compensate for.
        return 1.0
    low, high = slot.duration_range or (slot.target_duration, slot.target_duration)
    if segment.duration >= low:
        return 1.0
    return max(0.0, segment.duration / max(low, 1e-6))


def score_pair(
    slot: Slot,
    segment: MaterialSegment,
    intent: Intent,
    slot_vector: np.ndarray | None = None,
    segment_vector: np.ndarray | None = None,
    weights: Weights = DEFAULT_WEIGHTS,
) -> Score:
    semantic = role_affinity(slot.semantic_role, segment.semantic_role)

    visual = scale_affinity(slot.shot_type, segment.shot_type)
    if slot_vector is not None and segment_vector is not None:
        if slot_vector.size and segment_vector.size == slot_vector.size:
            # Cosine of unit vectors is in [-1, 1]; rescale to [0, 1] so it
            # combines with the categorical terms on the same footing.
            cosine = float(np.dot(slot_vector, segment_vector))
            visual = 0.5 * visual + 0.5 * (cosine + 1.0) / 2.0

    motion = motion_affinity(slot.motion, segment.camera_motion)
    quality = segment.technical_quality if segment.technical_quality is not None else 0.5
    # TODO(review): `quality` here is the deterministic measurement only.
    # `segment.interestingness` and `segment.composition_score` - the model's
    # judgement of whether the shot is worth watching and well framed - are
    # stored and never read, so today they cost tokens and change nothing.
    # They belong in this term (or their own) once the prompt makes them
    # discriminate; see the TODO in providers/prompts.py.
    emotion = emotion_overlap(intent, segment)

    total = (
        weights.semantic * semantic
        + weights.visual * visual
        + weights.motion * motion
        + weights.quality * quality
        + weights.emotion * emotion
    )
    # A segment that cannot cover the slot at all is discounted rather than
    # excluded: adaptation may still rescue it by shortening the slot.
    total *= 0.5 + 0.5 * duration_fit(slot, segment)

    return Score(
        total=total,
        semantic=semantic,
        visual=visual,
        motion=motion,
        quality=quality,
        emotion=emotion,
    )
