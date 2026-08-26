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
