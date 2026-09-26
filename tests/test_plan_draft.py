"""The contract between a model-written plan and the rest of the pipeline.

The model decides what is good; this layer decides what is valid. Every test
here is a guarantee the matcher relies on and the prompt only requests.
"""

from __future__ import annotations

import pytest

from aive.planner.draft import (
    MAX_SLOT,
    MIN_SLOT,
    DraftRejected,
    PlanDraft,
    SectionDraft,
    SlotDraft,
    to_plan,
)
from aive.planner.merge import Conditioning
from aive.planner.perturb import derive
from aive.specs.edit_plan import Intent
from aive.specs.validate import validate_timeline  # noqa: F401  (import guard)

POOL = {
    "total_usable_duration": 60.0,
    "by_semantic_role": {
        "environment": {"count": 6, "total_duration": 30.0},
        "person_activity": {"count": 4, "total_duration": 20.0},
        "detail": {"count": 2, "total_duration": 10.0},
    },
}


def slot(order, section="body", role="environment", shot="wide", duration=1.5, **kw):
    return SlotDraft(
        order=order,
        section=section,
        semantic_role=role,
        shot_type=shot,
        target_duration=duration,
        energy=0.5,
        **kw,
    )


def draft(slots=None, **kw):
    slots = slots or [slot(i) for i in range(8)]
    base = dict(
        narrative_arc="opening -> body -> ending",
        sections=[SectionDraft(name="body", target_duration=12.0)],
        slots=slots,
        pace="medium",
        transition_density=0.1,
        music_sync_strength=0.5,
    )
    base.update(kw)
    return PlanDraft(**base)


def build(d=None, conditioning=None, pool=POOL):
    return to_plan(
        d or draft(),
        project_id="p1",
        intent=Intent(target_duration=45.0, duration_stated=True),
        conditioning=conditioning or Conditioning(target_duration=45.0),
        pool_summary=pool,
    )


def test_the_plan_is_forced_onto_the_target_duration() -> None:
    """Arithmetic is never the model's job."""
    plan = build(draft(slots=[slot(i, duration=0.9) for i in range(8)]))
    assert plan.planned_duration() == pytest.approx(45.0, abs=0.05)


def test_absurd_slot_lengths_are_clamped_and_recorded() -> None:
    plan = build(draft(slots=[slot(0, duration=25.0), slot(1), slot(2)]))
    assert all(s.duration_range for s in plan.slots)
    repairs = plan.provenance.extra["repairs"]
    assert any("clamped" in r for r in repairs)


def test_duplicate_and_gapped_order_values_are_renumbered() -> None:
    """Slot order is what the timeline is built from; models emit duplicates."""
    plan = build(draft(slots=[slot(5), slot(5), slot(99), slot(0)]))
    assert [s.order for s in plan.slots] == [0, 1, 2, 3]
    assert len({s.slot_id for s in plan.slots}) == 4


def test_a_plan_with_too_few_slots_is_rejected_not_shipped() -> None:
    with pytest.raises(DraftRejected):
        build(draft(slots=[slot(0)]))


def test_slots_the_pool_cannot_fill_are_demoted_not_deleted() -> None:
    """Deleting would change the length and hide the mismatch."""
    d = draft(slots=[slot(0), slot(1, role="food"), slot(2, role="food"), slot(3)])
    plan = build(d)
    assert len(plan.slots) == 4
    food = [s for s in plan.slots if s.semantic_role == "food"]
    assert food and all(not s.required for s in food)
    assert any("food" in r for r in plan.provenance.extra["repairs"])


def test_transitions_are_removed_when_none_were_allowed() -> None:
    """The model is overruled here rather than argued with in the prompt."""
    d = draft(slots=[slot(i, transition_out="dissolve") for i in range(5)])
    plan = build(d, conditioning=Conditioning(target_duration=45.0, transition_density=0.0))
    assert all(s.transition_out == "hard_cut" for s in plan.slots)
    assert any("transitions removed" in r for r in plan.provenance.extra["repairs"])


def test_sections_are_rebuilt_from_the_slots_that_survived() -> None:
    d = draft(
        slots=[slot(0, section="opening"), slot(1, section="body"), slot(2, section="body")],
        sections=[
            SectionDraft(name="opening", target_duration=99.0),
            SectionDraft(name="ghost", target_duration=5.0),
        ],
    )
    plan = build(d)
    names = [s.name for s in plan.story.sections]
    assert names == ["opening", "body"]  # "ghost" had no slots
    for section in plan.story.sections:
        members = [s for s in plan.slots if s.slot_id in section.slot_ids]
        assert section.target_duration == pytest.approx(
            sum(s.target_duration for s in members), abs=0.01
        )


def test_the_merged_decisions_travel_with_the_plan() -> None:
    """"why is this 1.2 seconds" has to be answerable afterwards."""
    conditioning = Conditioning(target_duration=45.0)
    conditioning.decided_by = {"pace": "reference+slow_pace"}
    plan = build(conditioning=conditioning)
    assert plan.provenance.extra["decided_by"]["pace"] == "reference+slow_pace"
    assert plan.provenance.extra["source"] == "model"


