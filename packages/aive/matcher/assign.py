"""Assigning segments to slots.

Plain greedy in timeline order has a specific, visible failure: early slots
take the best footage and the tail is left with whatever remains, so the video
gets worse as it goes. Since the second half is where viewers drop off, that
is the worst possible place to concentrate the weak shots.

Two cheap corrections, both dependency-free:

* **assign scarce slots first** - a slot with only two viable candidates has
  to be served before one with forty, regardless of where it sits in time;
* **a bounded repair pass** - swap pairs of assignments whenever the swap
  raises the total score and breaks no constraint.

A full optimal assignment (Hungarian / min-cost flow) is the later upgrade,
but it cannot express the sequence constraints below on its own, so the repair
pass would still be needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..specs.edit_plan import EditPlanSpec, Slot
from ..specs.segment import MaterialSegment
from .score import DEFAULT_WEIGHTS, VIABLE_THRESHOLD, Score, Weights, score_pair

# Repetition rules. These are hard constraints, not preferences: reusing one
# source too often, or twice in a row, is the most recognisable signature of
# machine-assembled video.
MAX_USES_PER_SOURCE = 3
SIMILARITY_LIMIT = 0.95
REPAIR_PASSES = 3

# Score multiplier by how many times a source has already been used. Without
# this, the matcher reuses its favourite clips up to the cap while untouched
# footage sits unused: the cap alone stops repetition from being extreme, but
# it does not make variety the default. Reuse should be a fallback for when
# the alternative is clearly worse, not a preference.
REUSE_DECAY: tuple[float, ...] = (1.0, 0.72, 0.45)


@dataclass
class Candidate:
    segment: MaterialSegment
    score: Score


@dataclass
class Assignment:
    slot: Slot
    segment: MaterialSegment | None
    score: Score | None
    alternatives: list[Candidate] = field(default_factory=list)

    @property
    def filled(self) -> bool:
        return self.segment is not None


def _viable(
    plan: EditPlanSpec,
    slot: Slot,
    segments: list[MaterialSegment],
    vectors: dict[str, np.ndarray],
    weights: Weights,
) -> list[Candidate]:
    out = [
        Candidate(
            segment=segment,
            score=score_pair(
                slot,
                segment,
                plan.intent,
                segment_vector=vectors.get(segment.segment_id),
                weights=weights,
            ),
        )
        for segment in segments
        if segment.is_usable
    ]
    out = [c for c in out if c.score.total >= VIABLE_THRESHOLD]
    out.sort(key=lambda c: c.score.total, reverse=True)
    return out


def _reuse_factor(candidate: Candidate, usage: dict[str, int]) -> float:
    used = usage.get(candidate.segment.source_id, 0)
    return REUSE_DECAY[min(used, len(REUSE_DECAY) - 1)]


def _too_similar(
    a: MaterialSegment, b: MaterialSegment, vectors: dict[str, np.ndarray]
) -> bool:
    va, vb = vectors.get(a.segment_id), vectors.get(b.segment_id)
    if va is None or vb is None or va.size == 0 or va.size != vb.size:
        return False
    return float(np.dot(va, vb)) > SIMILARITY_LIMIT


def _allowed(
    segment: MaterialSegment,
    slot_index: int,
    chosen: dict[int, MaterialSegment],
    usage: dict[str, int],
    vectors: dict[str, np.ndarray],
) -> bool:
    if usage.get(segment.source_id, 0) >= MAX_USES_PER_SOURCE:
        return False
    for neighbour_index in (slot_index - 1, slot_index + 1):
        neighbour = chosen.get(neighbour_index)
        if neighbour is None:
            continue
        if neighbour.source_id == segment.source_id:
            return False
        if _too_similar(neighbour, segment, vectors):
            return False
    return True


def assign(
    plan: EditPlanSpec,
    segments: list[MaterialSegment],
    vectors: dict[str, np.ndarray] | None = None,
    weights: Weights = DEFAULT_WEIGHTS,
) -> list[Assignment]:
    vectors = vectors or {}
    slots = sorted(plan.slots, key=lambda s: s.order)

    candidates = {
        slot.slot_id: _viable(plan, slot, segments, vectors, weights) for slot in slots
    }

    # Scarcity first: a slot with few options must choose before a slot with
    # many, or it will find nothing left.
    order = sorted(
        range(len(slots)),
        key=lambda i: (
            len(candidates[slots[i].slot_id]),
            -slots[i].required,
        ),
    )

    chosen: dict[int, MaterialSegment] = {}
    scores: dict[int, Score] = {}
    usage: dict[str, int] = {}

    for index in order:
        slot = slots[index]
        allowed = [
            c
            for c in candidates[slot.slot_id]
            if _allowed(c.segment, index, chosen, usage, vectors)
        ]
        if not allowed:
            continue
        best = max(allowed, key=lambda c: c.score.total * _reuse_factor(c, usage))
        chosen[index] = best.segment
        scores[index] = best.score
        usage[best.segment.source_id] = usage.get(best.segment.source_id, 0) + 1

    _repair(slots, candidates, chosen, scores, usage, vectors)

    return [
        Assignment(
            slot=slot,
            segment=chosen.get(index),
            score=scores.get(index),
            alternatives=[
                c
                for c in candidates[slot.slot_id][:4]
                if chosen.get(index) is None or c.segment.segment_id != chosen[index].segment_id
            ][:3],
        )
        for index, slot in enumerate(slots)
    ]


def _repair(
    slots: list[Slot],
    candidates: dict[str, list[Candidate]],
    chosen: dict[int, MaterialSegment],
    scores: dict[int, Score],
    usage: dict[str, int],
    vectors: dict[str, np.ndarray],
) -> None:
    """Swap pairs while it improves the total and breaks nothing.

    Bounded to a few passes: this is a cleanup for greedy's worst mistakes,
    not a search for the optimum.
    """
    lookup = {
        slot.slot_id: {c.segment.segment_id: c.score for c in candidates[slot.slot_id]}
        for slot in slots
    }

    for _ in range(REPAIR_PASSES):
        improved = False
        indices = [i for i in chosen if chosen[i] is not None]
        for a in indices:
            for b in indices:
                if a >= b:
                    continue
                seg_a, seg_b = chosen[a], chosen[b]
                slot_a, slot_b = slots[a], slots[b]
                new_a = lookup[slot_a.slot_id].get(seg_b.segment_id)
                new_b = lookup[slot_b.slot_id].get(seg_a.segment_id)
                if new_a is None or new_b is None:
                    continue
                gain = (new_a.total + new_b.total) - (
                    scores[a].total + scores[b].total
                )
                if gain <= 1e-6:
                    continue

                swapped = dict(chosen)
                swapped[a], swapped[b] = seg_b, seg_a
                if not _swap_is_legal(a, b, swapped, vectors):
                    continue

                chosen[a], chosen[b] = seg_b, seg_a
                scores[a], scores[b] = new_a, new_b
                improved = True
        if not improved:
            break


def _swap_is_legal(
    a: int, b: int, chosen: dict[int, MaterialSegment], vectors: dict[str, np.ndarray]
) -> bool:
    """Neighbour rules only; per-source usage is unchanged by a swap."""
    for index in (a, b):
        segment = chosen.get(index)
        if segment is None:
            continue
        for neighbour_index in (index - 1, index + 1):
            neighbour = chosen.get(neighbour_index)
            if neighbour is None:
                continue
            if neighbour.source_id == segment.source_id:
                return False
            if _too_similar(neighbour, segment, vectors):
                return False
    return True
