"""Constraint merge: several sources of "how should this be cut" become one.

The planner must be told a single answer per question. Four things can answer
each question - what the user just said, what they locked earlier, what the
project already is, and what their reference videos do - and they disagree
routinely. "像这条剪，但慢一点" is a conflict on `pace` and an agreement on
everything else, in one sentence.

The precedence is fixed and lives here rather than being re-decided at each
call site::

    1. explicit user instruction, this turn
    2. user-locked / confirmed content
    3. current project state
    4. reference (style prior)
    5. system default

Two properties matter as much as the order itself.

**Merge, not choose.** A reference that loses on `pace` still wins on shot
scale grammar, section count and transition density. Picking one source
wholesale would throw away four correct answers to punish one wrong one.

**Every field records who decided it.** ``decided_by`` is the reason a user
can ask "why is this shot 1.2 seconds" and get "your reference video, median
1.15s" instead of a shrug. It is also how a wrong answer gets traced to the
layer that produced it rather than to the planner that consumed it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..specs.base import Spec
from ..specs.edit_plan import Intent
from ..specs.enums import Pace, SubtitleDensity
from ..specs.reference import DurationStats, StylePrior
from pydantic import Field


class Layer(str, Enum):
    """Who is answering. Lower in the list loses to higher."""

    USER = "user"
    LOCKED = "locked"
    STATE = "state"
    REFERENCE = "reference"
    DEFAULT = "default"


# The order is the policy. Nothing else in the codebase may reorder it.
PRECEDENCE: tuple[Layer, ...] = (
    Layer.USER,
    Layer.LOCKED,
    Layer.STATE,
    Layer.REFERENCE,
    Layer.DEFAULT,
)

# Ordered slow -> fast, so a relative instruction is an index shift.
PACE_LADDER: tuple[Pace, ...] = ("slow", "medium", "medium_fast", "fast")
SUBTITLE_LADDER: tuple[SubtitleDensity, ...] = ("none", "low", "medium", "high")

# TODO(review): system defaults, used only when nobody else answered. They are
# what an edit looks like with no user instruction and no reference, so they
# set the house style. Never validated against anything - worth revisiting once
# there are cuts a person has actually judged.
DEFAULT_PACE: Pace = "medium"
DEFAULT_TRANSITION_DENSITY = 0.1
DEFAULT_SUBTITLE_DENSITY: SubtitleDensity = "none"
DEFAULT_MUSIC_SYNC = 0.5

# TODO(review): how much a relative instruction moves things. "慢一点" shifting
# one rung on the pace ladder and "少一点转场" halving the density are guesses
# about what a person means by "a bit". Validate by whether someone who asked
# for a slower cut can tell the result apart from what they started with.
FEW_TRANSITIONS_FACTOR = 0.5
# One rung on the pace ladder, as a multiplier on shot length. The ladder's own
# rungs sit roughly this far apart (0.75 / 1.1 / 1.6 / 2.6s).
PACE_STEP_FACTOR = 1.4


@dataclass
class Decision:
    """One resolved value and the layer that produced it."""

    value: Any
    layer: Layer
    # Set when a relative instruction moved the value after resolution, e.g.
    # the reference decided medium_fast and the user said "慢一点".
    adjusted_by: str | None = None

    def describe(self) -> str:
        if self.adjusted_by:
            return f"{self.layer.value}+{self.adjusted_by}"
        return self.layer.value


class Conditioning(Spec):
    """Everything the planner is allowed to be told about how to cut.

    Deliberately flat and small. The planner receives this and a pool summary,
    and nothing else: not the raw prompt, not the reference analyses, not the
    project history. Anything the planner needs has to be resolved into a field
    here first, which is what keeps the precedence rules from being quietly
    re-litigated inside a prompt.
    """

    target_duration: float = 45.0
    duration_tolerance: float = 0.15

    pace: Pace = DEFAULT_PACE
    # -1 slower, +1 faster, relative to whatever `pace` was resolved from.
    # Kept separately because the four-rung ladder saturates: an instruction
    # that could not move the label still has to move the shot lengths.
    pace_shift: int = 0
    opening_pace: Pace | None = None
    transition_density: float = DEFAULT_TRANSITION_DENSITY
    music_sync_strength: float = DEFAULT_MUSIC_SYNC

    subtitle_density: SubtitleDensity = DEFAULT_SUBTITLE_DENSITY
    use_music: bool = True
    keep_original_sound: bool = False

    # Reference grammar. Empty when there is no usable prior, and the planner
    # is expected to cope with that rather than treating it as an error.
    shot_duration: DurationStats | None = None
    shot_scale_distribution: dict[str, float] = Field(default_factory=dict)
    shot_scale_bigrams: dict[str, float] = Field(default_factory=dict)
    role_distribution: dict[str, float] = Field(default_factory=dict)
    role_bigrams: dict[str, float] = Field(default_factory=dict)
    section_count: float | None = None

    style: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    negative_style: list[str] = Field(default_factory=list)
    prioritize: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    # field name -> the layer that decided it, e.g. {"pace": "reference+慢一点"}
    decided_by: dict[str, str] = Field(default_factory=dict)
    # Set when a style prior existed but was too inconsistent to use. The user
    # is told; the plan proceeds without it.
    prior_rejected: str | None = None


def _first(candidates: list[tuple[Layer, Any]]) -> tuple[Any, Layer]:
    """The highest-precedence non-None answer.

    ``None`` means "this layer did not answer", which is why Intent keeps its
    unset preferences as None instead of filling in defaults at parse time.
    """
    by_layer = {layer: value for layer, value in candidates if value is not None}
    for layer in PRECEDENCE:
        if layer in by_layer:
            return by_layer[layer], layer
    return None, Layer.DEFAULT


def _shift(ladder: tuple, value: Any, steps: int) -> Any:
    index = ladder.index(value) if value in ladder else len(ladder) // 2
    return ladder[max(0, min(len(ladder) - 1, index + steps))]


def merge(
    intent: Intent,
    prior: StylePrior | None = None,
    state: dict[str, Any] | None = None,
    locked: dict[str, Any] | None = None,
) -> Conditioning:
    """Resolve every planning question into one answer with an attribution.

    ``state`` and ``locked`` are accepted but unused for now: the project-state
    and lock layers do not exist yet. They are in the signature because adding
    a layer later must not change every call site, and because leaving them out
    would make the precedence order look like a two-source special case rather
    than the general rule it is.
    """
    state = state or {}
    locked = locked or {}
    decisions: dict[str, Decision] = {}

    usable_prior = prior if (prior is not None and prior.is_usable()) else None
    rejected = None
    if prior is not None and usable_prior is None:
        rejected = (
            f"style prior {prior.prior_id} ignored: consistency "
            f"{prior.consistency:.2f} - the references disagree too much for "
            f"their average to describe any of them"
        )

    def resolve(name: str, user, lock=None, st=None, ref=None, default=None) -> Any:
        value, layer = _first(
            [
                (Layer.USER, user),
                (Layer.LOCKED, lock),
                (Layer.STATE, st),
                (Layer.REFERENCE, ref),
                (Layer.DEFAULT, default),
            ]
        )
        decisions[name] = Decision(value=value, layer=layer)
        return value

    constraints = set(intent.user_constraints)

    # --- duration ----------------------------------------------------------
    # The reference answers only when nobody above it did. A stated length is
    # layer 1 and wins outright, so a 12-second reference can never shorten a
    # 45-second request - but "剪一个风格类似的" with no length named should
    # follow the reference rather than a constant, which was picking 45s for a
    # 24-second reference and then forcing the planner to stretch every shot.
    target = resolve(
        "target_duration",
        user=intent.target_duration if intent.duration_stated else None,
        st=state.get("target_duration"),
        ref=(
            usable_prior.total_duration.median
            if usable_prior and usable_prior.total_duration.median > 0
            else None
        ),
        default=intent.target_duration,
    )

    # --- pace --------------------------------------------------------------
    pace = resolve(
        "pace",
        user=intent.rhythm.overall_pace,
        lock=locked.get("pace"),
        st=state.get("pace"),
        ref=usable_prior.pace if usable_prior else None,
        default=DEFAULT_PACE,
    )
    # Relative instructions apply on top of whoever won, rather than replacing
    # them. "像这条剪，但慢一点" must keep the reference's grammar and move only
    # its pace - overriding with an absolute "slow" would discard the reference
    # answer the user was explicitly asking to keep.
    pace_shift = 0
    if "slow_pace" in constraints and "fast_pace" not in constraints:
        pace_shift = -1
        decisions["pace"].adjusted_by = "slow_pace"
    elif "fast_pace" in constraints:
        pace_shift = +1
        decisions["pace"].adjusted_by = "fast_pace"
    pace = _shift(PACE_LADDER, pace, pace_shift)

    opening = resolve(
        "opening_pace",
        user=intent.rhythm.opening_pace,
        lock=locked.get("opening_pace"),
        default=None,
    )

    # --- transitions -------------------------------------------------------
    density = resolve(
        "transition_density",
        user=intent.rhythm.transition_density,
        st=state.get("transition_density"),
        ref=usable_prior.transition_density.mean if usable_prior else None,
        default=DEFAULT_TRANSITION_DENSITY,
    )
    if "no_transitions" in constraints:
        density = 0.0
        decisions["transition_density"].adjusted_by = "no_transitions"
    elif "few_transitions" in constraints:
        density = round(density * FEW_TRANSITIONS_FACTOR, 3)
        decisions["transition_density"].adjusted_by = "few_transitions"

    # --- subtitles ---------------------------------------------------------
    # "unknown" is not an answer: it means nobody ran OCR on the reference, and
    # treating that as "no subtitles" would drop a whole layer of the style on
    # the grounds that we did not look.
    ref_subtitle = None
    if usable_prior and usable_prior.subtitle_density in SUBTITLE_LADDER:
        ref_subtitle = usable_prior.subtitle_density
    subtitle = resolve(
        "subtitle_density",
        user=intent.text.subtitle_density,
        lock=locked.get("subtitle_density"),
        st=state.get("subtitle_density"),
        ref=ref_subtitle,
        default=DEFAULT_SUBTITLE_DENSITY,
    )
    if "no_subtitles" in constraints:
        subtitle = "none"
        decisions["subtitle_density"].adjusted_by = "no_subtitles"
    elif "few_subtitles" in constraints:
        subtitle = _shift(SUBTITLE_LADDER, subtitle, -1)
        decisions["subtitle_density"].adjusted_by = "few_subtitles"

    # --- audio -------------------------------------------------------------
    use_music = resolve(
        "use_music",
        user=intent.audio.use_music,
        lock=locked.get("use_music"),
        st=state.get("use_music"),
        default=True,
    )
    if "no_music" in constraints:
        use_music = False
        decisions["use_music"].adjusted_by = "no_music"

    keep_sound = resolve(
        "keep_original_sound",
        user=intent.audio.keep_original_audio,
        lock=locked.get("keep_original_sound"),
        st=state.get("keep_original_sound"),
        default=False,
    )
    if "keep_original_sound" in constraints:
        keep_sound = True
        decisions["keep_original_sound"].adjusted_by = "keep_original_sound"

    sync = resolve(
        "music_sync_strength",
        st=state.get("music_sync_strength"),
        user=None,
        ref=usable_prior.beat_sync_strength.mean if usable_prior else None,
        default=DEFAULT_MUSIC_SYNC,
    )

    # --- grammar, reference-only -------------------------------------------
    # Nothing else can answer these: the user does not speak in shot-scale
    # bigrams, and there is no sensible default for "what follows a wide shot".
    # Absent a prior they stay empty and the planner decides for itself.
    for name in (
        "shot_duration",
        "shot_scale_distribution",
        "shot_scale_bigrams",
        "role_distribution",
        "role_bigrams",
        "section_count",
    ):
        decisions[name] = Decision(
            value=None, layer=Layer.REFERENCE if usable_prior else Layer.DEFAULT
        )

    shot_duration = usable_prior.shot_duration if usable_prior else None
    if shot_duration is not None and pace_shift:
        # The ladder has four rungs, so "faster" against an already-fast
        # reference cannot move the label. The measured length can still move,
        # and must: an instruction that lands on a ceiling is still an
        # instruction, and leaving it at the reference's own median would mean
        # the user asked for a change and got none.
        factor = PACE_STEP_FACTOR ** (-pace_shift)
        shot_duration = DurationStats(
            mean=round(shot_duration.mean * factor, 3),
            median=round(shot_duration.median * factor, 3),
            p10=round(shot_duration.p10 * factor, 3),
            p90=round(shot_duration.p90 * factor, 3),
            std=round(shot_duration.std * factor, 3),
        )

    return Conditioning(
        target_duration=target,
        pace_shift=pace_shift,
        duration_tolerance=intent.duration_tolerance,
        pace=pace,
        opening_pace=opening,
        transition_density=max(0.0, min(1.0, float(density))),
        music_sync_strength=max(0.0, min(1.0, float(sync))),
        subtitle_density=subtitle,
        use_music=bool(use_music),
        keep_original_sound=bool(keep_sound),
        shot_duration=shot_duration,
        shot_scale_distribution=(
            usable_prior.shot_scale_distribution if usable_prior else {}
        ),
        shot_scale_bigrams=usable_prior.shot_scale_bigrams if usable_prior else {},
        role_distribution=usable_prior.role_distribution if usable_prior else {},
        role_bigrams=usable_prior.role_bigrams if usable_prior else {},
        section_count=(
            usable_prior.section_count.mean
            if usable_prior and usable_prior.section_count.mean > 0
            else None
        ),
        # Style is unioned rather than resolved: the user's words and the
        # reference's derived tags describe different things ("chill" vs
        # "energetic cutting") and both are true at once.
        style=sorted(set(intent.style) | set(usable_prior.style_tags if usable_prior else [])),
        mood=list(intent.mood),
        negative_style=list(intent.negative_style),
        prioritize=list(intent.content.prioritize),
        avoid=list(intent.content.avoid),
        decided_by={k: v.describe() for k, v in decisions.items()},
        prior_rejected=rejected,
    )