def test_derived_candidates_differ_structurally_and_still_hit_the_target() -> None:
    base = build()
    others = derive(base, 3, seed=7)
    assert len(others) == 3
    for candidate in others:
        assert candidate.planned_duration() == pytest.approx(45.0, abs=0.05)
        assert candidate.plan_id != base.plan_id
        assert candidate.provenance.extra["derived_from"] == base.plan_id
    # A faster candidate of the same running time must be MORE shots, not the
    # same shots scaled - scaling then normalising cancels exactly.
    counts = {len(c.slots) for c in [base, *others]}
    assert len(counts) > 1, "pace variants collapsed into the baseline"
    shapes = {
        tuple(round(s.target_duration, 2) for s in c.slots)
        for c in [base, *others]
    }
    assert len(shapes) == 4, "candidates must differ by construction"


def test_every_derived_candidate_stays_internally_consistent() -> None:
    """Rotation and resampling both rewrite the slot list.

    Slot count is allowed to change - that is what a pace variant IS - but a
    section must never point at a shot that no longer exists, and the section
    durations must still add up to the shots inside them.
    """
    base = build(
        draft(
            slots=[
                slot(0, section="opening"),
                slot(1, section="a"),
                slot(2, section="b"),
                slot(3, section="ending"),
            ]
        )
    )
    for candidate in derive(base, 5, seed=1):
        ids = {s.slot_id for s in candidate.slots}
        assert len(ids) == len(candidate.slots), "duplicate slot ids"
        assert [s.order for s in candidate.slots] == list(range(len(candidate.slots)))

        listed = [i for section in candidate.story.sections for i in section.slot_ids]
        assert set(listed) <= ids, "a section points at a shot that is gone"
        assert len(listed) == len(candidate.slots), "a shot belongs to no section"
        for section in candidate.story.sections:
            members = [s for s in candidate.slots if s.slot_id in section.slot_ids]
            assert section.target_duration == pytest.approx(
                sum(s.target_duration for s in members), abs=0.01
            )


def test_variants_never_reinstate_a_constraint_the_user_ruled_out() -> None:
    """"不要转场" must survive into every candidate, not just the first.

    The variant table states transition densities absolutely, as if the
    baseline were the house default. Assigning those numbers would hand an
    explicit user instruction back to a perturbation table.
    """
    conditioning = Conditioning(target_duration=45.0, transition_density=0.0)
    base = build(conditioning=conditioning)
    for candidate in [base, *derive(base, 5, seed=3)]:
        assert candidate.rhythm.transition_density == 0.0
        assert all(s.transition_out == "hard_cut" for s in candidate.slots)


def test_variants_scale_a_heavy_reference_rather_than_flattening_it() -> None:
    conditioning = Conditioning(target_duration=45.0, transition_density=0.45)
    base = build(conditioning=conditioning)
    densities = [c.rhythm.transition_density for c in derive(base, 5, seed=3)]
    assert all(d > 0.1 for d in densities), "the reference's style was flattened"
    assert len(set(densities)) > 1, "variants stopped varying"


def test_duplicate_candidates_are_not_shipped() -> None:
    """A variant that is a no-op on this plan costs a render and teaches
    nothing."""
    single_section = draft(slots=[slot(i, section="body") for i in range(6)])
    base = build(single_section)
    candidates = derive(base, 5, seed=3)
    marks = {
        tuple((s.section, s.shot_type, round(s.target_duration, 2)) for s in c.slots)
        + (c.rhythm.transition_density, c.rhythm.music_sync_strength)
        for c in [base, *candidates]
    }
    assert len(marks) == len(candidates) + 1


def test_the_pace_label_describes_the_plan_rather_than_the_request() -> None:
    """Normalisation moves every shot; a stale label reads as a fact."""
    long_shots = draft(slots=[slot(i, duration=3.0) for i in range(6)], pace="fast")
    plan = build(long_shots)
    assert plan.rhythm.pace == "slow"  # 45s over 6 shots is not fast


def test_a_resampled_variant_relabels_its_pace() -> None:
    base = build(draft(slots=[slot(i, duration=1.5) for i in range(10)]))
    for candidate in derive(base, 5, seed=11):
        durations = sorted(s.target_duration for s in candidate.slots)
        middle = durations[len(durations) // 2]
        expected = (
            "fast" if middle <= 0.9
            else "medium_fast" if middle <= 1.3
            else "medium" if middle <= 2.0
            else "slow"
        )
        assert candidate.rhythm.pace == expected, (
            f"{candidate.provenance.extra['variant']} has {len(candidate.slots)} "
            f"shots of ~{middle:.2f}s but calls itself {candidate.rhythm.pace}"
        )


def test_the_plan_uses_the_merged_length_not_the_parsed_one() -> None:
    """The merge resolves the length; the arithmetic must read that answer.

    normalise_durations() reads intent.target_duration, so a plan built with
    the parsed intent silently ignores a reference-decided duration.
    """
    conditioning = Conditioning(target_duration=23.8)
    plan = to_plan(
        draft(),
        project_id="p1",
        intent=Intent(target_duration=45.0, duration_stated=False),
        conditioning=conditioning,
        pool_summary=POOL,
    )
    assert plan.planned_duration() == pytest.approx(23.8, abs=0.05)
    assert plan.intent.target_duration == pytest.approx(23.8)
