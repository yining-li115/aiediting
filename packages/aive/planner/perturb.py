"""Derive further candidates from a plan that already exists.

Best-of-N only measures anything if the candidates disagree about something an
editor would recognise. Re-prompting the same model n times at a higher
temperature produces plans that differ in wording and agree in structure, so
the critic ends up ranking noise.

So the model is asked once, and the other candidates are perturbations of its
answer: each changes one editing decision in a named direction. When the critic
prefers candidate 2 we learn "shorter shots suited this footage", which is a
fact about taste. That is the same argument ``variants.py`` makes for the
deterministic planner; this module applies it to a plan a model wrote.
"""

from __future__ import annotations

from ..specs.edit_plan import EditPlanSpec, Section, Slot
from ..specs.enums import ShotType
from .draft import observed_pace, resync_sections
from .variants import VARIANTS, Variant

# TODO(review): the floor on splitting a shot in two. Below this a "faster"
# variant would produce flashes rather than shots, so the resample stops early
# and the candidate is simply less fast than asked. Same judgement call as
# MIN_CLIP_DURATION, never validated on real cuts.
MIN_SPLIT = 0.5
MIN_SLOTS = 3

# What the variant table treats as "unremarkable", so its absolute numbers can
# be read as ratios against the plan's actual decision.
NEUTRAL_DENSITY = 0.10
NEUTRAL_SYNC = 0.5

ALTERNATING: tuple[ShotType, ...] = ("wide", "close_up", "medium", "extreme_close_up")


def derive(base: EditPlanSpec, count: int, seed: int | None = None) -> list[EditPlanSpec]:
    """``count`` further candidates, each one editing decision away from base."""
    if count <= 0:
        return []
    out: list[EditPlanSpec] = []
    # VARIANTS[0] is the baseline, which is what `base` already is.
    seen = {_fingerprint(base)}
    for variant in VARIANTS[1:]:
        if len(out) >= count:
            break
        candidate = _apply(base, variant, len(out) + 1, seed)
        mark = _fingerprint(candidate)
        if mark in seen:
            # Some variants are no-ops on some plans - rotating the sections of
            # a plan that has only one, for instance. A duplicate candidate
            # costs a proxy render and a critic comparison and teaches nothing,
            # so it is skipped rather than shipped.
            continue
        seen.add(mark)
        out.append(candidate)
    return out


def _fingerprint(plan: EditPlanSpec):
    """What makes two candidates the same edit, for dedup purposes."""
    return (
        tuple(
            (s.section, s.semantic_role, s.shot_type, round(s.target_duration, 2))
            for s in plan.slots
        ),
        round(plan.rhythm.transition_density, 3),
        round(plan.rhythm.music_sync_strength, 3),
    )


def _apply(
    base: EditPlanSpec, variant: Variant, index: int, seed: int | None
) -> EditPlanSpec:
    plan = base.model_copy(deep=True)
    plan.plan_id = f"{base.plan_id}_v{index}"

    # Pace changes the number of shots, not their length.
    #
    # This is the whole trap in perturbing a finished plan: the total duration
    # is fixed, so scaling every slot by 0.72 and then normalising back onto
    # the target cancels exactly, and "faster" comes out identical to the
    # baseline. In a plan of fixed length, faster means more cuts.
    if variant.pace_factor != 1.0:
        plan.slots = _resample(plan.slots, variant.pace_factor)

    if plan.slots and variant.opening_factor != 1.0:
        first = plan.slots[0]
        first.target_duration = round(first.target_duration * variant.opening_factor, 3)

    if variant.section_rotation and len(plan.story.sections) > 2:
        plan.slots, plan.story.sections = _rotate(plan, variant.section_rotation)
    else:
        plan.story.sections = resync_sections(plan)

    if variant.alternate_scale:
        for position, slot in enumerate(plan.slots):
            slot.shot_type = ALTERNATING[position % len(ALTERNATING)]

    # Relative, not absolute. The variant table states densities as if the
    # baseline were the house default, but the baseline here is whatever the
    # merge decided - and if the user said "不要转场" that is zero. Assigning
    # the variant's own number would hand their explicit instruction back to a
    # perturbation table. Scaling preserves both: zero stays zero, a heavy
    # reference stays heavy, and "rhythmic" is still the busier of the two.
    plan.rhythm.transition_density = _scaled(
        base.rhythm.transition_density, variant.transition_density, NEUTRAL_DENSITY
    )
    plan.rhythm.music_sync_strength = _scaled(
        base.rhythm.music_sync_strength, variant.music_sync_strength, NEUTRAL_SYNC
    )
    plan.rhythm.beat_alignment = (
        "soft" if plan.rhythm.music_sync_strength >= 0.6 else "none"
    )
    if plan.rhythm.transition_density <= 0.0:
        for slot in plan.slots:
            slot.transition_out = "hard_cut"

    plan.provenance.candidate_index = index
    plan.provenance.seed = None if seed is None else seed + index
    plan.provenance.extra = {
        **base.provenance.extra,
        "variant": variant.name,
        "note": variant.description,
        "derived_from": base.plan_id,
    }
    plan.normalise_durations()
    plan.story.sections = resync_sections(plan)
    plan.rhythm.pace = observed_pace(plan)
    return plan


