"""Round-trip and invariant-free structural behaviour of the domain models."""

from __future__ import annotations

import pytest

from aive.specs import (
    SPEC_VERSION,
    EditOperation,
    EditPlanSpec,
    EditScope,
    ProjectVersion,
    TimelineSpec,
    route_for,
)


def test_plan_roundtrip(plan: EditPlanSpec) -> None:
    restored = EditPlanSpec.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert restored.spec_version == SPEC_VERSION


def test_timeline_roundtrip(timeline: TimelineSpec) -> None:
    restored = TimelineSpec.model_validate_json(timeline.model_dump_json())
    assert restored == timeline
    # The discriminated track union must survive serialisation.
    assert len(restored.clips) == 3
    assert restored.subtitle_track.clips == []


def test_unknown_field_is_rejected() -> None:
    """A typo must fail at parse time, not silently produce a broken spec."""
    with pytest.raises(Exception):
        TimelineSpec.model_validate(
            {"project_id": "p", "duration": 1.0, "nonsense_field": 1}
        )


def test_slot_duration_range_defaults(plan: EditPlanSpec) -> None:
    for slot in plan.slots:
        low, high = slot.duration_range
        assert low < slot.target_duration < high


def test_normalise_durations_hits_the_target(plan: EditPlanSpec) -> None:
    plan.intent.target_duration = 30.0
    plan.normalise_durations()
    assert plan.planned_duration() == pytest.approx(30.0, abs=0.05)
    # The pattern must be rewritten in lockstep or reference fidelity drifts.
    assert plan.rhythm.shot_duration_pattern == [s.target_duration for s in plan.slots]


def test_operation_routing_is_static() -> None:
    assert route_for("trim_clip") == "fast"
    assert route_for("remove_subtitles") == "fast"
    assert route_for("change_rhythm") == "deep"
    assert route_for("regenerate_section") == "deep"
    assert EditOperation(operation="delete_clip").needs_rematch is True
    assert EditOperation(operation="set_speed").needs_rematch is False


def test_version_carries_snapshots_not_deltas() -> None:
    version = ProjectVersion(
        project_id="p",
        parent_version_id="v3",
        user_instruction="make the ending warmer",
        operations=[
            EditOperation(
                operation="regenerate_section",
                params={"section_id": "ending"},
                scope=EditScope(type="section", section_ids=["ending"]),
            )
        ],
        plan_snapshot_id="plan_012",
        timeline_snapshot_id="tl_031",
    )
    restored = ProjectVersion.model_validate_json(version.model_dump_json())
    assert restored.plan_snapshot_id == "plan_012"
    assert restored.operations[0].route == "deep"
