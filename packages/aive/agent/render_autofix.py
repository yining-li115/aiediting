"""One bounded repair proposal after a rendered candidate is rejected."""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.director import (
    AgentRunContext,
    DirectorReview,
    FirstCutDecision,
    RenderAutoFixContext,
)
from ..contracts.rendering import RenderReviewReport
from .provider import DirectorProvider
from .review import FirstCutReviewer


MAX_RENDER_AUTOFIX_ATTEMPTS = 1


class RenderAutoFixRejected(ValueError):
    def __init__(self, decision: FirstCutDecision, review: DirectorReview) -> None:
        self.decision = decision
        self.review = review
        super().__init__(review.summary)


@dataclass(frozen=True)
class RenderAutoFixOutcome:
    decision: FirstCutDecision
    review: DirectorReview


class RenderAutoFixPipeline:
    """Ask the Director for one replacement decision and validate it."""

    def __init__(
        self,
        *,
        provider: DirectorProvider,
        reviewer: FirstCutReviewer,
        max_attempts: int = MAX_RENDER_AUTOFIX_ATTEMPTS,
    ) -> None:
        if max_attempts < 0 or max_attempts > 3:
            raise ValueError("render AutoFix attempts must be between 0 and 3")
        self._provider = provider
        self._reviewer = reviewer
        self.max_attempts = max_attempts

    def can_repair(self, report: RenderReviewReport, attempts: int) -> bool:
        errors = tuple(issue for issue in report.issues if issue.severity == "error")
        return (
            attempts < self.max_attempts
            and bool(errors)
            and all(issue.repairable for issue in errors)
        )

    async def repair(
        self,
        context: AgentRunContext,
        autofix: RenderAutoFixContext,
    ) -> RenderAutoFixOutcome:
        if autofix.attempt > self.max_attempts:
            raise ValueError("render AutoFix attempt exceeds the configured limit")
        decision = await self._provider.repair_rendered_first_cut(
            context,
            autofix.decision,
            autofix.render_review,
        )
        review = self._reviewer.review(context, decision)
        if not review.accepted:
            raise RenderAutoFixRejected(decision, review)
        return RenderAutoFixOutcome(decision=decision, review=review)
