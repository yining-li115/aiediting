"""Two-stage planning: story and rhythm first, then slots.

The planner never sees the clip list. It reads an aggregate summary of the
pool - how much footage exists per semantic role - and decides structure from
that. Two reasons this matters:

* asking a model to choose from 300 clips and design a structure in one step
  is far harder to debug than two smaller steps, and the failure mode ("it
  picked odd clips") is indistinguishable from ("it planned badly");
* a plan that is independent of specific material is exactly what makes a
  style learned from a reference video transferable to different footage.

Duration arithmetic is done in code. A plan that sums to the wrong length is a
bug the code must not be able to ship, so it is normalised rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

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
from ..specs.enums import Pace, SemanticRole, ShotType
from .variants import Variant, pick

# A travel/daily vlog arc. Sections are dropped when the pool cannot fill them,
# so this is an upper bound on structure rather than a fixed template.
SECTION_PLAN: tuple[tuple[str, tuple[SemanticRole, ...], float], ...] = (
    ("opening", ("opening_environment", "environment"), 0.18),
    ("exploration", ("environment", "transit", "person_activity", "action"), 0.34),
    ("detail", ("detail", "food", "interaction"), 0.30),
    ("ending", ("ending_environment", "ending", "environment"), 0.18),
)

# Shot scale rhythm inside a section: establish, then move closer.
SCALE_CYCLE: tuple[ShotType, ...] = ("wide", "medium", "close_up", "medium")
ALTERNATING_CYCLE: tuple[ShotType, ...] = ("wide", "close_up", "medium", "extreme_close_up")

BASE_SHOT_SECONDS = 1.5
MIN_SLOT = 0.6
MAX_SLOT = 4.0


@dataclass
class PlanningContext:
    """Everything the planner is allowed to know."""

    project_id: str
    intent: Intent
    pool_summary: dict

    def available_roles(self) -> dict[str, float]:
        by_role = self.pool_summary.get("by_semantic_role", {})
        return {role: info["total_duration"] for role, info in by_role.items()}

    def usable_duration(self) -> float:
        return float(self.pool_summary.get("total_usable_duration", 0.0))


def _pace_for(shot_seconds: float) -> Pace:
    if shot_seconds <= 0.9:
        return "fast"
    if shot_seconds <= 1.3:
        return "medium_fast"
    if shot_seconds <= 2.0:
        return "medium"
    return "slow"


# TODO(review): these numbers decide how much a user instruction actually
# changes the edit, and they are currently guesses. "少一点转场" halving the
# density and "慢一点" adding 30% to shot length feel right, but nobody has
# checked whether a viewer can tell. Worth an A/B once real preference data
# exists (block F): the honest test is whether someone who asked for a slower
# cut can pick the slow version out of a pair.
_PACE_FACTORS: dict[str, float] = {
    "slow_pace": 1.3,
    "fast_pace": 0.75,
}
_STYLE_PACE: dict[str, float] = {
    "energetic": 0.85,
    "relaxed": 1.2,
    "cinematic": 1.2,
}


def _apply_constraints(intent: Intent, variant: Variant) -> tuple[float, float]:
    """User constraints override the variant. Explicit instruction wins.

    Every value in the canonical vocabulary must be handled somewhere, or a
    user's instruction is accepted and then quietly dropped. The ones this
    function does not own are listed below with where they are honoured, so
    that adding a constraint without wiring it up is visible.
    """
    pace_factor = variant.pace_factor
    transition_density = variant.transition_density

    for constraint in intent.user_constraints:
        if constraint == "few_transitions":
            transition_density = min(transition_density, 0.05)
        elif constraint == "no_transitions":
            transition_density = 0.0
        elif constraint in _PACE_FACTORS:
            pace_factor *= _PACE_FACTORS[constraint]
        # Handled elsewhere, deliberately:
        #   no_subtitles / few_subtitles  -> text block, below in build_plan
        #   keep_original_sound / no_music -> audio block, below in build_plan
        #   keep_opening                   -> the agent layer's edit scope,
        #                                     since it only means anything
        #                                     when re-editing (section 11.6)

    for style in intent.style:
        pace_factor *= _STYLE_PACE.get(style, 1.0)

    return pace_factor, transition_density


def _plan_sections(
    context: PlanningContext, variant: Variant
) -> list[tuple[str, tuple[SemanticRole, ...], float]]:
    """Stage 1: which narrative beats this pool can actually support."""
    available = context.available_roles()
    kept = [
        (name, roles, weight)
        for name, roles, weight in SECTION_PLAN
        if any(available.get(role, 0.0) > 0 for role in roles)
    ]
    if not kept:
        # No recognised roles at all: fall back to one undifferentiated body
        # section rather than producing an empty plan.
        kept = [("body", ("unknown",), 1.0)]

    if variant.section_rotation and len(kept) > 3:
        head, middle, tail = kept[0], kept[1:-1], kept[-1]
        rotation = variant.section_rotation % len(middle)
        kept = [head, *middle[rotation:], *middle[:rotation], tail]

    total = sum(weight for _, _, weight in kept)
    return [(name, roles, weight / total) for name, roles, weight in kept]


# TODO(review): the planner never sets slot.motion for stills.
#
# Slots get `motion=None`, so a photo placed in one is rendered static (see
# the Ken Burns note in renderer/compiler.py). Deciding when a still should
# move is an editing judgement the planner is the right place for: if the
# shots either side are moving, a held photo reads as a stall; if it sits
# among other stills, motion would be noise. Requires knowing which slots end
# up holding photos, which is only known after matching - so this may belong
# in the matcher's adaptation step rather than here.


def _subtitle_density(intent: Intent) -> str:
    """A ladder, so "fewer" is actually fewer than the default."""
    if "no_subtitles" in intent.user_constraints:
        return "none"
    if "few_subtitles" in intent.user_constraints:
        return "low"
    if "minimal" in intent.style:
        return "low"
    return "medium"


def _slot_count(section_duration: float, shot_seconds: float) -> int:
    return max(1, round(section_duration / max(shot_seconds, MIN_SLOT)))


def build_plan(
    context: PlanningContext, variant: Variant, candidate_index: int, seed: int
) -> EditPlanSpec:
    """Stage 1 + stage 2 for one candidate."""
    intent = context.intent.model_copy(deep=True)
    pace_factor, transition_density = _apply_constraints(intent, variant)
    shot_seconds = BASE_SHOT_SECONDS * pace_factor

    sections = _plan_sections(context, variant)
    target = intent.target_duration

    slots: list[Slot] = []
    section_specs: list[Section] = []
    order = 0
    scale_cycle = ALTERNATING_CYCLE if variant.alternate_scale else SCALE_CYCLE

    for section_index, (name, roles, share) in enumerate(sections):
        section_duration = target * share
        count = _slot_count(section_duration, shot_seconds)
        per_slot = section_duration / count
        slot_ids: list[str] = []

        for position in range(count):
            duration = per_slot
            if section_index == 0 and position == 0:
                duration *= variant.opening_factor
            duration = max(MIN_SLOT, min(MAX_SLOT, duration))

            slot = Slot(
                slot_id=f"slot_{order + 1:02d}",
                order=order,
                section=name,
                semantic_role=roles[position % len(roles)],
                shot_type=scale_cycle[order % len(scale_cycle)],
                target_duration=round(duration, 3),
                # Energy rises through the middle and settles at the end, so
                # the matcher has something to prefer beyond pure similarity.
                energy=round(
                    0.35 + 0.5 * (section_index / max(len(sections) - 1, 1)), 3
                ),
                # Only the opening is truly required: everything else can be
                # skipped if the pool cannot fill it.
                required=section_index == 0 and position == 0,
            )
            slots.append(slot)
            slot_ids.append(slot.slot_id)
            order += 1

        section_specs.append(
            Section(
                name=name,
                target_duration=round(section_duration, 3),
                slot_ids=slot_ids,
            )
        )

    plan = EditPlanSpec(
        project_id=context.project_id,
        mode="auto",
        intent=intent,
        story=Story(
            sections=section_specs,
            narrative_arc=" -> ".join(name for name, _, _ in sections),
        ),
        slots=slots,
        rhythm=Rhythm(
            pace=_pace_for(shot_seconds),
            transition_density=round(transition_density, 3),
            music_sync_strength=variant.music_sync_strength,
            beat_alignment="soft" if variant.music_sync_strength >= 0.6 else "none",
        ),
        text=TextPlan(
            subtitle_density=_subtitle_density(intent),
            source="none",
        ),
        audio=AudioPlan(
            mood="warm" if "warm" in intent.style else None,
            use_music="no_music" not in intent.user_constraints,
            keep_original_sound="keep_original_sound" in intent.user_constraints,
        ),
    )
    plan.provenance.candidate_index = candidate_index
    plan.provenance.seed = seed + candidate_index
    plan.provenance.extra = {"variant": variant.name, "note": variant.description}
    # The code owns the arithmetic; the plan is forced onto target.
    plan.normalise_durations()
    return plan


def generate_plans(
    context: PlanningContext, count: int, seed: int = 41
) -> list[EditPlanSpec]:
    return [
        build_plan(context, variant, index, seed)
        for index, variant in enumerate(pick(count))
    ]
