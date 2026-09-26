"""Explicit provider fixtures for tests; never imported by runtime code."""

from __future__ import annotations

from aive.contracts.director import (
    AgentRunContext,
    CopyStrategy,
    CreativeStrategy,
    DirectorReview,
    FirstCutDecision,
    MusicStrategy,
)
from aive.contracts.rendering import RenderReviewReport


class FixtureFirstCutDirectorProvider:
    """Select fixture Moments predictably so application tests stay offline."""

    name = "fixture-first-cut-director"
    version = "1"

    async def create_first_cut(
        self,
        context: AgentRunContext,
    ) -> FirstCutDecision:
        asset_order = {
            asset.asset_id: index for index, asset in enumerate(context.assets)
        }
        usable = tuple(
            sorted(
                (
                    moment
                    for moment in context.moments
                    if moment.is_usable and moment.asset_id in asset_order
                ),
                key=lambda moment: (
                    asset_order[moment.asset_id],
                    moment.ordinal,
                    moment.source.start,
                    moment.moment_id,
                ),
            )
        )
        if not usable:
            raise RuntimeError("fixture has no usable Moments")
        selected_assets = {moment.asset_id for moment in usable}
        has_audio = any(
            asset.has_audio and asset.asset_id in selected_assets
            for asset in context.assets
        )
        has_speech = any(moment.has_speech for moment in usable)
        story_arc = tuple(
            dict.fromkeys(
                moment.semantic_role
                for moment in usable
                if moment.semantic_role != "unknown"
            )
        )[:8]
        return FirstCutDecision(
            moment_ids=tuple(moment.moment_id for moment in usable),
            summary="Fixture first cut",
            explanation="Use the fixture Moments in source order.",
            creative_strategy=CreativeStrategy(
                objective=context.intent.prompt or "Create a coherent first cut",
                hook=usable[0].semantic or usable[0].asr_text,
                pacing="balanced",
                story_arc=story_arc,
            ),
            copy_strategy=CopyStrategy(
                caption_mode="speech" if has_speech else "none",
                caption_tone="concise" if has_speech else None,
            ),
            music_strategy=MusicStrategy(
                mode="source_only" if has_audio else "none",
            ),
        )

    async def repair_first_cut(
        self,
        context: AgentRunContext,
        decision: FirstCutDecision,
        review: DirectorReview,
    ) -> FirstCutDecision:
        return await self.create_first_cut(context)

    async def repair_rendered_first_cut(
        self,
        context: AgentRunContext,
        decision: FirstCutDecision,
        review: RenderReviewReport,
    ) -> FirstCutDecision:
        return decision
