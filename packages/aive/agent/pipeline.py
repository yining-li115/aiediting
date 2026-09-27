"""One bounded Director proposal, review, and semantic repair loop."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.director import AgentRunContext, DirectorReview, FirstCutDecision
from .provider import DirectorProvider
from .review import CreativePlanReviewerProvider, FirstCutReviewer


MAX_AUTOFIX_ATTEMPTS = 1


class DirectorReviewRejected(ValueError):
    def __init__(
        self,
        decision: FirstCutDecision,
        review: DirectorReview,
        repair_attempts: int,
    ) -> None:
        self.decision = decision
        self.review = review
        self.repair_attempts = repair_attempts
        super().__init__(review.summary)


@dataclass(frozen=True)
class DirectorPipelineOutcome:
    decision: FirstCutDecision
    review: DirectorReview
    repair_attempts: int


class DirectorPipeline:
    """Keep model autonomy inside a small, observable, pre-commit loop."""

    def __init__(
        self,
        *,
        provider: DirectorProvider,
        reviewer: FirstCutReviewer,
        creative_reviewer: CreativePlanReviewerProvider | None = None,
        max_repair_attempts: int = MAX_AUTOFIX_ATTEMPTS,
    ) -> None:
        if max_repair_attempts < 0 or max_repair_attempts > 3:
            raise ValueError("Director AutoFix attempts must be between 0 and 3")
        self._provider = provider
        self._reviewer = reviewer
        self._creative_reviewer = creative_reviewer
        self._max_repair_attempts = max_repair_attempts

    async def create_first_cut(
        self,
        context: AgentRunContext,
    ) -> DirectorPipelineOutcome:
        decision = await self._provider.create_first_cut(context)
        review = await self._review(context, decision)
        attempts = 0
        while not review.accepted and attempts < self._max_repair_attempts:
            if not any(issue.repairable for issue in review.issues):
                break
            decision = await self._provider.repair_first_cut(
                context,
                decision,
                review,
            )
            attempts += 1
            review = await self._review(context, decision)
        if not review.accepted:
            raise DirectorReviewRejected(decision, review, attempts)
        return DirectorPipelineOutcome(
            decision=decision,
            review=review,
            repair_attempts=attempts,
        )

    async def _review(
        self,
        context: AgentRunContext,
        decision: FirstCutDecision,
    ) -> DirectorReview:
        deterministic = self._reviewer.review(context, decision)
        if not deterministic.accepted or self._creative_reviewer is None:
            return deterministic
        return await self._creative_reviewer.review_edit_blueprint(
            context,
            decision,
        )
