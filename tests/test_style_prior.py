"""B1: N reference videos become one transferable style prior.

The prior is what conditions the planner when the user supplies style
references. It must contain no pixels and no content - only how the cutting
behaved - because that is both what transfers to different footage and what
keeps the reference's own material out of the output (section 16.1).
"""

from __future__ import annotations

import pytest

from aive.reference.aggregate import build_prior, duration_stats, merge_distributions
from aive.specs.reference import (
    MIN_CONSISTENCY,
    ReferenceAnalysis,
    ReferenceMusic,
    ReferenceShot,
)


def reference(rid, durations, scales, roles, density=0.05, sync=0.8, subtitle="medium"):
    shots = [
        ReferenceShot(
            index=i,
            start=sum(durations[:i]),
            end=sum(durations[: i + 1]),
            shot_type=scales[i % len(scales)],
            semantic_role=roles[i % len(roles)],
        )
        for i in range(len(durations))
    ]
    analysis = ReferenceAnalysis(
        reference_id=rid,
        duration=sum(durations),
        shot_count=len(shots),
        shots=shots,
        shot_duration=duration_stats(durations),
        transition_density=density,
        music=ReferenceMusic(beat_sync_strength=sync),
        shot_scale_sequence=[s.shot_type for s in shots],
        semantic_structure=[s.semantic_role for s in shots],
        style_tags=["travel"],
    )
    analysis.text.subtitle_density = subtitle
    return analysis


CONSISTENT = [
    reference("r1", [1.0, 1.2, 0.9, 1.1], ["wide", "medium", "close_up"], ["environment", "person_activity"]),
    reference("r2", [1.1, 0.9, 1.0, 1.2], ["wide", "medium", "close_up"], ["environment", "person_activity"]),
    reference("r3", [0.95, 1.15, 1.05, 1.0], ["wide", "medium", "close_up"], ["environment", "person_activity"]),
]


def test_the_prior_carries_no_content() -> None:
    """Nothing that could reproduce the reference may survive aggregation."""
    prior = build_prior(CONSISTENT)
    payload = prior.model_dump()
    forbidden = {"shots", "semantic", "keyframes", "storage_key", "frames"}
    assert not (forbidden & set(payload)), "a content field leaked into the prior"
    assert "wide" in prior.shot_scale_distribution  # grammar, not content


def test_agreeing_references_produce_a_usable_prior() -> None:
    prior = build_prior(CONSISTENT, project_id="p1")
    assert prior.consistency >= MIN_CONSISTENCY
    assert prior.is_usable()
    assert prior.source_reference_ids == ["r1", "r2", "r3"]
    assert prior.pace == "medium_fast"


def test_references_that_disagree_are_flagged_rather_than_averaged() -> None:
    """A 0.35s MV and a 3.6s travel film average to a pace neither one has."""
    mixed = [
        reference("m1", [0.4, 0.3, 0.35, 0.4], ["close_up", "extreme_close_up"], ["detail"], density=0.6),
        reference("m2", [3.5, 4.0, 3.2, 3.8], ["wide"], ["environment"], density=0.0),
    ]
    prior = build_prior(mixed)
    assert prior.consistency < MIN_CONSISTENCY
    assert not prior.is_usable()


def test_a_single_reference_is_usable() -> None:
    """One video cannot disagree with itself, and refusing it would make the
    commonest case - one reference - unusable."""
    prior = build_prior([CONSISTENT[0]])
    assert prior.is_usable()


def test_no_references_is_not_an_error() -> None:
    prior = build_prior([])
    assert not prior.is_usable()
    assert prior.shot_scale_distribution == {}


def test_bigrams_survive_aggregation_across_different_lengths() -> None:
    """Sequences of different lengths cannot be averaged; transitions can."""
    prior = build_prior(CONSISTENT)
    assert prior.shot_scale_bigrams
    assert pytest.approx(sum(prior.shot_scale_bigrams.values()), abs=0.01) == 1.0


def test_a_long_reference_does_not_outvote_the_others() -> None:
    """Distribution weighting is per reference, not per shot: otherwise one
    long video decides the style on its own."""
    long_one = reference("long", [1.0] * 40, ["close_up"], ["detail"])
    short_ones = [
        reference("s1", [1.0] * 4, ["wide"], ["environment"]),
        reference("s2", [1.0] * 4, ["wide"], ["environment"]),
    ]
    prior = build_prior([long_one, *short_ones])
    assert prior.shot_scale_distribution["wide"] > prior.shot_scale_distribution["close_up"]


def test_merge_distributions_renormalises() -> None:
    merged = merge_distributions([{"a": 1.0}, {"b": 1.0}])
    assert merged == {"a": 0.5, "b": 0.5}


def test_unknown_subtitle_density_does_not_become_a_measurement() -> None:
    """No OCR ran means unknown, and unknown must not read as "no captions"."""
    prior = build_prior([reference("r", [1.0, 1.1], ["wide"], ["environment"], subtitle="unknown")])
    assert prior.subtitle_density == "unknown"
