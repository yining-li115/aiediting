"""EditPlanSpec - "how it should be cut".

This layer is deliberately **not bound to any material**. It is what the
planner produces, what reference decomposition produces, and what global
semantic edits ("make it faster", "drop the subtitles") act on.

Keeping it free of asset references is what lets a style learned from a
reference video be applied to a completely different set of user clips.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from .base import Provenance, Spec, VersionedSpec, new_id
from .enums import (
    BeatAlignment,
    CameraMotion,
    EditMode,
    Pace,
    SemanticRole,
    ShotType,
    SubtitleDensity,
    SubtitleSource,
    Transition,
)


class Intent(Spec):
    """What the user asked for."""

    target_duration: float = 45.0
    duration_tolerance: float = 0.15  # fraction of target_duration
    aspect_ratio: str = "9:16"
    platform: str | None = None
    style: list[str] = Field(default_factory=list)
    user_constraints: list[str] = Field(default_factory=list)
    language: str = "zh"


class Section(Spec):
    """One narrative beat, e.g. opening / exploration / food / ending."""

    name: str
    target_duration: float
    slot_ids: list[str] = Field(default_factory=list)


class Story(Spec):
    sections: list[Section] = Field(default_factory=list)
    narrative_arc: str | None = None


class Slot(Spec):
    """A shot-shaped hole: what kind of shot goes here, and for how long.

    ``duration_range`` is the room the matcher is allowed to work in. Anything
    outside it must be recorded as an explicit adaptation action rather than
    quietly applied, otherwise "why is this shot 3 seconds long?" becomes
    unanswerable.
    """

    slot_id: str = Field(default_factory=lambda: new_id("slot"))
    order: int
    section: str | None = None
    semantic_role: SemanticRole = "unknown"
    shot_type: ShotType = "unknown"
    target_duration: float
    duration_range: tuple[float, float] | None = None
    motion: CameraMotion | None = None
    energy: float = 0.5
    transition_out: Transition = "hard_cut"
    speed_intent: float = 1.0
    # required=False means the matcher may drop this slot when no candidate
    # fits, instead of failing the whole plan.
    required: bool = True
    notes: str | None = None

    @model_validator(mode="after")
    def _default_range(self) -> "Slot":
        if self.duration_range is None:
            object.__setattr__(
                self,
                "duration_range",
                (self.target_duration * 0.7, self.target_duration * 1.4),
            )
        return self


class Rhythm(Spec):
    """Two kinds of field live here, and both are needed.

    ``pace`` / ``transition_density`` / ``music_sync_strength`` are the knobs a
    user turns. ``shot_duration_pattern`` / ``beat_alignment`` are the concrete
    quantities reference fidelity is measured against - rhythm similarity needs
    the actual per-shot duration sequence, not a coarse enum.
    """

    pace: Pace = "medium"
    # One entry per slot, in slot order.
    shot_duration_pattern: list[float] = Field(default_factory=list)
    transition_density: float = 0.1
    music_sync_strength: float = 0.5
    beat_alignment: BeatAlignment = "none"


class TextPlan(Spec):
    subtitle_density: SubtitleDensity = "none"
    style: str = "minimal"
    source: SubtitleSource = "none"


class AudioPlan(Spec):
    mood: str | None = None
    # Distinct from music_id being unset: that means "not chosen yet", this
    # means "the user asked for none". Conflating them would make "不要音乐"
    # indistinguishable from a track that simply has not been picked.
    use_music: bool = True
    music_id: str | None = None
    keep_original_sound: bool = False
    duck_music_under_speech: bool = True


class EditPlanSpec(VersionedSpec):
    """The full plan. Consumed by the matcher, the critic and the agent."""

    plan_id: str = Field(default_factory=lambda: new_id("plan"))
    project_id: str
    mode: EditMode = "auto"

    intent: Intent = Field(default_factory=Intent)
    story: Story = Field(default_factory=Story)
    slots: list[Slot] = Field(default_factory=list)
    rhythm: Rhythm = Field(default_factory=Rhythm)
    text: TextPlan = Field(default_factory=TextPlan)
    audio: AudioPlan = Field(default_factory=AudioPlan)

    provenance: Provenance = Field(default_factory=Provenance)

    # --- convenience -------------------------------------------------------

    def slot_by_id(self, slot_id: str) -> Slot | None:
        return next((s for s in self.slots if s.slot_id == slot_id), None)

    def planned_duration(self) -> float:
        return sum(s.target_duration for s in self.slots)

    def normalise_durations(self) -> None:
        """Scale slot durations so the plan hits ``intent.target_duration``.

        The LLM is not trusted to do this arithmetic; the code enforces it.
        ``shot_duration_pattern`` is rewritten to match so the two never drift.
        """
        total = self.planned_duration()
        if total <= 0:
            return
        factor = self.intent.target_duration / total
        for slot in self.slots:
            slot.target_duration = round(slot.target_duration * factor, 3)
            lo, hi = slot.duration_range or (0.0, 0.0)
            slot.duration_range = (round(lo * factor, 3), round(hi * factor, 3))
        self.rhythm.shot_duration_pattern = [s.target_duration for s in self.slots]
