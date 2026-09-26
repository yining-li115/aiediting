"""ProjectDoc v1: the only authoritative description of an edited project.

The document contains creative intent and executable timeline state. Media
bytes, analysis caches, job state, and provider handles are referenced rather
than embedded. Persist each saved document as an immutable project revision.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .reference import ReferenceContext, ReferencePolicy
from .time import (
    ConstantPlayback,
    FrameRange,
    Rational,
    SourceRange,
    VariablePlayback,
)
from .timeline import (
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionStyle,
    CaptionTrack,
    GraphicClip,
    GraphicTrack,
    ImageClip,
    MotionSpec,
    OutputSettings,
    Timeline,
    TransitionSpec,
    VideoClip,
    VideoTrack,
)


PROJECT_DOC_SCHEMA_VERSION = "1.0"

TaskMode = Literal["autonomous", "reference_remake"]
AssetKind = Literal["video", "photo", "audio"]
AssetRole = Literal[
    "source",
    "reference",
    "style_reference",
    "remake_reference",
    "music",
    "voiceover",
    "sfx",
]


class ProjectIntent(ContractModel):
    task_mode: TaskMode
    target_duration_frames: int = Field(gt=0)
    prompt: str | None = Field(default=None, max_length=8_000)
    explicit_requirements: tuple[str, ...] = ()
    style_tags: tuple[str, ...] = ()


class AssetRef(ContractModel):
    asset_id: StableId
    kind: AssetKind
    roles: tuple[AssetRole, ...] = ("source",)
    content_hash: str | None = Field(default=None, min_length=16, max_length=128)
    label: str | None = Field(default=None, max_length=255)


class DependencyRef(ContractModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    content_hash: str | None = Field(default=None, min_length=16, max_length=128)


class DependencySet(ContractModel):
    renderer: DependencyRef
    skills: tuple[DependencyRef, ...] = ()
    components: tuple[DependencyRef, ...] = ()
    analysis_models: tuple[DependencyRef, ...] = ()


class PlanSection(ContractModel):
    section_id: StableId
    output: FrameRange
    role: str = Field(min_length=1, max_length=128)
    goal: str | None = Field(default=None, max_length=1_000)


class CreativePlan(ContractModel):
    summary: str | None = Field(default=None, max_length=4_000)
    sections: tuple[PlanSection, ...] = ()


LockProtection = Literal[
    "content",
    "source_range",
    "position",
    "duration",
    "audio",
    "style",
    "deletion",
]


class Lock(ContractModel):
    lock_id: StableId
    target_type: Literal["project", "section", "track", "clip", "caption", "graphic"]
    target_id: StableId
    protect: tuple[LockProtection, ...] = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)


class ProjectDoc(ContractModel):
    """Complete, versioned project snapshot stored in one project revision."""

    schema_version: Literal["1.0"] = PROJECT_DOC_SCHEMA_VERSION
    project_id: StableId
    revision_id: StableId
    parent_revision_id: StableId | None = None
    intent: ProjectIntent
    assets: tuple[AssetRef, ...] = ()
    creative_plan: CreativePlan | None = None
    timeline: Timeline
    locks: tuple[Lock, ...] = ()
    reference_context: ReferenceContext | None = None
    dependencies: DependencySet

    @model_validator(mode="after")
    def validate_document_graph(self) -> ProjectDoc:
        if self.parent_revision_id == self.revision_id:
            raise ValueError("a revision cannot be its own parent")

        assets = self._unique_map(self.assets, "asset_id", "asset")
        tracks = self._unique_map(self.timeline.tracks, "track_id", "track")
        elements = self._elements()
        self._require_unique_ids(elements)

        for element_type, element in elements:
            output = element.output
            if output.end_frame > self.timeline.duration_frames:
                raise ValueError(
                    f"{element_type} {self._element_id(element)!r} exceeds "
                    "timeline duration"
                )
            asset_id = getattr(element, "asset_id", None)
            if asset_id is not None:
                asset = assets.get(asset_id)
                if asset is None:
                    raise ValueError(
                        f"{element_type} {self._element_id(element)!r} references "
                        f"unknown asset {asset_id!r}"
                    )
                self._validate_asset_kind(element, asset)
            if isinstance(element, (VideoClip, AudioClip)):
                self._validate_playback(element)
            if isinstance(element, (VideoClip, ImageClip)):
                if (
                    element.transition_out is not None
                    and element.transition_out.duration_frames
                    >= element.output.duration_frames
                ):
                    raise ValueError(
                        f"clip {element.clip_id!r} transition must be shorter than the clip"
                    )
            if isinstance(element, AudioClip):
                if element.fade_in_frames > element.output.duration_frames:
                    raise ValueError(f"clip {element.clip_id!r} fade-in is too long")
                if element.fade_out_frames > element.output.duration_frames:
                    raise ValueError(f"clip {element.clip_id!r} fade-out is too long")

        sections: dict[str, PlanSection] = {}
        if self.creative_plan is not None:
            sections = self._unique_map(
                self.creative_plan.sections, "section_id", "section"
            )
            for section in sections.values():
                if section.output.end_frame > self.timeline.duration_frames:
                    raise ValueError(
                        f"section {section.section_id!r} exceeds timeline duration"
                    )

        self._validate_locks(tracks, elements, sections)
        self._validate_reference(assets, elements)
        self._validate_dependency_names()
        return self

    @staticmethod
    def _unique_map(items: tuple, attr: str, label: str) -> dict[str, object]:
        result: dict[str, object] = {}
        for item in items:
            value = getattr(item, attr)
            if value in result:
                raise ValueError(f"duplicate {label} id {value!r}")
            result[value] = item
        return result

    def _elements(self) -> list[tuple[str, object]]:
        elements: list[tuple[str, object]] = []
        for track in self.timeline.tracks:
            if isinstance(track, VideoTrack):
                elements.extend(("clip", clip) for clip in track.clips)
            elif isinstance(track, AudioTrack):
                elements.extend(("clip", clip) for clip in track.clips)
            elif isinstance(track, CaptionTrack):
                elements.extend(("caption", cue) for cue in track.cues)
            elif isinstance(track, GraphicTrack):
                elements.extend(("graphic", graphic) for graphic in track.graphics)
        return elements

    @staticmethod
    def _element_id(element: object) -> str:
        for attr in ("clip_id", "cue_id", "graphic_id"):
            value = getattr(element, attr, None)
            if value is not None:
                return value
        raise TypeError(f"unsupported timeline element {type(element).__name__}")

    def _require_unique_ids(self, elements: list[tuple[str, object]]) -> None:
        seen: set[str] = set()
        for _, element in elements:
            element_id = self._element_id(element)
            if element_id in seen:
                raise ValueError(f"duplicate element id {element_id!r}")
            seen.add(element_id)

    @staticmethod
    def _validate_asset_kind(element: object, asset: AssetRef) -> None:
        if isinstance(element, VideoClip) and asset.kind != "video":
            raise ValueError(f"video clip requires a video asset, got {asset.kind}")
        if isinstance(element, ImageClip) and asset.kind != "photo":
            raise ValueError(f"image clip requires a photo asset, got {asset.kind}")
        if isinstance(element, AudioClip) and asset.kind not in {"audio", "video"}:
            raise ValueError(f"audio clip requires an audio or video asset, got {asset.kind}")

    def _validate_playback(self, clip: VideoClip | AudioClip) -> None:
        if isinstance(clip.playback, ConstantPlayback):
            source_ticks = clip.source.duration_ticks
            fps = self.timeline.output.fps
            rate = clip.playback.rate
            left = source_ticks * fps.num * rate.den
            right = (
                clip.source.timescale
                * clip.output.duration_frames
                * fps.den
                * rate.num
            )
            if left != right:
                raise ValueError(
                    f"clip {clip.clip_id!r} playback rate does not match its "
                    "source and output durations"
                )
        elif isinstance(clip.playback, VariablePlayback):
            last = clip.playback.points[-1]
            if last.output_frame != clip.output.duration_frames:
                raise ValueError(
                    f"clip {clip.clip_id!r} variable playback must end at its "
                    "output duration"
                )
            if last.source_tick != clip.source.duration_ticks:
                raise ValueError(
                    f"clip {clip.clip_id!r} variable playback must end at its "
                    "source duration"
                )

    def _validate_locks(
        self,
        tracks: dict[str, object],
        elements: list[tuple[str, object]],
        sections: dict[str, PlanSection],
    ) -> None:
        self._unique_map(self.locks, "lock_id", "lock")
        element_ids: dict[str, set[str]] = {
            "clip": set(),
            "caption": set(),
            "graphic": set(),
        }
        for element_type, element in elements:
            element_ids[element_type].add(self._element_id(element))
        for lock in self.locks:
            if lock.target_type == "project" and lock.target_id != self.project_id:
                raise ValueError(f"lock {lock.lock_id!r} targets an unknown project")
            if lock.target_type == "track" and lock.target_id not in tracks:
                raise ValueError(f"lock {lock.lock_id!r} targets an unknown track")
            if lock.target_type == "section" and lock.target_id not in sections:
                raise ValueError(f"lock {lock.lock_id!r} targets an unknown section")
            if (
                lock.target_type in element_ids
                and lock.target_id not in element_ids[lock.target_type]
            ):
                raise ValueError(
                    f"lock {lock.lock_id!r} targets an unknown {lock.target_type}"
                )

    def _validate_reference(
        self,
        assets: dict[str, object],
        elements: list[tuple[str, object]],
    ) -> None:
        context = self.reference_context
        if context is None:
            return
        if self.intent.task_mode != "reference_remake":
            raise ValueError("reference_context requires reference_remake task mode")
        reference_asset = assets.get(context.reference_asset_id)
        if not isinstance(reference_asset, AssetRef) or reference_asset.kind != "video":
            raise ValueError("reference asset must be a declared video asset")
        element_ids = {self._element_id(element) for _, element in elements}
        alignment_ids: set[str] = set()
        for alignment in context.alignments:
            if alignment.alignment_id in alignment_ids:
                raise ValueError(f"duplicate alignment id {alignment.alignment_id!r}")
            alignment_ids.add(alignment.alignment_id)
            if alignment.output_range.end_frame > self.timeline.duration_frames:
                raise ValueError(
                    f"alignment {alignment.alignment_id!r} exceeds timeline duration"
                )
            missing = set(alignment.output_element_ids) - element_ids
            if missing:
                raise ValueError(
                    f"alignment {alignment.alignment_id!r} references unknown "
                    f"output elements {sorted(missing)!r}"
                )

    def _validate_dependency_names(self) -> None:
        for label, dependencies in (
            ("skill", self.dependencies.skills),
            ("component", self.dependencies.components),
            ("analysis model", self.dependencies.analysis_models),
        ):
            names = [dependency.name for dependency in dependencies]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate {label} dependency name")


__all__ = [
    "PROJECT_DOC_SCHEMA_VERSION",
    "AssetRef",
    "AudioClip",
    "AudioTrack",
    "CaptionCue",
    "CaptionStyle",
    "CaptionTrack",
    "ConstantPlayback",
    "CreativePlan",
    "DependencyRef",
    "DependencySet",
    "FrameRange",
    "GraphicClip",
    "GraphicTrack",
    "ImageClip",
    "Lock",
    "OutputSettings",
    "PlanSection",
    "ProjectDoc",
    "ProjectIntent",
    "Rational",
    "ReferenceContext",
    "ReferencePolicy",
    "SourceRange",
    "Timeline",
    "VariablePlayback",
    "VideoClip",
    "VideoTrack",
]
