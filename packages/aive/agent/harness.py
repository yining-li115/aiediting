"""Runtime boundary for model-driven video editing work."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ..contracts.conversation import (
    AgentToolStep,
    AgentTurnContext,
    ConversationMessage,
    CreateAgentTurnRequest,
    PatchProposal,
)
from ..contracts.director import (
    AgentRunContext,
    FirstCutDecision,
    RenderAutoFixContext,
)
from ..contracts.moments import MomentEvidence
from ..contracts.rendering import RenderArtifact, RenderReviewReport
from ..contracts.revision import ProjectRevision
from ..media.repository import AssetProcessingRecord
from .context import AgentRunContextBuilder
from .continuous_edit import ContinuousEditOutcome, ContinuousEditPipeline
from .first_cut import build_first_cut_explanation
from .pipeline import (
    DirectorPipeline,
    DirectorPipelineOutcome,
    DirectorReviewRejected,
    MAX_AUTOFIX_ATTEMPTS,
)
from .provider import DirectorProvider
from .render_review import RenderReviewEngine, RenderReviewOutcome
from .render_autofix import (
    MAX_RENDER_AUTOFIX_ATTEMPTS,
    RenderAutoFixOutcome,
    RenderAutoFixPipeline,
)
from .review import FirstCutReviewer
from .tools import ComponentAgentTools
from .turn_context import (
    BASE_CONTINUOUS_EDIT_TOOLS,
    CONTINUOUS_EDIT_TOOLS,
    AgentTurnContextBuilder,
)


class VideoAgentHarness:
    """Own model context, review, and bounded recovery for Agent workflows.

    The Harness has no repository or ProjectService access. Application
    services persist its inputs and outputs and remain the only layer allowed
    to commit typed Patches or enqueue renders.
    """

    def __init__(
        self,
        *,
        provider: DirectorProvider,
        context_builder: AgentRunContextBuilder | None = None,
        turn_context_builder: AgentTurnContextBuilder | None = None,
        reviewer: FirstCutReviewer | None = None,
        render_review_engine: RenderReviewEngine | None = None,
        component_tools: ComponentAgentTools | None = None,
        max_repair_attempts: int = MAX_AUTOFIX_ATTEMPTS,
        max_render_autofix_attempts: int = MAX_RENDER_AUTOFIX_ATTEMPTS,
    ) -> None:
        self._provider = provider
        self._context_builder = context_builder or AgentRunContextBuilder()
        self._turn_context_builder = turn_context_builder or AgentTurnContextBuilder(
            allowed_tools=(
                CONTINUOUS_EDIT_TOOLS
                if component_tools is not None
                else BASE_CONTINUOUS_EDIT_TOOLS
            )
        )
        decision_reviewer = reviewer or FirstCutReviewer()
        self._first_cut = DirectorPipeline(
            provider=provider,
            reviewer=decision_reviewer,
            max_repair_attempts=max_repair_attempts,
        )
        self._render_review = render_review_engine or RenderReviewEngine()
        self._render_autofix = RenderAutoFixPipeline(
            provider=provider,
            reviewer=decision_reviewer,
            max_attempts=max_render_autofix_attempts,
        )
        self._continuous_edit = ContinuousEditPipeline(
            provider,
            component_tools=component_tools,
        )

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def provider_version(self) -> str:
        return self._provider.version

    @property
    def render_reviewer_name(self) -> str:
        return self._render_review.provider_name

    @property
    def render_reviewer_version(self) -> str:
        return self._render_review.provider_version

    def build_context(
        self,
        *,
        revision: ProjectRevision,
        asset_statuses: tuple[AssetProcessingRecord, ...],
        moments: tuple[MomentEvidence, ...],
    ) -> AgentRunContext:
        """Build the frozen, project-scoped evidence for one Agent run."""

        return self._context_builder.build(
            revision=revision,
            asset_statuses=asset_statuses,
            moments=moments,
        )

    def build_turn_context(
        self,
        *,
        turn_id: str,
        request: CreateAgentTurnRequest,
        revision: ProjectRevision,
        moments: tuple[MomentEvidence, ...],
        history: tuple[ConversationMessage, ...],
        asset_statuses: tuple[AssetProcessingRecord, ...] = (),
    ) -> AgentTurnContext:
        return self._turn_context_builder.build(
            turn_id=turn_id,
            request=request,
            revision=revision,
            moments=moments,
            history=history,
            asset_statuses=asset_statuses,
        )

    async def run_edit_turn(
        self,
        context: AgentTurnContext,
        *,
        existing_steps: tuple[AgentToolStep, ...] = (),
        on_step: Callable[[AgentToolStep], None] | None = None,
        proposal_validator: Callable[[PatchProposal], None] | None = None,
    ) -> ContinuousEditOutcome:
        return await self._continuous_edit.run(
            context,
            existing_steps=existing_steps,
            on_step=on_step,
            proposal_validator=proposal_validator,
        )

    def can_autofix_render(
        self,
        report: RenderReviewReport,
        attempts: int,
    ) -> bool:
        return self._render_autofix.can_repair(report, attempts)

    def build_render_autofix_context(
        self,
        *,
        context: AgentRunContext,
        rejected_revision_id: str,
        rejected_artifact_id: str,
        attempt: int,
        decision: FirstCutDecision,
        review: RenderReviewReport,
    ) -> RenderAutoFixContext:
        if not self._render_autofix.can_repair(review, attempt - 1):
            raise ValueError("render review is not eligible for bounded AutoFix")
        return RenderAutoFixContext(
            project_id=context.project_id,
            base_revision_id=context.base_revision_id,
            rejected_revision_id=rejected_revision_id,
            rejected_artifact_id=rejected_artifact_id,
            attempt=attempt,
            decision=decision,
            render_review=review,
        )

    async def run_first_cut(
        self,
        context: AgentRunContext,
    ) -> DirectorPipelineOutcome:
        """Run the first-cut workflow without committing project state."""

        outcome = await self._first_cut.create_first_cut(context)
        decision = outcome.decision.model_copy(
            update={
                "explanation": build_first_cut_explanation(context, outcome.decision)
            }
        )
        return DirectorPipelineOutcome(
            decision=decision,
            review=outcome.review,
            repair_attempts=outcome.repair_attempts,
        )

    async def review_render(
        self,
        *,
        revision: ProjectRevision,
        artifact: RenderArtifact,
        media_path: Path,
        review_id: str,
        reviewed_at: datetime,
    ) -> RenderReviewOutcome:
        """Review one exact ready Artifact without mutating project state."""

        return await self._render_review.review(
            revision=revision,
            artifact=artifact,
            media_path=media_path,
            review_id=review_id,
            reviewed_at=reviewed_at,
        )

    async def autofix_render(
        self,
        *,
        context: AgentRunContext,
        autofix: RenderAutoFixContext,
    ) -> RenderAutoFixOutcome:
        """Produce and validate one replacement decision without persisting it."""

        outcome = await self._render_autofix.repair(context, autofix)
        explanation = (
            f"{build_first_cut_explanation(context, outcome.decision)} "
            f"Render AutoFix addressed: {autofix.render_review.summary}"
        )[:4_000]
        decision = outcome.decision.model_copy(
            update={"explanation": explanation}
        )
        return RenderAutoFixOutcome(decision=decision, review=outcome.review)
