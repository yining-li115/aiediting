"""Scoring, assignment, adaptation and timeline construction."""

from __future__ import annotations

import numpy as np
import pytest

from aive.critic import score as structural_score
from aive.matcher import assign, build_timeline, fit, score_pair
from aive.matcher.affinity import role_affinity, scale_affinity
from aive.matcher.assign import MAX_USES_PER_SOURCE
from aive.specs.edit_plan import EditPlanSpec, Intent, Slot
from aive.specs.segment import MaterialSegment
from aive.specs.validate import validate_timeline


def make_segment(
    index: int,
    role: str = "detail",
    shot: str = "close_up",
    duration: float = 4.0,
    quality: float = 0.8,
    source: str | None = None,
) -> MaterialSegment:
    return MaterialSegment(
        segment_id=f"seg_{index:03d}",
        project_id="p",
        source_id=source or f"asset_{index:03d}",
        start=0.0,
        end=duration,
        semantic_role=role,
        shot_type=shot,
        camera_motion="static",
        technical_quality=quality,
        emotion=["warm"],
    )


def make_plan(slot_specs: list[tuple[str, str, float]]) -> EditPlanSpec:
    slots = [
        Slot(
            slot_id=f"slot_{i + 1:02d}",
            order=i,
            semantic_role=role,
            shot_type=shot,
            target_duration=duration,
            required=i == 0,
        )
        for i, (role, shot, duration) in enumerate(slot_specs)
    ]
    plan = EditPlanSpec(
        project_id="p",
        intent=Intent(target_duration=sum(d for _, _, d in slot_specs)),
        slots=slots,
    )
    plan.rhythm.shot_duration_pattern = [s.target_duration for s in slots]
    return plan


# --- affinity --------------------------------------------------------------


def test_role_substitution_is_asymmetric() -> None:
    """A detail shot stands in for food better than the reverse."""
    assert role_affinity("food", "food") == 1.0
    assert role_affinity("food", "detail") > role_affinity("detail", "food")
    assert role_affinity("food", "opening_environment") == 0.0


def test_scale_ladder_prefers_adjacent_rungs() -> None:
    assert scale_affinity("wide", "wide") == 1.0
    assert scale_affinity("wide", "medium") > scale_affinity("wide", "close_up")
    assert scale_affinity("wide", "extreme_close_up") < 0.2


# --- scoring ---------------------------------------------------------------


def test_exact_match_beats_a_substitute() -> None:
    slot = Slot(slot_id="s", order=0, semantic_role="food",
                shot_type="close_up", target_duration=2.0)
    exact = make_segment(1, role="food", shot="close_up")
    near = make_segment(2, role="detail", shot="medium")
    intent = Intent()
    assert score_pair(slot, exact, intent).total > score_pair(slot, near, intent).total


def test_quality_breaks_ties() -> None:
    slot = Slot(slot_id="s", order=0, semantic_role="food",
                shot_type="close_up", target_duration=2.0)
    good = make_segment(1, role="food", quality=0.9)
    poor = make_segment(2, role="food", quality=0.4)
    intent = Intent()
    assert score_pair(slot, good, intent).total > score_pair(slot, poor, intent).total


def test_embedding_similarity_contributes() -> None:
    slot = Slot(slot_id="s", order=0, semantic_role="detail",
                shot_type="close_up", target_duration=2.0)
    segment = make_segment(1)
    same = np.ones(8, dtype=np.float32) / np.sqrt(8)
    opposite = -same
    intent = Intent()
    aligned = score_pair(slot, segment, intent, same, same).total
    misaligned = score_pair(slot, segment, intent, same, opposite).total
    assert aligned > misaligned


# --- assignment ------------------------------------------------------------


def test_no_source_appears_twice_in_a_row() -> None:
    """Consecutive shots from one file is the loudest machine-cut signature."""
    plan = make_plan([("detail", "close_up", 2.0)] * 6)
    segments = [make_segment(i, source="asset_shared") for i in range(6)]
    segments += [make_segment(10 + i) for i in range(6)]
    timeline = build_timeline(plan, segments)
    sources = [c.source_id for c in timeline.clips]
    assert all(a != b for a, b in zip(sources, sources[1:]))


def test_source_reuse_is_capped() -> None:
    plan = make_plan([("detail", "close_up", 2.0)] * 10)
    segments = [make_segment(i, source=f"asset_{i % 3:03d}") for i in range(12)]
    timeline = build_timeline(plan, segments)
    counts: dict[str, int] = {}
    for clip in timeline.clips:
        counts[clip.source_id] = counts.get(clip.source_id, 0) + 1
    assert max(counts.values()) <= MAX_USES_PER_SOURCE


def test_variety_is_preferred_over_reusing_the_best_clip() -> None:
    """With plenty of footage, reuse must be a fallback rather than a habit.

    Without a reuse penalty the matcher fills every slot from its few
    highest-scoring sources and leaves untouched footage unused.
    """
    plan = make_plan([("detail", "close_up", 2.0)] * 10)
    # One slightly better source, plus plenty of adequate ones.
    segments = [make_segment(0, quality=0.95)]
    segments += [make_segment(i, quality=0.8) for i in range(1, 15)]
    timeline = build_timeline(plan, segments)
    distinct = len({c.source_id for c in timeline.clips})
    assert distinct >= 8, f"only {distinct} distinct sources across 10 clips"


