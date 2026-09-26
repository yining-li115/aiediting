"""What the planner model is allowed to return, and how it becomes a plan.

Two jobs live here, deliberately together.

**The draft schema** is the shape the model fills in. It is not EditPlanSpec:
it omits everything the model has no business deciding (ids, duration ranges,
spec versions, provenance) and it bounds everything the model gets wrong when
left free. A model asked for an open-ended plan writes advice; a model asked
to fill this writes a plan.

**The repair pass** turns a draft into a real EditPlanSpec. Every guarantee the
matcher relies on is enforced here rather than requested in the prompt, because
a prompt is a request and this is a contract. The division of labour is the one
the whole codebase uses: the model decides what is good, the code decides what
is valid.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..specs.base import ModelAnswer
from ..specs.edit_plan import (
    AudioPlan,
    EditPlanSpec,
    Intent,
    Rhythm,
    Section,
    Slot,
    Story,
    TextPlan,
)
from ..specs.enums import Pace, SemanticRole, ShotType, Transition
from .merge import Conditioning

# TODO(review): bounds on what the model may ask for. MAX_SLOTS stops a runaway
# plan of 200 quarter-second slots; the slot length bounds are the same ones
# the deterministic planner uses. All three are judgement calls about what
# reads as a shot rather than a flash or a stare, never validated on real cuts.
MIN_SLOT = 0.6
MAX_SLOT = 6.0
MAX_SLOTS = 80
MIN_SLOTS = 3


class SectionDraft(ModelAnswer):
    name: str = Field(max_length=40)
    target_duration: float = Field(gt=0.0, le=600.0)


class SlotDraft(ModelAnswer):
    """One shot-shaped hole, as the model describes it.

    ``semantic_role`` and ``shot_type`` are Literal-typed so the JSON schema
    constrains them: a free-text role becomes "unknown" downstream, which
    silently discards most of the matcher's scoring signal for that slot.
    """

    order: int = Field(ge=0)
    section: str = Field(max_length=40)
    semantic_role: SemanticRole
    shot_type: ShotType
    target_duration: float = Field(gt=0.0, le=30.0)
    energy: float = Field(ge=0.0, le=1.0)
    transition_out: Transition = "hard_cut"
    required: bool = True


class PlanDraft(ModelAnswer):
    narrative_arc: str = Field(default="", max_length=200)
    sections: list[SectionDraft] = Field(max_length=12)
    slots: list[SlotDraft] = Field(max_length=MAX_SLOTS)
    pace: Pace = "medium"
    transition_density: float = Field(default=0.1, ge=0.0, le=1.0)
    music_sync_strength: float = Field(default=0.5, ge=0.0, le=1.0)


class DraftRejected(ValueError):
    """The draft could not be repaired into a usable plan."""


def to_plan(
    draft: PlanDraft,
    *,
    project_id: str,
    intent: Intent,
    conditioning: Conditioning,
    pool_summary: dict[str, Any] | None = None,
    candidate_index: int = 0,
    seed: int | None = None,
) -> EditPlanSpec:
    """Draft -> EditPlanSpec, with every invariant enforced by code.

    Raises ``DraftRejected`` when nothing usable survives, so the caller can
    fall back to the deterministic planner instead of shipping a broken plan.
    """
    repairs: list[str] = []

    slots: list[Slot] = []
    for index, item in enumerate(sorted(draft.slots, key=lambda s: s.order)):
        duration = item.target_duration
        if not MIN_SLOT <= duration <= MAX_SLOT:
            duration = max(MIN_SLOT, min(MAX_SLOT, duration))
            repairs.append(
                f"slot {index}: {item.target_duration:.2f}s clamped to {duration:.2f}s"
            )
        slots.append(
            Slot(
                slot_id=f"slot_{index + 1:02d}",
                # Renumbered rather than trusted: models emit duplicate and
                # gapped order values, and slot order is what the timeline is
                # built from.
                order=index,
                section=item.section or None,
                semantic_role=item.semantic_role,
                shot_type=item.shot_type,
                target_duration=round(duration, 3),
                energy=item.energy,
                transition_out=item.transition_out,
                required=item.required,
            )
        )

    if len(slots) < MIN_SLOTS:
        raise DraftRejected(
            f"plan has {len(slots)} slots, fewer than the {MIN_SLOTS} needed "
            "for anything that reads as an edit"
        )

    slots, pool_repairs = _fit_to_pool(slots, pool_summary or {})
    repairs.extend(pool_repairs)

    if conditioning.transition_density <= 0.0:
        changed = sum(1 for s in slots if s.transition_out != "hard_cut")
        if changed:
            for slot in slots:
                slot.transition_out = "hard_cut"
            # The user said no transitions. A model that adds them anyway is
            # overruled here rather than argued with in the prompt.
            repairs.append(f"{changed} transitions removed: none were allowed")

    sections = _sections_from(draft, slots)

    # The plan carries the MERGED length, not the parsed one. normalise_durations
    # reads intent.target_duration, so leaving the parsed default in place means
    # the merge resolves a length and the arithmetic then ignores it - a
    # reference-decided 23.8s plan came out 45s exactly that way.
    intent = intent.model_copy(deep=True)
    intent.target_duration = conditioning.target_duration
    intent.duration_tolerance = conditioning.duration_tolerance

    plan = EditPlanSpec(
        project_id=project_id,
        mode="auto",
        intent=intent,
        story=Story(sections=sections, narrative_arc=draft.narrative_arc or None),
        slots=slots,
        rhythm=Rhythm(
            pace=draft.pace,
            transition_density=conditioning.transition_density,
            music_sync_strength=conditioning.music_sync_strength,
            beat_alignment=(
                "soft" if conditioning.music_sync_strength >= 0.6 else "none"
            ),
        ),
        text=TextPlan(
            subtitle_density=conditioning.subtitle_density,
            source="none",
        ),
        audio=AudioPlan(
            mood=conditioning.mood[0] if conditioning.mood else None,
            use_music=conditioning.use_music,
            keep_original_sound=conditioning.keep_original_sound,
        ),
    )
    plan.provenance.candidate_index = candidate_index
    plan.provenance.seed = seed
    plan.provenance.extra = {
        "source": "model",
        "repairs": repairs,
        "decided_by": conditioning.decided_by,
    }
    # Non-negotiable and never asked of the model: the plan must total the
    # target duration. The prompt tells it to aim for the right proportions
    # precisely because this step owns the arithmetic.
    plan.normalise_durations()
    # After, not before: normalisation rescales every slot, and a section list
    # computed beforehand would still be quoting the model's original numbers.
    plan.story.sections = resync_sections(plan)
    plan.rhythm.pace = observed_pace(plan)
    return plan


def observed_pace(plan: EditPlanSpec):
    """The pace the plan actually has, not the one it was asked for.

    Normalising onto the target duration moves every shot, and a variant can
    change how many there are. A `pace` field left at whatever the model or the
    conditioning said would describe an intention rather than the plan in front
    of it - and the critic, the matcher and the user all read it as a fact.
    """
    from .plan import pace_for  # noqa: PLC0415

    if not plan.slots:
        return plan.rhythm.pace
    durations = sorted(s.target_duration for s in plan.slots)
    middle = durations[len(durations) // 2]
    return pace_for(middle)


def resync_sections(plan: EditPlanSpec) -> list[Section]:
    """Rebuild section membership and durations from the current slots.

    Call after anything that adds, removes or rescales slots. A section whose
    ``slot_ids`` point at shots that no longer exist, or whose duration
    disagrees with the shots inside it, is worse than no section list at all:
    it looks authoritative and is wrong.
    """
    order: list[str] = []
    for section in plan.story.sections:
        if section.name not in order:
            order.append(section.name)
    for slot in plan.slots:
        if slot.section and slot.section not in order:
            order.append(slot.section)

    rebuilt: list[Section] = []
    for name in order:
        members = [s for s in plan.slots if s.section == name]
        if not members:
            # A section with no slots is a heading with nothing under it.
            continue
        rebuilt.append(
            Section(
                name=name,
                target_duration=round(sum(s.target_duration for s in members), 3),
                slot_ids=[s.slot_id for s in members],
            )
        )
    return rebuilt


def _fit_to_pool(
    slots: list[Slot], pool_summary: dict[str, Any]
) -> tuple[list[Slot], list[str]]:
    """Demote slots the footage cannot fill.

    Demoted, not deleted: dropping them would change the plan's length and hide
    the mismatch, and the matcher's adaptation pass may still find something
    workable. ``required=False`` is the existing way to say "skip this if you
    must", so the failure stays visible and recoverable.
    """
    by_role = pool_summary.get("by_semantic_role") or {}
    if not by_role:
        return slots, []
    available = {
        role: float(info.get("total_duration", 0.0)) for role, info in by_role.items()
    }
    missing: dict[str, int] = {}
    for slot in slots:
        if available.get(slot.semantic_role, 0.0) <= 0.0 and slot.required:
            slot.required = False
            missing[slot.semantic_role] = missing.get(slot.semantic_role, 0) + 1
    repairs = [
        f"{count} '{role}' slots made optional: the pool has no such footage"
        for role, count in sorted(missing.items())
    ]
    return slots, repairs


def _sections_from(draft: PlanDraft, slots: list[Slot]) -> list[Section]:
    """Rebuild the section list from the slots that actually survived.

    The model's own section list is used for names and order only. Its
    durations are recomputed, because a section whose stated length disagrees
    with the slots inside it is a section nobody can act on.
    """
    order: list[str] = []
    for item in draft.sections:
        if item.name and item.name not in order:
            order.append(item.name)
    for slot in slots:
        if slot.section and slot.section not in order:
            order.append(slot.section)

    sections: list[Section] = []
    for name in order:
        members = [s for s in slots if s.section == name]
        if not members:
            # A section with no slots is a heading with nothing under it.
            continue
        sections.append(
            Section(
                name=name,
                target_duration=round(sum(s.target_duration for s in members), 3),
                slot_ids=[s.slot_id for s in members],
            )
        )
    return sections
