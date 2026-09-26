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

        if decision.music_strategy.mode == "music_bed" and not any(
            "music" in asset.roles for asset in context.assets
        ):
            issues.append(
                DirectorReviewIssue(
                    code="music_asset_unavailable",
                    severity="error",
                    message="Music-bed strategy requires an authorized music Asset.",
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