def test_near_identical_neighbours_are_rejected() -> None:
    plan = make_plan([("detail", "close_up", 2.0)] * 4)
    segments = [make_segment(i) for i in range(6)]
    identical = np.ones(8, dtype=np.float32) / np.sqrt(8)
    distinct = np.zeros((6, 8), dtype=np.float32)
    distinct[:, 0] = 1.0
    vectors = {"seg_000": identical, "seg_001": identical}
    for i in range(2, 6):
        vec = np.zeros(8, dtype=np.float32)
        vec[i] = 1.0
        vectors[f"seg_{i:03d}"] = vec
    timeline = build_timeline(plan, segments, vectors)
    used = [c.source_id for c in timeline.clips]
    assert not (used[0] == "asset_000" and used[1] == "asset_001")


def test_unfillable_optional_slot_is_skipped_and_logged() -> None:
    plan = make_plan([("detail", "close_up", 2.0), ("food", "close_up", 2.0)])
    # Only a wide landscape: nothing can plausibly serve the food slot.
    segments = [make_segment(1, role="environment", shot="extreme_wide")]
    timeline = build_timeline(plan, segments)
    skipped = timeline.skipped_slot_ids()
    assert skipped
    assert all(r.detail for r in timeline.adaptation_log if r.action == "skip")


# --- adaptation ------------------------------------------------------------


def test_long_segment_is_trimmed_from_the_middle() -> None:
    """Shot edges are where the transition artefacts live."""
    slot = Slot(slot_id="s", order=0, target_duration=2.0)
    segment = make_segment(1, duration=10.0)
    fitted = fit(slot, segment)
    assert fitted.speed == 1.0
    assert fitted.timeline_duration == pytest.approx(2.0)
    assert fitted.source_in > segment.start
    assert fitted.source_out < segment.end


def test_short_segment_gets_the_slot_shortened_before_speed() -> None:
    slot = Slot(slot_id="s", order=0, target_duration=3.0, duration_range=(1.5, 4.0))
    segment = make_segment(1, duration=2.0)
    fitted = fit(slot, segment)
    assert fitted.speed == 1.0  # shortening is less visible than a speed change
    assert fitted.timeline_duration == pytest.approx(2.0)
    assert any(r.action == "shorten" for r in fitted.records)


def test_very_short_segment_uses_speed() -> None:
    slot = Slot(slot_id="s", order=0, target_duration=2.0, duration_range=(2.0, 2.5))
    segment = make_segment(1, duration=1.2)
    fitted = fit(slot, segment)
    assert fitted.speed < 1.0
    assert any(r.action == "speed" for r in fitted.records)


def test_every_non_trivial_adaptation_is_logged() -> None:
    plan = make_plan([("detail", "close_up", 1.5)] * 4)
    segments = [make_segment(i, duration=6.0) for i in range(4)]
    timeline = build_timeline(plan, segments)
    assert timeline.adaptation_log
    assert all(r.slot_id and r.action for r in timeline.adaptation_log)


# --- output validity -------------------------------------------------------


def test_built_timeline_passes_every_hard_invariant() -> None:
    plan = make_plan(
        [("opening_environment", "wide", 2.0), ("detail", "close_up", 1.5),
         ("food", "close_up", 1.5), ("environment", "medium", 2.0)]
    )
    segments = [
        make_segment(1, role="opening_environment", shot="wide", duration=5.0),
        make_segment(2, role="detail", shot="close_up", duration=4.0),
        make_segment(3, role="food", shot="close_up", duration=3.0),
        make_segment(4, role="environment", shot="medium", duration=6.0),
    ]
    timeline = build_timeline(plan, segments)
    hard = [v for v in validate_timeline(timeline, plan) if v.severity == "hard"]
    assert hard == [], [v.message for v in hard]


def test_timeline_is_contiguous_after_rounding() -> None:
    """Per-clip rounding must not accumulate into gaps."""
    plan = make_plan([("detail", "close_up", 1.333)] * 12)
    segments = [make_segment(i, duration=5.0) for i in range(12)]
    timeline = build_timeline(plan, segments)
    for previous, current in zip(timeline.clips, timeline.clips[1:]):
        assert previous.timeline_end == pytest.approx(current.timeline_start, abs=1e-6)
    assert timeline.clips[-1].timeline_end == pytest.approx(timeline.duration)


# --- structural critic -----------------------------------------------------


def test_hard_violations_zero_the_score() -> None:
    """A structurally broken timeline must never win a comparison."""
    plan = make_plan([("detail", "close_up", 2.0)] * 3)
    segments = [make_segment(i, duration=5.0) for i in range(3)]
    timeline = build_timeline(plan, segments)
    assert structural_score(plan, timeline, segments).overall > 0

    timeline.clips[0].source_out += 3.0  # breaks the speed invariant
    assert structural_score(plan, timeline, segments).overall == 0.0


def test_repetition_is_penalised() -> None:
    plan = make_plan([("detail", "close_up", 2.0)] * 4)
    segments = [make_segment(i, duration=5.0) for i in range(4)]
    timeline = build_timeline(plan, segments)
    clean = structural_score(plan, timeline, segments)

    for clip in timeline.clips:
        clip.source_id = "asset_000"
    repeated = structural_score(plan, timeline, segments)
    assert repeated.dimensions["repetition_penalty"] < clean.dimensions[
        "repetition_penalty"
    ]
    assert repeated.overall < clean.overall
