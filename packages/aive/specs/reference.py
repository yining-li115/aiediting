"""ReferenceAnalysis - what a reference video's editing looks like.

This is **not** an EditPlanSpec. It is the analysis unit for one reference
video, and it feeds two different things:

    B2 (template):  1 x ReferenceAnalysis -> slots[]      a sequence
    B1 (style):     N x ReferenceAnalysis -> StylePrior   a distribution

Keeping it separate from EditPlanSpec is what lets those two modes share all
the analysis work and diverge only at the point where they differ.

**Nothing here is pixels or audio.** Structure, timing and grammar transfer;
the reference's own frames, music and caption text do not. See section 16.1 -
"transfer editing grammar, not protected content" - which is a legal boundary
rather than a design preference.
"""

from __future__ import annotations

from pydantic import Field

from .base import Provenance, Spec, VersionedSpec, new_id
from .enums import CameraMotion, SemanticRole, ShotType, Transition

# TODO(review): below this, references disagree enough that the aggregate
# describes none of them. 0.5 is a guess - it has never been checked against
# a set of references a person would call "the same style". Validate by
# scoring a deliberately mixed set (a fast MV plus a slow travel film) and
# confirming it lands below, while three clips from one creator land above.
MIN_CONSISTENCY = 0.5


class ReferenceShot(Spec):
    """One shot in the reference, as observed."""

    index: int
    start: float
    end: float
    shot_type: ShotType = "unknown"
    semantic_role: SemanticRole = "unknown"
    motion: CameraMotion = "unknown"
    energy: float = 0.5
    transition_out: Transition = "hard_cut"
    # Whether captions are on screen - the density transfers, the words do not.
    has_text: bool = False
    # A short description, kept for debugging and for explaining a remake to
    # the user. Never used to reproduce the shot.
    semantic: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start


class DurationStats(Spec):
    mean: float = 0.0
    median: float = 0.0
    p10: float = 0.0
    p90: float = 0.0
    std: float = 0.0


class ReferenceMusic(Spec):
    """How the reference's cutting relates to its music.

    The relationship transfers; the track does not. ``beat_times`` exists so
    that ``cut_to_beat_offset`` can be computed and so a remake can aim for the
    same relationship using its own music.
    """

    bpm: float | None = None
    beat_count: int = 0
    # Fraction of cuts landing within half a beat of a beat: 0 means the edit
    # ignores the music, 1 means every cut is on the grid.
    beat_sync_strength: float = 0.0
    cut_to_beat_offset_ms: DurationStats = Field(default_factory=DurationStats)
    has_speech: bool = False


class ReferenceText(Spec):
    # TODO(review): populated only if OCR is available. Until then density is
    # unknown rather than "none" - claiming a reference has no captions when we
    # simply did not look would make a remake silently drop a whole layer of
    # its style.
    detected: bool = False
    subtitle_density: str = "unknown"
    coverage: float = 0.0  # fraction of the runtime with text on screen


class ReferenceAnalysis(VersionedSpec):
    reference_id: str = Field(default_factory=lambda: new_id("ref"))
    project_id: str | None = None
    duration: float
    shot_count: int = 0

    shots: list[ReferenceShot] = Field(default_factory=list)
    shot_duration: DurationStats = Field(default_factory=DurationStats)

    pace: str = "medium"
    transition_density: float = 0.0
    music: ReferenceMusic = Field(default_factory=ReferenceMusic)
    text: ReferenceText = Field(default_factory=ReferenceText)

    # The observed sequences, which is what B2 turns into slots and what B1
    # turns into transition probabilities.
    shot_scale_sequence: list[str] = Field(default_factory=list)
    semantic_structure: list[str] = Field(default_factory=list)
    style_tags: list[str] = Field(default_factory=list)

    provenance: Provenance = Field(default_factory=Provenance)

    def durations(self) -> list[float]:
        return [shot.duration for shot in self.shots]


class StylePrior(VersionedSpec):
    """B1's output: the editing grammar of N references, as a distribution.

    This is what makes a style transferable. Nothing here names a shot, a
    moment or a frame of the reference - it is all statistics over how the
    cutting behaved, which is exactly the part that can be applied to somebody
    else's footage. A remake (B2) walks the reference's shot sequence; a style
    prior only says "shots tend to run about this long, and a wide tends to be
    followed by a medium".

    It conditions the planner. It is not a plan, and it never becomes one on
    its own: the planner still has to decide what to do with the user's actual
    material, and an explicit user instruction outranks anything here.
    """

    prior_id: str = Field(default_factory=lambda: new_id("sp"))
    project_id: str | None = None
    source_reference_ids: list[str] = Field(default_factory=list)

    shot_duration: DurationStats = Field(default_factory=DurationStats)
    # How long the references run. Not a style property in itself, but the
    # best available answer to "how long should this be" when the user did
    # not say - far better than a constant.
    total_duration: DurationStats = Field(default_factory=DurationStats)
    pace: str = "medium"

    shot_scale_distribution: dict[str, float] = Field(default_factory=dict)
    shot_scale_bigrams: dict[str, float] = Field(default_factory=dict)
    role_distribution: dict[str, float] = Field(default_factory=dict)
    role_bigrams: dict[str, float] = Field(default_factory=dict)

    transition_density: DurationStats = Field(default_factory=DurationStats)
    subtitle_density: str = "unknown"
    beat_sync_strength: DurationStats = Field(default_factory=DurationStats)
    section_count: DurationStats = Field(default_factory=DurationStats)

    style_tags: list[str] = Field(default_factory=list)

    # How much the references actually agree. Five clips with nothing in common
    # average out to a prior that describes none of them, and applying that is
    # worse than applying no prior at all - so this is reported, and a low
    # value is something the user is told about rather than something the
    # planner silently acts on.
    consistency: float = 0.0

    provenance: Provenance = Field(default_factory=Provenance)

    def is_usable(self) -> bool:
        """Whether this prior says anything worth conditioning on."""
        return bool(self.source_reference_ids) and self.consistency >= MIN_CONSISTENCY
