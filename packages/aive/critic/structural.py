"""Stage 1 of best-of-N: score candidates without rendering anything.

Pure rules, no model call, so it is free, instant and fully explainable. That
matters more than it sounds: it means every candidate can be scored, and only
the survivors cost money at the proxy-render stage.

It cannot judge whether a video looks good - that is the video critic's job
after a proxy render. What it can do is catch the failures that are visible in
the structure alone: repeated shots, a rhythm that ignores the plan, slots
that went unfilled, a length far off target.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..specs.edit_plan import EditPlanSpec
from ..specs.segment import MaterialSegment
from ..specs.timeline import TimelineSpec
from ..specs.validate import Violation, validate_timeline

# TODO(review): these weights decide which candidate wins, and they are
# judgement calls. The honest test is whether the winner matches what a person
# picks from the same set - which is exactly what block F's preference data is
# for. Until then, treat any ranking difference under ~0.02 as noise.
WEIGHTS: dict[str, float] = {
    "slot_coverage": 0.16,
    "shot_diversity": 0.15,
    "rhythm_fidelity": 0.16,
    "semantic_match": 0.16,
    "duration_error": 0.14,
    "repetition_penalty": 0.14,
    "beat_alignment": 0.09,
}
VIOLATION_PENALTY = 0.05


@dataclass
class StructuralScore:
    overall: float
    dimensions: dict[str, float] = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "overall": round(self.overall, 4),
            "dimensions": {k: round(v, 4) for k, v in self.dimensions.items()},
            "violations": [v.code for v in self.violations],
            "notes": self.notes,
        }


def _entropy(counts: list[int]) -> float:
    total = sum(counts)
    if total <= 0 or len(counts) <= 1:
        return 0.0
    probs = [c / total for c in counts if c]
    raw = -sum(p * math.log(p) for p in probs)
    return raw / math.log(len(counts))


def slot_coverage(plan: EditPlanSpec, timeline: TimelineSpec) -> float:
    if not plan.slots:
        return 0.0
    used = {c.slot_id for c in timeline.clips if c.slot_id}
    return len(used) / len(plan.slots)


def shot_diversity(
    timeline: TimelineSpec, segments: dict[str, MaterialSegment]
) -> float:
    clips = timeline.clips
    if not clips:
        return 0.0
    # Two things matter: how many different sources were used, and whether the
    # shot scales vary. A cut made entirely of medium shots from two files is
    # technically valid and visually flat.
    source_ratio = len({c.source_id for c in clips}) / len(clips)
    scales = [
        segments[c.slot_id].shot_type
        for c in clips
        if c.slot_id and c.slot_id in segments
    ]
    if scales:
        unique = sorted(set(scales))
        scale_entropy = _entropy([scales.count(s) for s in unique])
    else:
        scale_entropy = 0.0
    return 0.6 * source_ratio + 0.4 * scale_entropy


def rhythm_fidelity(plan: EditPlanSpec, timeline: TimelineSpec) -> float:
    """How closely the realised shot lengths follow the planned pattern."""
    wanted = plan.rhythm.shot_duration_pattern
    actual = [c.timeline_duration for c in timeline.clips]
    if not wanted or not actual:
        return 0.5
    length = min(len(wanted), len(actual))
    errors = [
        abs(actual[i] - wanted[i]) / max(wanted[i], 1e-6) for i in range(length)
    ]
    mae = sum(errors) / length
    return max(0.0, 1.0 - mae)


def semantic_match(
    plan: EditPlanSpec, timeline: TimelineSpec, segments: dict[str, MaterialSegment]
) -> float:
    from ..matcher.affinity import role_affinity

    scores = []
    for clip in timeline.clips:
        slot = plan.slot_by_id(clip.slot_id) if clip.slot_id else None
        segment = segments.get(clip.slot_id) if clip.slot_id else None
        if slot is None or segment is None:
            continue
        scores.append(role_affinity(slot.semantic_role, segment.semantic_role))
    return sum(scores) / len(scores) if scores else 0.5


def duration_error(plan: EditPlanSpec, timeline: TimelineSpec) -> float:
    target = plan.intent.target_duration
    if target <= 0:
        return 0.5
    relative = abs(timeline.duration - target) / target
    tolerance = max(plan.intent.duration_tolerance, 1e-6)
    return max(0.0, 1.0 - relative / tolerance)


def repetition_penalty(
    timeline: TimelineSpec, vectors: dict[str, np.ndarray]
) -> tuple[float, list[str]]:
    """1.0 means no repetition problems; every offence subtracts."""
    clips = timeline.clips
    if len(clips) < 2:
        return 1.0, []

    notes: list[str] = []
    offences = 0

    for previous, current in zip(clips, clips[1:]):
        if previous.source_id == current.source_id:
            offences += 1
            notes.append(f"consecutive source {current.source_id}")
        va = vectors.get(previous.slot_id or "")
        vb = vectors.get(current.slot_id or "")
        if va is not None and vb is not None and va.size and va.size == vb.size:
            if float(np.dot(va, vb)) > 0.95:
                offences += 1
                notes.append(f"near-identical shots at {current.timeline_start:.1f}s")

    counts: dict[str, int] = {}
    for clip in clips:
        counts[clip.source_id] = counts.get(clip.source_id, 0) + 1
    for source, count in counts.items():
        if count > 3:
            offences += count - 3
            notes.append(f"{source} used {count} times")

    return max(0.0, 1.0 - offences / len(clips)), notes


def beat_alignment(
    plan: EditPlanSpec, timeline: TimelineSpec, beat_times: list[float] | None
) -> float:
    """How close the cuts sit to the beat, weighted by how much was asked for.

    Neutral when there is no music or no sync was requested: an edit that was
    never meant to be on the grid must not be marked down for not being on it.
    """
    from ..matcher.beats import build_grid, offsets_to_beats

    wanted = plan.rhythm.music_sync_strength
    if not beat_times or wanted <= 0 or len(timeline.clips) < 2:
        return 0.5

    grid = build_grid(beat_times, timeline.audio.music_in, timeline.duration)
    offsets = offsets_to_beats(timeline.clips, grid)
    if not offsets:
        return 0.5

    interval = grid.interval
    # Mean absolute offset as a fraction of half a beat: 0 is exactly on the
    # grid, 1 is as far off as it is possible to be.
    error = sum(abs(o) for o in offsets) / len(offsets) / max(interval / 2, 1e-6)
    accuracy = max(0.0, 1.0 - error)
    # Scale by intent: a plan asking for weak sync should not be judged as
    # harshly as one asking for strict sync.
    return 0.5 + (accuracy - 0.5) * wanted


def score(
    plan: EditPlanSpec,
    timeline: TimelineSpec,
    segments: list[MaterialSegment] | None = None,
    vectors: dict[str, np.ndarray] | None = None,
    beat_times: list[float] | None = None,
) -> StructuralScore:
    # Segments and vectors are keyed by slot_id so every lookup follows the
    # plan rather than guessing from clip order.
    by_slot: dict[str, MaterialSegment] = {}
    slot_vectors: dict[str, np.ndarray] = {}
    if segments:
        index = {s.segment_id: s for s in segments}
        source_index: dict[str, list[MaterialSegment]] = {}
        for segment in segments:
            source_index.setdefault(segment.source_id, []).append(segment)
        for clip in timeline.clips:
            if not clip.slot_id:
                continue
            match = _segment_for_clip(clip, source_index)
            if match is not None:
                by_slot[clip.slot_id] = match
                if vectors and match.segment_id in vectors:
                    slot_vectors[clip.slot_id] = vectors[match.segment_id]
        del index

    dimensions = {
        "slot_coverage": slot_coverage(plan, timeline),
        "shot_diversity": shot_diversity(timeline, by_slot),
        "rhythm_fidelity": rhythm_fidelity(plan, timeline),
        "semantic_match": semantic_match(plan, timeline, by_slot),
        "duration_error": duration_error(plan, timeline),
    }
    repetition, notes = repetition_penalty(timeline, slot_vectors)
    dimensions["repetition_penalty"] = repetition
    dimensions["beat_alignment"] = beat_alignment(plan, timeline, beat_times)

    violations = validate_timeline(timeline, plan)
    soft = [v for v in violations if v.severity == "soft"]
    hard = [v for v in violations if v.severity == "hard"]

    overall = sum(WEIGHTS[k] * v for k, v in dimensions.items())
    overall -= VIOLATION_PENALTY * len(soft)
    if hard:
        # A structurally invalid timeline must never win a comparison, however
        # good its other dimensions look.
        overall = 0.0
        notes.append(f"{len(hard)} hard violations")

    return StructuralScore(
        overall=max(0.0, min(1.0, overall)),
        dimensions=dimensions,
        violations=violations,
        notes=notes,
    )


def _segment_for_clip(clip, source_index) -> MaterialSegment | None:
    """Find which segment a clip came from, by source and time overlap."""
    for segment in source_index.get(clip.source_id, []):
        if segment.start - 1e-3 <= clip.source_in < segment.end + 1e-3:
            return segment
    return None


def rank(
    plan_by_id: dict[str, EditPlanSpec],
    candidates: list[TimelineSpec],
    segments: list[MaterialSegment] | None = None,
    vectors: dict[str, np.ndarray] | None = None,
    beat_times: list[float] | None = None,
) -> list[tuple[TimelineSpec, StructuralScore]]:
    scored = [
        (
            timeline,
            score(
                plan_by_id[timeline.plan_id], timeline, segments, vectors, beat_times
            ),
        )
        for timeline in candidates
        if timeline.plan_id in plan_by_id
    ]
    scored.sort(key=lambda pair: pair[1].overall, reverse=True)
    return scored
