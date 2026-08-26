"""Material side: assets, raw segments and the analysed candidate pool."""

from __future__ import annotations

from pydantic import Field

from .base import Spec, VersionedSpec
from .enums import AssetKind, CameraMotion, SemanticRole, ShotType


class Asset(Spec):
    """One user-uploaded source file.

    Identified by storage keys rather than filesystem paths so the record
    survives the backend moving off this machine.
    """

    asset_id: str
    project_id: str
    storage_key: str
    kind: AssetKind = "video"
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool = True
    original_name: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    # Normalised 540p copy used for analysis so every downstream step sees the
    # same resolution and frame rate regardless of what the phone produced.
    proxy_key: str | None = None


class RawSegment(Spec):
    """A shot boundary result, before any semantic analysis."""

    segment_id: str
    project_id: str
    asset_id: str
    kind: AssetKind = "video"
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


class MaterialSegment(VersionedSpec):
    """A fully analysed candidate segment - one entry in the candidate pool.

    Fields fall into three groups:

    * deterministic (``sharpness`` ... ``technical_quality``) - computed with
      OpenCV, free and reproducible;
    * model-derived (``semantic`` ... ``emotion``) - from the material VLM;
    * bookkeeping (``is_usable``, ``reject_reason``) - so we can always answer
      "why was this clip never used?".
    """

    segment_id: str
    project_id: str
    source_id: str  # asset_id
    kind: AssetKind = "video"
    start: float
    end: float

    # --- semantics (model) ---
    semantic: str | None = None
    semantic_role: SemanticRole = "unknown"
    shot_type: ShotType = "unknown"
    camera_motion: CameraMotion = "unknown"
    people: int | None = None
    face_quality: float | None = None

    # --- technical quality (deterministic) ---
    technical_quality: float | None = None
    sharpness: float | None = None
    exposure_score: float | None = None
    stability_score: float | None = None
    motion_quality: float | None = None
    # Left null until a VLM scores composition; do not fake it with a heuristic.
    composition_score: float | None = None

    emotion: list[str] = Field(default_factory=list)
    interestingness: float | None = None

    # --- audio ---
    has_speech: bool = False
    asr_text: str | None = None
    audio_rms: float | None = None

    # --- retrieval ---
    keyframe_keys: list[str] = Field(default_factory=list)
    embedding_dim: int | None = None

    # --- usability ---
    is_usable: bool = True
    reject_reason: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start
