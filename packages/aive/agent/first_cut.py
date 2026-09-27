"""Literal translation of one validated Edit Blueprint into Patch v1."""

from __future__ import annotations

from hashlib import sha256
from typing import Mapping

from ..contracts.captions import CLEAN_CAPTION_COMPONENT, CLEAN_CAPTION_VERSION
from ..contracts.director import AgentRunContext, FirstCutDecision
from ..contracts.moments import MomentEvidence
from ..contracts.patch import Patch, PatchScope
from ..contracts.patch_project import (
    AddTrack,
    SetCreativePlan,
    SetDependencies,
    SetTimelineDuration,
)
from ..contracts.project_doc import CreativePlan, DependencyRef, PlanSection, ProjectDoc
from ..contracts.time import SourceRange
from ..contracts.timeline import (
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionTrack,
    ImageClip,
    VideoClip,
    VideoTrack,
)


class FirstCutBuildError(ValueError):
    pass


def build_first_cut_explanation(
    context: AgentRunContext,
    decision: FirstCutDecision,
) -> str:
    """Return the Director's user-facing explanation without policy prose."""

    del context
    return decision.explanation


def build_first_cut_patch(
    *,
    document: ProjectDoc,
    moments: tuple[MomentEvidence, ...],
    decision: FirstCutDecision,
    run_id: str,
    request_id: str,
    asset_has_audio: Mapping[str, bool],
    asset_duration_seconds: Mapping[str, float] | None = None,
    candidate_attempt: int = 0,
) -> Patch:
    """Copy explicit model choices into executable contracts; invent nothing."""

    if document.intent.task_mode != "autonomous":
        raise FirstCutBuildError("first-cut Director supports autonomous projects only")
    if document.timeline.tracks:
        raise FirstCutBuildError("first cut requires an empty base timeline")
    if candidate_attempt < 0 or candidate_attempt > 3:
        raise FirstCutBuildError("candidate attempt must be between 0 and 3")

    identity_seed = (
        run_id
        if candidate_attempt == 0
        else f"{run_id}:render-autofix:{candidate_attempt}"
    )
    blueprint = decision.edit_blueprint
    moments_by_id = {moment.moment_id: moment for moment in moments}
    assets_by_id = {asset.asset_id: asset for asset in document.assets}
    durations = asset_duration_seconds or {}
    visual_clips: list[VideoClip | ImageClip] = []
    source_audio: list[AudioClip] = []

    for visual in blueprint.visuals:
        moment = moments_by_id.get(visual.moment_id)
        if moment is None or moment.project_id != document.project_id:
            raise FirstCutBuildError(
                f"Director selected unknown Moment {visual.moment_id!r}"
            )
        asset = assets_by_id.get(moment.asset_id)
        if asset is None or "source" not in asset.roles:
            raise FirstCutBuildError(
                f"Moment {visual.moment_id!r} is not backed by a source Asset"
            )
        clip_id = _stable_id("clip", identity_seed, visual.decision_key)
        if asset.kind == "video":
            if visual.source is None:
                raise FirstCutBuildError(
                    f"video decision {visual.decision_key!r} requires a source range"
                )
            _require_source_inside_moment(visual.source, moment)
            source = SourceRange(**visual.source.model_dump())
            visual_clips.append(
                VideoClip(
                    clip_id=clip_id,
                    asset_id=asset.asset_id,
                    source=source,
                    output=visual.output,
                    playback=visual.playback,
                    crop=visual.crop,
                    reframe=visual.reframe,
                    transition_out=visual.transition_out,
                )
            )
            if visual.preserve_source_audio:
                if not asset_has_audio.get(asset.asset_id, False):
                    raise FirstCutBuildError(
                        f"decision {visual.decision_key!r} preserves unavailable audio"
                    )
                source_audio.append(
                    AudioClip(
                        clip_id=_stable_id(
                            "audio", identity_seed, visual.decision_key
                        ),
                        asset_id=asset.asset_id,
                        source=source,
                        output=visual.output,
                        playback=visual.playback,
                    )
                )
        elif asset.kind == "photo":
            if visual.source is not None:
                raise FirstCutBuildError(
                    f"photo decision {visual.decision_key!r} cannot name a source range"
                )
            if visual.preserve_source_audio:
                raise FirstCutBuildError(
                    f"photo decision {visual.decision_key!r} cannot preserve source audio"
                )
            visual_clips.append(
                ImageClip(
                    clip_id=clip_id,
                    asset_id=asset.asset_id,
                    output=visual.output,
                    crop=visual.crop,
                    reframe=visual.reframe,
                    transition_out=visual.transition_out,
                )
            )
        else:
            raise FirstCutBuildError(
                f"source Asset {asset.asset_id!r} cannot be used as a visual"
            )

    operations = [
        AddTrack(
            operation_id=_stable_id("op", identity_seed, "add-primary"),
            track=VideoTrack(
                track_id=_stable_id("track", identity_seed, "primary"),
                role="primary",
                clips=tuple(visual_clips),
            ),
        )
    ]
    if source_audio:
        operations.append(
            AddTrack(
                operation_id=_stable_id("op", identity_seed, "add-source-audio"),
                track=AudioTrack(
                    track_id=_stable_id("track", identity_seed, "source-audio"),
                    role="source",
                    clips=tuple(source_audio),
                ),
            )
        )

    if blueprint.captions:
        assert blueprint.caption_style is not None
        _authorize_caption_component(document, blueprint.caption_style)
        dependency = DependencyRef(
            name=blueprint.caption_style.component_name,
            version=blueprint.caption_style.component_version,
        )
        if dependency not in document.dependencies.components:
            operations.append(
                SetDependencies(
                    operation_id=_stable_id(
                        "op", identity_seed, "install-caption-component"
                    ),
                    dependencies=document.dependencies.model_copy(
                        update={
                            "components": document.dependencies.components
                            + (dependency,)
                        }
                    ),
                )
            )
        operations.append(
            AddTrack(
                operation_id=_stable_id("op", identity_seed, "add-captions"),
                track=CaptionTrack(
                    track_id=_stable_id("track", identity_seed, "captions"),
                    style=blueprint.caption_style,
                    cues=tuple(
                        CaptionCue(
                            cue_id=_stable_id(
                                "caption", identity_seed, caption.decision_key
                            ),
                            output=caption.output,
                            text=caption.text,
                            source=caption.source,
                            speaker=caption.speaker,
                        )
                        for caption in blueprint.captions
                    ),
                ),
            )
        )

    if blueprint.music:
        music_clips: list[AudioClip] = []
        for music in blueprint.music:
            asset = assets_by_id.get(music.asset_id)
            if asset is None or "music" not in asset.roles:
                raise FirstCutBuildError(
                    f"music decision names unauthorized Asset {music.asset_id!r}"
                )
            if not asset_has_audio.get(asset.asset_id, False):
                raise FirstCutBuildError(
                    f"music Asset {asset.asset_id!r} has no audio stream"
                )
            duration = durations.get(asset.asset_id)
            if duration is None or duration <= 0:
                raise FirstCutBuildError(
                    f"music Asset {asset.asset_id!r} has no usable duration"
                )
            if music.source.end / music.source.timescale > duration + 1e-6:
                raise FirstCutBuildError(
                    f"music decision {music.decision_key!r} exceeds its Asset"
                )
            music_clips.append(
                AudioClip(
                    clip_id=_stable_id(
                        "music", identity_seed, music.decision_key
                    ),
                    asset_id=music.asset_id,
                    source=music.source,
                    output=music.output,
                    gain_db=music.gain_db,
                    fade_in_frames=music.fade_in_frames,
                    fade_out_frames=music.fade_out_frames,
                    ducking_group="speech" if music.duck_under_speech else None,
                )
            )
        operations.append(
            AddTrack(
                operation_id=_stable_id("op", identity_seed, "add-music"),
                track=AudioTrack(
                    track_id=_stable_id("track", identity_seed, "music"),
                    role="music",
                    clips=tuple(music_clips),
                ),
            )
        )

    if blueprint.sound_effects:
        sfx_clips: list[AudioClip] = []
        for effect in blueprint.sound_effects:
            asset = assets_by_id.get(effect.asset_id)
            if asset is None or "sfx" not in asset.roles:
                raise FirstCutBuildError(
                    f"sound-effect decision names unauthorized Asset {effect.asset_id!r}"
                )
            if not asset_has_audio.get(asset.asset_id, False):
                raise FirstCutBuildError(
                    f"sound-effect Asset {asset.asset_id!r} has no audio stream"
                )
            duration = durations.get(asset.asset_id)
            if duration is None or duration <= 0:
                raise FirstCutBuildError(
                    f"sound-effect Asset {asset.asset_id!r} has no usable duration"
                )
            if effect.source.end / effect.source.timescale > duration + 1e-6:
                raise FirstCutBuildError(
                    f"sound-effect decision {effect.decision_key!r} exceeds its Asset"
                )
            sfx_clips.append(
                AudioClip(
                    clip_id=_stable_id(
                        "sfx", identity_seed, effect.decision_key
                    ),
                    asset_id=effect.asset_id,
                    source=effect.source,
                    output=effect.output,
                    gain_db=effect.gain_db,
                    fade_in_frames=effect.fade_in_frames,
                    fade_out_frames=effect.fade_out_frames,
                )
            )
        operations.append(
            AddTrack(
                operation_id=_stable_id("op", identity_seed, "add-sfx"),
                track=AudioTrack(
                    track_id=_stable_id("track", identity_seed, "sfx"),
                    role="sfx",
                    clips=tuple(sfx_clips),
                ),
            )
        )

    operations.extend(
        (
            SetCreativePlan(
                operation_id=_stable_id("op", identity_seed, "creative-plan"),
                creative_plan=CreativePlan(
                    summary=blueprint.narrative_summary,
                    sections=tuple(
                        PlanSection(
                            section_id=_stable_id(
                                "section", identity_seed, section.section_key
                            ),
                            output=section.output,
                            role=section.role,
                            goal=section.goal,
                        )
                        for section in blueprint.sections
                    ),
                ),
            ),
            SetTimelineDuration(
                operation_id=_stable_id("op", identity_seed, "duration"),
                duration_frames=blueprint.output_duration_frames,
            ),
        )
    )
    return Patch(
        patch_id=_stable_id("patch", identity_seed),
        project_id=document.project_id,
        base_revision_id=document.revision_id,
        idempotency_key=_stable_id("director-commit", identity_seed),
        origin="agent",
        request_id=request_id,
        summary=decision.summary,
        scope=PatchScope(kind="global"),
        operations=tuple(operations),
    )


def _require_source_inside_moment(source, moment: MomentEvidence) -> None:
    if (
        source.timescale != moment.source.timescale
        or source.start < moment.source.start
        or source.end > moment.source.end
    ):
        raise FirstCutBuildError(
            f"Director source range is outside Moment {moment.moment_id!r}"
        )


def _authorize_caption_component(document: ProjectDoc, style) -> None:
    dependency = DependencyRef(
        name=style.component_name,
        version=style.component_version,
    )
    if dependency in document.dependencies.components:
        return
    if (
        style.component_name != CLEAN_CAPTION_COMPONENT
        or style.component_version != CLEAN_CAPTION_VERSION
    ):
        raise FirstCutBuildError(
            "caption style must reference an authorized component dependency"
        )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"
