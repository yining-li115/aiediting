"""Deterministic semantic review for model-authored first-cut decisions."""

from __future__ import annotations

from ..contracts.director import (
    AgentRunContext,
    DirectorReview,
    DirectorReviewIssue,
    FirstCutDecision,
)


class FirstCutReviewer:
    """Reject unsafe or incomplete selections before Patch persistence."""

    def review(
        self,
        context: AgentRunContext,
        decision: FirstCutDecision,
    ) -> DirectorReview:
        moments = {moment.moment_id: moment for moment in context.moments}
        assets = {asset.asset_id: asset for asset in context.assets}
        issues: list[DirectorReviewIssue] = []
        selected_frames = 0
        selected_asset_ids: set[str] = set()

        for moment_id in decision.moment_ids:
            moment = moments.get(moment_id)
            if moment is None:
                issues.append(
                    DirectorReviewIssue(
                        code="unknown_moment",
                        severity="error",
                        message=f"Selected Moment {moment_id!r} is not in the run context.",
                        repairable=True,
                        moment_id=moment_id,
                    )
                )
                continue
            asset = assets.get(moment.asset_id)
            if asset is None or "source" not in asset.roles:
                issues.append(
                    DirectorReviewIssue(
                        code="unauthorized_source",
                        severity="error",
                        message=f"Moment {moment_id!r} is not backed by a source Asset.",
                        repairable=True,
                        moment_id=moment_id,
                    )
                )
                continue
            if not moment.is_usable:
                issues.append(
                    DirectorReviewIssue(
                        code="unusable_moment",
                        severity="error",
                        message=f"Moment {moment_id!r} was marked unusable by analysis.",
                        repairable=True,
                        moment_id=moment_id,
                    )
                )
                continue
            selected_asset_ids.add(moment.asset_id)
            selected_frames += _moment_frames(context, moment_id)

        available_frames = sum(
            _moment_frames(context, moment.moment_id)
            for moment in context.moments
            if moment.is_usable and moment.asset_id in assets
        )
        required_frames = min(context.intent.target_duration_frames, available_frames)
        if selected_frames < required_frames:
            issues.append(
                DirectorReviewIssue(
                    code="insufficient_duration_coverage",
                    severity="error",
                    message=(
                        f"Selection covers {selected_frames} frames but at least "
                        f"{required_frames} usable frames are available and required."
                    ),
                    repairable=True,
                )
            )

        if (
            context.intent.caption_policy == "none"
            and decision.copy_strategy.caption_mode != "none"
        ):
            issues.append(
                DirectorReviewIssue(
                    code="captions_disabled",
                    severity="error",
                    message="Project intent explicitly disables captions.",
                    repairable=True,
                )
            )
        if (
            context.intent.caption_policy == "auto"
            and decision.copy_strategy.caption_mode == "none"
            and any(
                moments[moment_id].has_speech
                for moment_id in decision.moment_ids
                if moment_id in moments
            )
        ):
            issues.append(
                DirectorReviewIssue(
                    code="captions_required",
                    severity="error",
                    message="Project intent requests captions for selected speech.",
                    repairable=True,
                )
            )
        if (
            decision.copy_strategy.caption_mode != "none"
            and not any(
                moments[moment_id].asr_text
                for moment_id in decision.moment_ids
                if moment_id in moments
            )
        ):
            issues.append(
                DirectorReviewIssue(
                    code="caption_evidence_unavailable",
                    severity="error",
                    message="Caption strategy requires ASR text on a selected Moment.",
                    repairable=True,
                )
            )

        if (
            context.intent.music_policy == "none"
            and decision.music_strategy.mode == "music_bed"
        ):
            issues.append(
                DirectorReviewIssue(
                    code="music_disabled",
                    severity="error",
                    message="Project intent explicitly disables background music.",
                    repairable=True,
                )
            )
        if (
            context.intent.music_policy == "auto"
            and decision.music_strategy.mode != "music_bed"
            and any("music" in asset.roles for asset in context.assets)
        ):
            issues.append(
                DirectorReviewIssue(
                    code="music_required",
                    severity="error",
                    message="Project intent requests the available authorized music Asset.",
                    repairable=True,
                )
            )

        if decision.music_strategy.mode == "music_bed":
            music_asset = assets.get(decision.music_strategy.asset_id or "")
            if music_asset is None or "music" not in music_asset.roles:
                issues.append(
                    DirectorReviewIssue(
                        code="music_asset_unavailable",
                        severity="error",
                        message=(
                            "Music-bed strategy must name an authorized music Asset "
                            "from the frozen context."
                        ),
                        repairable=True,
                    )
                )
            elif not music_asset.has_audio:
                issues.append(
                    DirectorReviewIssue(
                        code="music_asset_has_no_audio",
                        severity="error",
                        message="Selected music Asset has no audio stream.",
                        repairable=True,
                    )
                )

        if decision.music_strategy.mode == "source_only" and not any(
            asset.has_audio and asset.asset_id in selected_asset_ids
            for asset in context.assets
        ):
            issues.append(
                DirectorReviewIssue(
                    code="source_audio_unavailable",
                    severity="warning",
                    message="Source-only audio was requested, but no source Asset has audio.",
                )
            )

        if decision.creative_strategy.hook is None:
            issues.append(
                DirectorReviewIssue(
                    code="hook_not_articulated",
                    severity="warning",
                    message="The proposal does not explain its opening hook.",
                )
            )
        if not decision.creative_strategy.story_arc:
            issues.append(
                DirectorReviewIssue(
                    code="story_arc_not_articulated",
                    severity="warning",
                    message="The proposal does not describe its story progression.",
                )
            )

        errors = [issue for issue in issues if issue.severity == "error"]
        return DirectorReview(
            accepted=not errors,
            issues=tuple(issues),
            summary=(
                "Director proposal passed deterministic review."
                if not errors
                else f"Director proposal requires repair for {len(errors)} error(s)."
            ),
        )


def _moment_frames(context: AgentRunContext, moment_id: str) -> int:
    moment = next(value for value in context.moments if value.moment_id == moment_id)
    asset = next(value for value in context.assets if value.asset_id == moment.asset_id)
    fps = context.output.fps
    if asset.kind == "photo":
        return max(1, 3 * fps.num // fps.den)
    ticks = moment.source.end - moment.source.start
    return ticks * fps.num // (moment.source.timescale * fps.den)