def _scaled(base_value: float, variant_value: float, neutral: float) -> float:
    """Apply a variant's number as a ratio against the table's own neutral."""
    if neutral <= 0:
        return round(max(0.0, min(1.0, base_value)), 3)
    return round(max(0.0, min(1.0, base_value * (variant_value / neutral))), 3)


def _resample(slots: list[Slot], pace_factor: float) -> list[Slot]:
    """Change how many shots cover the same running time.

    ``pace_factor`` below 1 means shorter shots, which at a fixed total means
    more of them. Splitting the longest slot and merging the shortest adjacent
    pair are the two moves an editor would actually make, and both keep the
    section each shot belongs to.
    """
    target = max(MIN_SLOTS, round(len(slots) / pace_factor))
    working = [slot.model_copy(deep=True) for slot in slots]

    guard = 0
    while len(working) != target and guard < 200:
        guard += 1
        if len(working) < target:
            position = max(
                range(len(working)), key=lambda i: working[i].target_duration
            )
            longest = working[position]
            if longest.target_duration / 2 < MIN_SPLIT:
                break
            half = round(longest.target_duration / 2, 3)
            longest.target_duration = half
            twin = longest.model_copy(deep=True)
            twin.target_duration = half
            working.insert(position + 1, twin)
        else:
            if len(working) <= MIN_SLOTS:
                break
            position = min(
                range(len(working) - 1),
                key=lambda i: working[i].target_duration
                + working[i + 1].target_duration,
            )
            merged = working[position]
            merged.target_duration = round(
                merged.target_duration + working[position + 1].target_duration, 3
            )
            # The absorbed shot's transition disappears with it; the surviving
            # shot keeps its own outgoing transition.
            del working[position + 1]

    for order, slot in enumerate(working):
        slot.order = order
        slot.slot_id = f"slot_{order + 1:02d}"
        slot.duration_range = (
            round(slot.target_duration * 0.7, 3),
            round(slot.target_duration * 1.4, 3),
        )
    return working


def _rotate(plan: EditPlanSpec, by: int):
    """Reorder the middle sections, keeping the opening and ending in place.

    The first and last sections carry the most structural weight - an edit that
    opens on its ending reads as broken rather than as a variation - so only
    what sits between them moves.
    """
    sections = plan.story.sections
    if len(sections) <= 2:
        return plan.slots, sections
    head, middle, tail = sections[0], sections[1:-1], sections[-1]
    shift = by % len(middle) if middle else 0
    middle = middle[shift:] + middle[:shift]
    ordered = [head, *middle, tail]

    by_name = {s.name: [x for x in plan.slots if x.section == s.name] for s in ordered}
    slots = []
    order = 0
    rebuilt: list[Section] = []
    for section in ordered:
        ids = []
        for slot in by_name.get(section.name, []):
            slot.order = order
            order += 1
            slots.append(slot)
            ids.append(slot.slot_id)
        rebuilt.append(
            Section(
                name=section.name,
                target_duration=section.target_duration,
                slot_ids=ids,
            )
        )
    # Slots whose section never appeared in the story list still have to go
    # somewhere, or the plan silently loses shots.
    for slot in plan.slots:
        if slot not in slots:
            slot.order = order
            order += 1
            slots.append(slot)
    return slots, rebuilt
