"""Deterministic legality review for model-authored Edit Blueprints."""

from __future__ import annotations

from typing import Protocol

from ..contracts.director import (
    AgentRunContext,
    DirectorReview,
    DirectorReviewIssue,
    EditBlueprint,
    FirstCutDecision,
)
from ..contracts.time import FrameRange, Rational, SourceRange


class CreativePlanReviewerProvider(Protocol):
    """Model boundary for narrative, rhythm, emotion, and aesthetic review."""

    name: str
    version: str

    async def review_edit_blueprint(
        self,
        context: AgentRunContext,
        decision: FirstCutDecision,
    ) -> DirectorReview: ...


class FirstCutReviewer:
    """Validate identity, scope, timing, and declared project constraints only."""

    def review(
        self,
        context: AgentRunContext,
        decision: FirstCutDecision,
    ) -> DirectorReview:
        moments = {moment.moment_id: moment for moment in context.moments}
        assets = {asset.asset_id: asset for asset in context.assets}
        blueprint = decision.edit_blueprint
        issues: list[DirectorReviewIssue] = []

        for visual in blueprint.visuals:
            moment = moments.get(visual.moment_id)
            if moment is None:
                issues.append(
                    _error(
                        "unknown_moment",
                        f"Moment {visual.moment_id!r} is not in the frozen context.",
                        visual.moment_id,
                    )
                )
                continue
            asset = assets.get(moment.asset_id)
            if asset is None or "source" not in asset.roles:
                issues.append(
                    _error(
                        "unauthorized_source",
                        f"Moment {visual.moment_id!r} is not backed by a source Asset.",
                        visual.moment_id,
                    )
                )
                continue
            if asset.kind == "video":
                if visual.source is None:
                    issues.append(
                        _error(
                            "video_source_required",
                            f"Video Moment {visual.moment_id!r} requires a source range.",
                            visual.moment_id,
                        )
                    )
                elif (
                    visual.source.timescale != moment.source.timescale
                    or visual.source.start < moment.source.start
                    or visual.source.end > moment.source.end
                ):
                    issues.append(
                        _error(
                            "source_outside_moment",
                            f"Source range for {visual.moment_id!r} is outside its evidence.",
                            visual.moment_id,
                        )
                    )
                if visual.preserve_source_audio and not asset.has_audio:
                    issues.append(
                        _error(
                            "source_audio_unavailable",
                            f"Asset {asset.asset_id!r} has no source audio to preserve.",
                            visual.moment_id,
                        )
                    )
            elif visual.source is not None or visual.preserve_source_audio:
                issues.append(
                    _error(
                        "photo_source_invalid",
                        f"Photo Moment {visual.moment_id!r} cannot use a source range or audio.",
                        visual.moment_id,
                    )
                )

        if context.intent.caption_policy == "none" and blueprint.captions:
            issues.append(
                _error(
                    "captions_disabled",
                    "Project intent explicitly disables captions.",
                )
            )
        for caption in blueprint.captions:
            unknown = set(caption.evidence_moment_ids) - set(moments)
            if unknown:
                issues.append(
                    _error(
                        "caption_evidence_unknown",
                        f"Caption cites unknown Moments {sorted(unknown)!r}.",
                    )
                )
            if caption.source == "asr" and not any(
                moments[moment_id].asr_text
                for moment_id in caption.evidence_moment_ids
                if moment_id in moments
            ):
                issues.append(
                    _error(
                        "caption_asr_unavailable",
                        "ASR caption has no cited transcript evidence.",
                    )
                )

        if context.intent.music_policy == "none" and blueprint.music:
            issues.append(
                _error(
                    "music_disabled",
                    "Project intent explicitly disables background music.",
                )
            )
        for music in blueprint.music:
            asset = assets.get(music.asset_id)
            if asset is None or "music" not in asset.roles:
                issues.append(
                    _error(
                        "music_asset_unavailable",
                        f"Music Asset {music.asset_id!r} is not authorized.",
                    )
                )
            elif not asset.has_audio:
                issues.append(
                    _error(
                        "music_asset_has_no_audio",
                        f"Music Asset {music.asset_id!r} has no audio stream.",
                    )
                )
            else:
                _review_audio_range(
                    issues,
                    label="music",
                    decision_key=music.decision_key,
                    source=music.source,
                    output=music.output,
                    duration_seconds=asset.duration_seconds,
                    fade_in_frames=music.fade_in_frames,
                    fade_out_frames=music.fade_out_frames,
                    fps=context.output.fps,
                )

        for effect in blueprint.sound_effects:
            asset = assets.get(effect.asset_id)
            if asset is None or "sfx" not in asset.roles:
                issues.append(
                    _error(
                        "sfx_asset_unavailable",
                        f"Sound-effect Asset {effect.asset_id!r} is not authorized.",
                    )
                )
                continue
            if not asset.has_audio:
                issues.append(
                    _error(
                        "sfx_asset_has_no_audio",
                        f"Sound-effect Asset {effect.asset_id!r} has no audio stream.",
                    )
                )
                continue
            unknown = set(effect.evidence_moment_ids) - set(moments)
            if unknown:
                issues.append(
                    _error(
                        "sfx_evidence_unknown",
                        f"Sound-effect decision cites unknown Moments {sorted(unknown)!r}.",
                    )
                )
            _review_audio_range(
                issues,
                label="sound-effect",
                decision_key=effect.decision_key,
                source=effect.source,
                output=effect.output,
                duration_seconds=asset.duration_seconds,
                fade_in_frames=effect.fade_in_frames,
                fade_out_frames=effect.fade_out_frames,
                fps=context.output.fps,
            )

        audio_decisions = {
            item.decision_key: item
            for item in (*blueprint.music, *blueprint.sound_effects)
        }
        for sync in blueprint.audio_visual_sync:
            decision_item = audio_decisions[sync.audio_decision_key]
            asset = assets.get(decision_item.asset_id)
            if asset is None:
                continue
            analysis = asset.audio_analysis
            if analysis is None:
                issues.append(
                    _error(
                        "audio_sync_analysis_unavailable",
                        f"Audio Asset {asset.asset_id!r} has no frozen signal analysis.",
                    )
                )
                continue
            signal_times = {
                "beat": analysis.signal.beat_times_ms,
                "accent": analysis.signal.accent_times_ms,
                "transient": analysis.signal.transient_times_ms,
            }[sync.signal_event]
            if sync.source_time_ms not in signal_times:
                issues.append(
                    _error(
                        "audio_sync_event_unmeasured",
                        f"Audio sync {sync.sync_key!r} does not cite a measured "
                        f"{sync.signal_event}.",
                    )
                )
                continue
            expected_frame = _mapped_output_frame(
                decision_item.source,
                decision_item.output,
                sync.source_time_ms,
            )
            if expected_frame is None or abs(expected_frame - sync.output_frame) > 1:
                issues.append(
                    _error(
                        "audio_sync_mapping_invalid",
                        f"Audio sync {sync.sync_key!r} does not map its source event "
                        "to the declared output frame.",
                    )
                )
                continue
            allowed_targets = _sync_target_frames(blueprint, sync.target)
            if sync.output_frame not in allowed_targets:
                issues.append(
                    _error(
                        "audio_sync_target_missing",
                        f"Audio sync {sync.sync_key!r} does not land on a real "
                        f"{sync.target.replace('_', ' ')}.",
                    )
                )

        errors = tuple(issue for issue in issues if issue.severity == "error")
        return DirectorReview(
            accepted=not errors,
            issues=tuple(issues),
            summary=(
                "Edit Blueprint passed deterministic contract review."
                if not errors
                else f"Edit Blueprint has {len(errors)} contract error(s)."
            ),
        )


