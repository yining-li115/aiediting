"""Intent parsing and planning."""

from __future__ import annotations

import pytest

from aive.planner import PlanningContext, generate_plans, parse_intent_rules
from aive.planner.intent import parse_duration
from aive.specs.edit_plan import Intent


@pytest.fixture
def pool_summary() -> dict:
    return {
        "total_usable_duration": 180.0,
        "by_semantic_role": {
            "opening_environment": {"count": 4, "total_duration": 16.0},
            "environment": {"count": 8, "total_duration": 40.0},
            "person_activity": {"count": 6, "total_duration": 30.0},
            "detail": {"count": 10, "total_duration": 45.0},
            "food": {"count": 5, "total_duration": 25.0},
            "ending_environment": {"count": 3, "total_duration": 12.0},
        },
    }


# --- intent ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("帮我剪一个45秒的vlog", 45.0),
        ("make it 30 seconds", 30.0),
        ("1分30秒", 90.0),
        ("2分钟", 90.0),  # clamped to the supported maximum
        ("5秒", 20.0),  # clamped to the supported minimum
        ("没有时长", None),
    ],
)
def test_duration_extraction(text, expected) -> None:
    intent = parse_intent_rules(text)
    if expected is None:
        assert intent.target_duration == 45.0  # default
    else:
        assert intent.target_duration == expected


def test_styles_and_constraints_are_extracted() -> None:
    intent = parse_intent_rules(
        "帮我剪一个45秒的东京旅行vlog，轻松、有氛围感，不要太多转场"
    )
    assert "travel" in intent.style
    assert "relaxed" in intent.style
    assert "few_transitions" in intent.user_constraints


def test_empty_prompt_still_produces_a_usable_intent() -> None:
    """An empty prompt must not yield an empty plan."""
    intent = parse_intent_rules(None)
    assert intent.target_duration == 45.0
    assert intent.style == ["travel"]


def test_explicit_duration_overrides_the_prompt() -> None:
    intent = parse_intent_rules("剪一个60秒的", target_duration=30.0)
    assert intent.target_duration == 30.0


def test_parse_duration_returns_none_without_a_number() -> None:
    assert parse_duration("make it short") is None


# --- planning -------------------------------------------------------------


def test_every_plan_hits_the_target_duration(pool_summary) -> None:
    """Length arithmetic is the code's job, never the model's."""
    intent = Intent(target_duration=45.0)
    plans = generate_plans(PlanningContext("p", intent, pool_summary), 6)
    for plan in plans:
        assert plan.planned_duration() == pytest.approx(45.0, abs=0.1)
        assert plan.rhythm.shot_duration_pattern == [
            s.target_duration for s in plan.slots
        ]


def test_candidates_differ_structurally_not_randomly(pool_summary) -> None:
    """Best-of-N is meaningless if candidates only differ by sampling noise."""
    plans = generate_plans(PlanningContext("p", Intent(), pool_summary), 6)
    shapes = {(len(p.slots), round(p.slots[0].target_duration, 2)) for p in plans}
    assert len(shapes) >= 4
    names = {p.provenance.extra["variant"] for p in plans}
    assert len(names) == 6


def test_planning_is_deterministic(pool_summary) -> None:
    context = PlanningContext("p", Intent(), pool_summary)
    first = generate_plans(context, 4, seed=41)
    second = generate_plans(context, 4, seed=41)
    for a, b in zip(first, second):
        assert [s.target_duration for s in a.slots] == [
            s.target_duration for s in b.slots
        ]
        assert [s.semantic_role for s in a.slots] == [s.semantic_role for s in b.slots]


def test_sections_absent_from_the_pool_are_dropped() -> None:
    """A plan must not ask for footage the user does not have."""
    thin = {
        "total_usable_duration": 20.0,
        "by_semantic_role": {"detail": {"count": 5, "total_duration": 20.0}},
    }
    plan = generate_plans(PlanningContext("p", Intent(), thin), 1)[0]
    assert [s.name for s in plan.story.sections] == ["detail"]


def test_user_constraint_beats_the_variant(pool_summary) -> None:
    """'fewer transitions' must win over a variant that wants more."""
    intent = Intent(user_constraints=["few_transitions"])
    plans = generate_plans(PlanningContext("p", intent, pool_summary), 6)
    assert all(p.rhythm.transition_density <= 0.05 for p in plans)


def test_no_subtitles_constraint_reaches_the_text_block(pool_summary) -> None:
    intent = Intent(user_constraints=["no_subtitles"])
    plan = generate_plans(PlanningContext("p", intent, pool_summary), 1)[0]
    assert plan.text.subtitle_density == "none"


# --- the parser/planner contract -------------------------------------------


def test_every_constraint_in_the_vocabulary_is_handled(pool_summary) -> None:
    """A constraint the planner ignores is worse than one never extracted:
    the user sees their instruction accepted and silently disregarded."""
    from aive.specs.enums import UserConstraint

    baseline = generate_plans(PlanningContext("p", Intent(), pool_summary), 1)[0]

    unhandled = []
    for constraint in UserConstraint.__args__:
        intent = Intent(user_constraints=[constraint])
        plan = generate_plans(PlanningContext("p", intent, pool_summary), 1)[0]
        changed = (
            plan.rhythm.transition_density != baseline.rhythm.transition_density
            or len(plan.slots) != len(baseline.slots)
            or plan.text.subtitle_density != baseline.text.subtitle_density
            or plan.audio.keep_original_sound != baseline.audio.keep_original_sound
            or plan.audio.use_music != baseline.audio.use_music
        )
        # keep_opening only means anything when re-editing, so the planner is
        # not where it takes effect.
        if not changed and constraint != "keep_opening":
            unhandled.append(constraint)

    assert not unhandled, f"constraints accepted but ignored: {unhandled}"


def test_rule_fallback_emits_canonical_values() -> None:
    """Both parsers must speak the same vocabulary or behaviour depends on
    which one happened to run."""
    from aive.specs.enums import UserConstraint

    allowed = set(UserConstraint.__args__)
    prompts = [
        "不要太多转场", "别加字幕", "慢一点", "快一点",
        "保留原声", "不要音乐", "别切太碎", "fewer transitions",
    ]
    for prompt in prompts:
        intent = parse_intent_rules(prompt)
        assert intent.user_constraints, f"nothing extracted from {prompt!r}"
        for constraint in intent.user_constraints:
            assert constraint in allowed, f"{constraint!r} is not in the vocabulary"


def test_keep_original_sound_reaches_the_audio_plan(pool_summary) -> None:
    intent = Intent(user_constraints=["keep_original_sound"])
    plan = generate_plans(PlanningContext("p", intent, pool_summary), 1)[0]
    assert plan.audio.keep_original_sound is True
