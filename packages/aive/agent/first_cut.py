"""Translate a validated Director decision into the shared Patch v1 protocol."""

from __future__ import annotations

from hashlib import sha256
from typing import Mapping

from ..contracts.captions import (
    CLEAN_CAPTION_COMPONENT,
    CLEAN_CAPTION_VERSION,
    default_caption_style,
)
from ..contracts.director import AgentRunContext, FirstCutDecision
from ..contracts.moments import MomentEvidence
from ..contracts.patch import Patch, PatchScope
from ..contracts.patch_project import (
    AddTrack,
    SetCreativePlan,
    SetDependencies,
    SetTimelineDuration,
)
from ..contracts.project_doc import (
    CreativePlan,
    DependencyRef,
    PlanSection,
    ProjectDoc,
)
from ..contracts.time import FrameRange, SourceRange
from ..contracts.timeline import (
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionTrack,
    ImageClip,
    VideoClip,
    VideoTrack,
)


PHOTO_HOLD_SECONDS = 3


class FirstCutBuildError(ValueError):
    pass


def build_first_cut_explanation(
    context: AgentRunContext,
    decision: FirstCutDecision,
) -> str:
    """Explain selected evidence and planned strategies without inventing edits."""

    moments = {moment.moment_id: moment for moment in context.moments}
    assets = {asset.asset_id: asset for asset in context.assets}
    selected = [moments[moment_id] for moment_id in decision.moment_ids if moment_id in moments]
    asset_count = len({moment.asset_id for moment in selected})
    audible = sum(
        1
        for moment in selected
        if (asset := assets.get(moment.asset_id)) is not None and asset.has_audio
    )
    caption_count = sum(1 for moment in selected if moment.asr_text)
    parts = [
        f"Selected {len(selected)} analyzed Moment(s) from {asset_count} source Asset(s).",
        f"Creative direction: {decision.creative_strategy.objective}",
    ]
    if audible:
        parts.append(f"Preserved source audio for {audible} selected video Moment(s).")
    if decision.copy_strategy.title:
        parts.append(f"Draft title: {decision.copy_strategy.title}")
    if decision.copy_strategy.caption_mode != "none":
        if caption_count:
            parts.append(
                f"Applied {caption_count} {decision.copy_strategy.caption_mode} "
                "caption cue(s) from ASR evidence."
            )
        else:
            parts.append("No caption track was added because no selected Moment had ASR text.")
    if decision.music_strategy.mode == "music_bed":
        parts.append(
            f"Added authorized music Asset {decision.music_strategy.asset_id} with "
            f"{decision.music_strategy.gain_db:g} dB gain"
            + (
                " and speech ducking."
                if decision.music_strategy.duck_under_speech
                else "."
            )
        )
    else:
        parts.append(f"Audio strategy: {decision.music_strategy.mode}.")
    return " ".join(parts)[:4_000]


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
    """Build one executable, gapless first-cut Patch from selected Moments."""

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
    by_moment = {moment.moment_id: moment for moment in moments}
    by_asset = {asset.asset_id: asset for asset in document.assets}
    target_frames = document.intent.target_duration_frames
    fps = document.timeline.output.fps
    cursor = 0
    visual_clips: list[VideoClip | ImageClip] = []
    audio_clips: list[AudioClip] = []
    caption_cues: list[CaptionCue] = []
    has_speech_audio = False
    sections: list[PlanSection] = []

    for ordinal, moment_id in enumerate(decision.moment_ids):
        moment = by_moment.get(moment_id)
        if moment is None:
            raise FirstCutBuildError(
                f"Director selected unknown Moment {moment_id!r}"
            )
        if moment.project_id != document.project_id:
            raise FirstCutBuildError(
                f"Moment {moment_id!r} belongs to another project"
            )
        if not moment.is_usable:
            raise FirstCutBuildError(f"Moment {moment_id!r} is not usable")
        asset = by_asset.get(moment.asset_id)
        if asset is None or "source" not in asset.roles:
            raise FirstCutBuildError(
                f"Moment {moment_id!r} does not reference a declared source asset"
            )
        remaining = target_frames - cursor
        if remaining <= 0:
            break

        if asset.kind == "video":
            available = _source_frames(moment, fps.num, fps.den)
            duration = min(available, remaining)
            if duration <= 0:
                continue
            source = _frame_exact_source(moment, duration, fps.num, fps.den)
            output = FrameRange(start_frame=cursor, duration_frames=duration)
            clip_id = _stable_id("clip", identity_seed, moment_id, str(ordinal))
            visual_clips.append(
                VideoClip(
                    clip_id=clip_id,
                    asset_id=asset.asset_id,
                    source=source,
                    output=output,
                )
            )
            if asset_has_audio.get(asset.asset_id, False):
                audio_clips.append(
                    AudioClip(
                        clip_id=_stable_id(
                            "audio", identity_seed, moment_id, str(ordinal)
                        ),
                        asset_id=asset.asset_id,
                        source=source,
                        output=output,
                    )
                )
                has_speech_audio = has_speech_audio or moment.has_speech
            if (
                decision.copy_strategy.caption_mode != "none"
                and moment.asr_text
            ):
                caption_cues.append(
                    CaptionCue(
                        cue_id=_stable_id(
                            "caption", identity_seed, moment_id, str(ordinal)
                        ),
                        output=output,
                        text=moment.asr_text[:4_000],
                        source="asr",
                    )
                )
        elif asset.kind == "photo":
            nominal = max(1, PHOTO_HOLD_SECONDS * fps.num // fps.den)
            duration = min(nominal, remaining)
            output = FrameRange(start_frame=cursor, duration_frames=duration)
            clip_id = _stable_id("clip", identity_seed, moment_id, str(ordinal))
            visual_clips.append(
                ImageClip(
                    clip_id=clip_id,
                    asset_id=asset.asset_id,
                    output=output,
                )
            )
        else:
            raise FirstCutBuildError(
                f"source asset {asset.asset_id!r} cannot be used as a visual"
            )

        sections.append(
            PlanSection(
                section_id=_stable_id(
                    "section", identity_seed, moment_id, str(ordinal)
                ),
                output=output,
                role=moment.semantic_role or "source_moment",
                goal=(moment.semantic or moment.asr_text or "Use selected Moment")[:1_000],
            )
        )
        cursor += duration

    if not visual_clips or cursor <= 0:
        raise FirstCutBuildError("Director decision produced no renderable visual clips")

    video_track_id = _stable_id("track", identity_seed, "primary")
    operations = [
        AddTrack(
            operation_id=_stable_id("op", identity_seed, "add-primary"),
            track=VideoTrack(
                track_id=video_track_id,
                role="primary",
                clips=tuple(visual_clips),
            ),
        )
    ]
    if audio_clips:
        operations.append(
            AddTrack(
                operation_id=_stable_id(
                    "op", identity_seed, "add-source-audio"
                ),
                track=AudioTrack(
                    track_id=_stable_id("track", identity_seed, "source-audio"),
                    role="source",
                    clips=tuple(audio_clips),
                ),
            )
        )
    if caption_cues:
        components = document.dependencies.components
        caption_dependency = DependencyRef(
            name=CLEAN_CAPTION_COMPONENT,
            version=CLEAN_CAPTION_VERSION,
        )
        if caption_dependency not in components:
            operations.append(
                SetDependencies(
                    operation_id=_stable_id(
                        "op", identity_seed, "install-caption-component"
                    ),
                    dependencies=document.dependencies.model_copy(
                        update={"components": components + (caption_dependency,)}
                    ),
                )
            )
        operations.append(
            AddTrack(
                operation_id=_stable_id("op", identity_seed, "add-captions"),
                track=CaptionTrack(
                    track_id=_stable_id("track", identity_seed, "captions"),
                    style=default_caption_style(
                        _stable_id("caption-style", identity_seed, "clean")
                    ),
                    cues=tuple(caption_cues),
                ),
            )
        )
    if decision.music_strategy.mode == "music_bed":
        durations = asset_duration_seconds or {}
        music_asset_id = decision.music_strategy.asset_id
        music_asset = by_asset.get(music_asset_id or "")
        if music_asset is None or "music" not in music_asset.roles:
            raise FirstCutBuildError(
                "music-bed strategy requires the selected authorized music Asset"
            )
        if not asset_has_audio.get(music_asset.asset_id, False):
            raise FirstCutBuildError("selected music Asset has no audio stream")
        music_duration = durations.get(music_asset.asset_id)
        if music_duration is None or music_duration <= 0:
            raise FirstCutBuildError("selected music Asset has no usable duration")
        music_clips = _music_bed_clips(
            identity_seed=identity_seed,
            asset_id=music_asset.asset_id,
            asset_duration_seconds=music_duration,
            timeline_duration_frames=cursor,
            fps_num=fps.num,
            fps_den=fps.den,
            gain_db=decision.music_strategy.gain_db,
            fade_in_frames=decision.music_strategy.fade_in_frames,
            fade_out_frames=decision.music_strategy.fade_out_frames,
            duck_under_speech=(
                decision.music_strategy.duck_under_speech and has_speech_audio
            ),
        )
        operations.append(
            AddTrack(
                operation_id=_stable_id("op", identity_seed, "add-music"),
                track=AudioTrack(
                    track_id=_stable_id("track", identity_seed, "music"),
                    role="music",
                    clips=music_clips,
                ),
            )
        )
    operations.extend(
        (
            SetCreativePlan(
                operation_id=_stable_id("op", identity_seed, "creative-plan"),
                creative_plan=CreativePlan(
                    summary=_creative_plan_summary(decision),
                    sections=tuple(sections),
                ),
            ),
            SetTimelineDuration(
                operation_id=_stable_id("op", identity_seed, "duration"),
                duration_frames=cursor,
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


def _source_frames(moment: MomentEvidence, fps_num: int, fps_den: int) -> int:
    ticks = moment.source.end - moment.source.start
    return ticks * fps_num // (moment.source.timescale * fps_den)


def _frame_exact_source(
    moment: MomentEvidence,
    duration_frames: int,
    fps_num: int,
    fps_den: int,
) -> SourceRange:
    """Rescale ticks so a one-times playback maps to an exact frame count."""

    timescale = moment.source.timescale * fps_num
    start = moment.source.start * fps_num
    end = start + duration_frames * moment.source.timescale * fps_den
    if end > moment.source.end * fps_num:
        raise FirstCutBuildError(
            f"Moment {moment.moment_id!r} cannot supply the requested frames"
        )
    return SourceRange(start=start, end=end, timescale=timescale)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _creative_plan_summary(decision: FirstCutDecision) -> str:
    parts = [
        decision.creative_strategy.objective,
        f"Pacing: {decision.creative_strategy.pacing}.",
    ]
    if decision.creative_strategy.hook:
        parts.append(f"Hook: {decision.creative_strategy.hook}")
    if decision.copy_strategy.title:
        parts.append(f"Draft title: {decision.copy_strategy.title}")
    parts.append(f"Caption strategy: {decision.copy_strategy.caption_mode}.")
    parts.append(f"Music strategy: {decision.music_strategy.mode}.")
    return " ".join(parts)[:4_000]


def _music_bed_clips(
    *,
    identity_seed: str,
    asset_id: str,
    asset_duration_seconds: float,
    timeline_duration_frames: int,
    fps_num: int,
    fps_den: int,
    gain_db: float,
    fade_in_frames: int,
    fade_out_frames: int,
    duck_under_speech: bool,
) -> tuple[AudioClip, ...]:
    available_frames = int(asset_duration_seconds * fps_num / fps_den)
    if available_frames <= 0:
        raise FirstCutBuildError("selected music Asset is shorter than one frame")
    clips: list[AudioClip] = []
    cursor = 0
    ordinal = 0
    while cursor < timeline_duration_frames:
        duration = min(available_frames, timeline_duration_frames - cursor)
        clips.append(
            AudioClip(
                clip_id=_stable_id(
                    "music", identity_seed, asset_id, str(ordinal)
                ),
                asset_id=asset_id,
                source=SourceRange(
                    start=0,
                    end=duration * fps_den,
                    timescale=fps_num,
                ),
                output=FrameRange(start_frame=cursor, duration_frames=duration),
                gain_db=gain_db,
                fade_in_frames=min(fade_in_frames, duration) if ordinal == 0 else 0,
                fade_out_frames=0,
                ducking_group="speech" if duck_under_speech else None,
            )
        )
        cursor += duration
        ordinal += 1
    last = clips[-1]
    clips[-1] = last.model_copy(
        update={
            "fade_out_frames": min(fade_out_frames, last.output.duration_frames)
        }
    )
    return tuple(clips)