def _error(
    code: str,
    message: str,
    moment_id: str | None = None,
) -> DirectorReviewIssue:
    return DirectorReviewIssue(
        code=code,
        severity="error",
        message=message,
        repairable=True,
        moment_id=moment_id,
    )


def _review_audio_range(
    issues: list[DirectorReviewIssue],
    *,
    label: str,
    decision_key: str,
    source: SourceRange,
    output: FrameRange,
    duration_seconds: float,
    fade_in_frames: int,
    fade_out_frames: int,
    fps: Rational,
) -> None:
    if source.end / source.timescale > duration_seconds + 1e-6:
        issues.append(
            _error(
                "audio_source_outside_asset",
                f"{label.title()} decision {decision_key!r} exceeds its Asset.",
            )
        )
    if fade_in_frames + fade_out_frames > output.duration_frames:
        issues.append(
            _error(
                "audio_fades_overlap",
                f"{label.title()} decision {decision_key!r} has overlapping fades.",
            )
        )
    if (
        source.duration_ticks * fps.num
        != source.timescale * output.duration_frames * fps.den
    ):
        issues.append(
            _error(
                "audio_duration_mapping_invalid",
                f"{label.title()} decision {decision_key!r} source and output "
                "durations do not match constant playback.",
            )
        )


def _mapped_output_frame(
    source: SourceRange,
    output: FrameRange,
    source_time_ms: int,
) -> int | None:
    source_start = source.start / source.timescale
    source_end = source.end / source.timescale
    event_time = source_time_ms / 1_000
    if event_time < source_start or event_time >= source_end:
        return None
    progress = (event_time - source_start) / (source_end - source_start)
    return output.start_frame + round(progress * output.duration_frames)


def _sync_target_frames(blueprint: EditBlueprint, target: str) -> set[int]:
    duration = blueprint.output_duration_frames
    if target == "visual_cut":
        return {
            visual.output.start_frame
            for visual in blueprint.visuals
            if 0 < visual.output.start_frame < duration
        }
    if target == "transition":
        return {
            visual.output.end_frame
            for visual in blueprint.visuals
            if visual.transition_out is not None
            and visual.output.end_frame < duration
        }
    return {
        frame
        for section in blueprint.sections
        for frame in (section.output.start_frame, section.output.end_frame)
        if 0 <= frame < duration
    }
