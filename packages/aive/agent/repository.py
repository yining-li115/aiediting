"""Persistence port for durable autonomous Director runs."""

from __future__ import annotations

from typing import Protocol

from ..contracts.director import (
    AgentRunContext,
    CreateFirstCutRequest,
    DirectorReview,
    DirectorRun,
    FirstCutDecision,
    FirstCutTicket,
    RenderAutoFixContext,
)
from ..contracts.patch import Patch


class DirectorRepositoryError(RuntimeError):
    pass


class DirectorRunNotFound(DirectorRepositoryError):
    pass


class DirectorRevisionConflict(DirectorRepositoryError):
    pass


class DirectorIdempotencyConflict(DirectorRepositoryError):
    pass


class DirectorJobConflict(DirectorRepositoryError):
    pass


class DirectorRunRepository(Protocol):
    def get_idempotent(
        self,
        project_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> FirstCutTicket | None: ...

    def enqueue(
        self,
        request: CreateFirstCutRequest,
        *,
        run_id: str,
        job_id: str,
        request_hash: str,
        provider: str,
        provider_version: str,
    ) -> FirstCutTicket: ...

    def get(self, project_id: str, run_id: str) -> DirectorRun | None: ...

    def get_job_run(self, job_id: str) -> DirectorRun: ...

    def latest(self, project_id: str) -> DirectorRun | None: ...

    def incomplete_render_reviews(self) -> tuple[DirectorRun, ...]: ...

    def get_autofix_job_run(self, job_id: str) -> DirectorRun: ...

    def get_autofix_draft(
        self, job_id: str
    ) -> tuple[FirstCutDecision, DirectorReview] | None: ...

    def mark_running(self, job_id: str) -> DirectorRun: ...

    def save_context(
        self, job_id: str, context: AgentRunContext
    ) -> DirectorRun: ...

    def save_decision(
        self,
        job_id: str,
        decision: FirstCutDecision,
        review: DirectorReview,
        repair_attempts: int,
    ) -> DirectorRun: ...

    def mark_awaiting_review(
        self,
        job_id: str,
        *,
        patch: Patch,
        revision_id: str,
        render_artifact_id: str,
        render_job_id: str,
    ) -> DirectorRun: ...

    def mark_review_accepted(self, job_id: str) -> DirectorRun: ...

    def mark_review_rejected(self, job_id: str) -> DirectorRun: ...

    def enqueue_render_autofix(
        self,
        job_id: str,
        *,
        autofix_job_id: str,
        context: RenderAutoFixContext,
    ) -> DirectorRun: ...

    def save_render_autofix_decision(
        self,
        autofix_job_id: str,
        decision: FirstCutDecision,
        review: DirectorReview,
    ) -> DirectorRun: ...

    def mark_render_autofix_candidate(
        self,
        autofix_job_id: str,
        *,
        patch: Patch,
        revision_id: str,
        render_artifact_id: str,
        render_job_id: str,
    ) -> DirectorRun: ...

    def mark_render_autofix_failed(
        self, autofix_job_id: str, error: str
    ) -> DirectorRun: ...

    def mark_failed(self, job_id: str, error: str) -> DirectorRun: ...
