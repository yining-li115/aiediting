"""Precedence tests.

The rule under test is fixed: user > locked > project state > reference >
default, applied per field rather than per source. Most of these exist because
the failure they guard against is silent - a preference that loses to the wrong
layer still produces a perfectly valid plan, just not the one that was asked
for.
"""

from __future__ import annotations

import pytest

from aive.planner.merge import Layer, PRECEDENCE, merge
from aive.specs.edit_plan import Intent
from aive.specs.reference import DurationStats, StylePrior


def prior(**overrides) -> StylePrior:
    base = dict(
        prior_id="sp_test",
        source_reference_ids=["r1", "r2"],
        consistency=0.9,
        pace="fast",
        shot_duration=DurationStats(mean=0.8, median=0.75, p10=0.5, p90=1.2),
        shot_scale_distribution={"close_up": 0.5, "medium": 0.3, "wide": 0.2},
        shot_scale_bigrams={"close_up->medium": 0.4, "medium->wide": 0.3},
        transition_density=DurationStats(mean=0.45),
        subtitle_density="high",
        beat_sync_strength=DurationStats(mean=0.9),
        style_tags=["energetic"],
    )
    base.update(overrides)
    return StylePrior(**base)


def test_precedence_order_is_the_documented_one() -> None:
    assert PRECEDENCE == (
        Layer.USER,
        Layer.LOCKED,
        Layer.STATE,
        Layer.REFERENCE,
        Layer.DEFAULT,
    )


def test_reference_decides_what_the_user_did_not_mention() -> None:
    c = merge(Intent(), prior())
    assert c.pace == "fast"
    assert c.subtitle_density == "high"
    assert c.transition_density == pytest.approx(0.45)
    assert c.decided_by["pace"] == "reference"


def test_explicit_user_instruction_beats_the_reference() -> None:
    intent = Intent()
    intent.rhythm.overall_pace = "slow"
    c = merge(intent, prior())
    assert c.pace == "slow"
    assert c.decided_by["pace"] == "user"


def test_a_relative_instruction_shifts_the_reference_instead_of_replacing_it() -> None:
    """"像这条剪，但慢一点" - one rung down from fast, not absolute slow.

    Replacing the reference outright would discard the answer the user was
    explicitly asking to keep.
    """
    intent = Intent(user_constraints=["slow_pace"])
    c = merge(intent, prior())
    assert c.pace == "medium_fast"
    assert c.decided_by["pace"] == "reference+slow_pace"


def test_overriding_one_field_keeps_the_reference_on_every_other() -> None:
    """The merge is per-field. Losing on pace must not cost the grammar."""
    intent = Intent(user_constraints=["no_subtitles", "slow_pace"])
    c = merge(intent, prior())
    assert c.subtitle_density == "none"
    assert c.pace == "medium_fast"
    # The grammar is still the reference's, untouched.
    assert c.shot_scale_bigrams == {"close_up->medium": 0.4, "medium->wide": 0.3}
    assert c.shot_scale_distribution == {"close_up": 0.5, "medium": 0.3, "wide": 0.2}
    assert c.music_sync_strength == pytest.approx(0.9)
    # Shot length moved, because "慢一点" is about exactly that.
    assert c.shot_duration is not None and c.shot_duration.median > 0.75


def test_the_reference_never_decides_the_length() -> None:
    """A 12-second reference must not shorten a 45-second request."""
    c = merge(Intent(target_duration=45.0, duration_stated=True), prior())
    assert c.target_duration == 45.0
    assert c.decided_by["target_duration"] == "user"


def test_an_unstated_duration_is_not_treated_as_a_user_decision() -> None:
    c = merge(Intent(), prior())
    assert c.decided_by["target_duration"] == "default"


def test_an_inconsistent_prior_is_refused_and_reported() -> None:
    """References that disagree average into a style describing none of them."""
    c = merge(Intent(), prior(consistency=0.1))
    assert c.pace == "medium"
    assert c.decided_by["pace"] == "default"
    assert c.shot_scale_bigrams == {}
    assert c.prior_rejected and "consistency" in c.prior_rejected


def test_no_prior_at_all_falls_through_to_defaults() -> None:
    c = merge(Intent(), None)
    assert c.pace == "medium"
    assert c.shot_scale_distribution == {}
    assert c.prior_rejected is None


def test_unknown_subtitle_density_is_not_read_as_none() -> None:
    """"nobody ran OCR" and "the reference has no captions" are different."""
    c = merge(Intent(), prior(subtitle_density="unknown"))
    assert c.decided_by["subtitle_density"] == "default"


def test_every_constraint_in_the_vocabulary_changes_something() -> None:
    """Walks the whole vocabulary so a constraint cannot be accepted and then
    quietly ignored - which has happened before."""
    from aive.specs.enums import UserConstraint
    from typing import get_args

    baseline = merge(Intent(), prior())
    # keep_opening is honoured by the agent layer, which does not exist yet.
    handled_elsewhere = {"keep_opening"}

    for constraint in get_args(UserConstraint):
        if constraint in handled_elsewhere:
            continue
        c = merge(Intent(user_constraints=[constraint]), prior())
        changed = c.model_dump(exclude={"decided_by"}) != baseline.model_dump(
            exclude={"decided_by"}
        )
        assert changed, f"{constraint} was accepted and then ignored"
        assert any(
            constraint in v for v in c.decided_by.values()
        ), f"{constraint} changed the plan without recording that it did"


def test_a_pace_instruction_at_the_ladder_ceiling_still_changes_something() -> None:
    """"快一点" against an already-fast reference cannot move the label.

    Four rungs is the whole ladder, so the instruction has to land on the
    measured shot length instead - otherwise the user asks for a change and
    silently gets none.
    """
    reference = prior(pace="fast")
    c = merge(Intent(user_constraints=["fast_pace"]), reference)
    assert c.pace == "fast"  # already at the top
    assert c.pace_shift == 1
    assert c.shot_duration is not None
    assert c.shot_duration.median < reference.shot_duration.median
    assert c.decided_by["pace"] == "reference+fast_pace"


def test_locked_values_beat_state_and_reference_but_not_the_user() -> None:
    intent = Intent()
    intent.rhythm.overall_pace = "slow"
    c = merge(intent, prior(), state={"pace": "medium"}, locked={"pace": "fast"})
    assert c.pace == "slow"

    c = merge(Intent(), prior(), state={"pace": "medium"}, locked={"pace": "fast"})
    assert c.pace == "fast"
    assert c.decided_by["pace"] == "locked"

    c = merge(Intent(), prior(), state={"pace": "medium"})
    assert c.pace == "medium"
    assert c.decided_by["pace"] == "state"


def test_style_is_unioned_rather_than_resolved() -> None:
    """The user's words and the reference's tags describe different axes."""
    c = merge(Intent(style=["relaxed"]), prior())
    assert c.style == ["energetic", "relaxed"]
