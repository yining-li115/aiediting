"""Persistence port for revision-bound preview and export artifacts."""

from __future__ import annotations

from typing import Protocol

from ..contracts.rendering import (
    CreateRenderRequest,
    RenderArtifact,
    RenderReviewContext,
    RenderReviewReport,
    RenderReviewTicket,
    RenderTicket,
)


class RenderRepositoryError(RuntimeError):
    pass


class RenderRevisionNotFound(RenderRepositoryError):
    pass


class RenderArtifactNotFound(RenderRepositoryError):
    pass


class RenderIdempotencyConflict(RenderRepositoryError):
    pass


class RenderJobConflict(RenderRepositoryError):
    pass


class RenderRepository(Protocol):
    def enqueue(
        self,
        request: CreateRenderRequest,
        *,
        artifact_id: str,
        job_id: str,
        request_hash: str,
        renderer: str,
        renderer_version: str,
    ) -> RenderTicket: ...

    def get(
        self, project_id: str, artifact_id: str
    ) -> RenderArtifact | None: ...

    def get_job_artifact(self, job_id: str) -> RenderArtifact: ...

    def get_storage_key(self, project_id: str, artifact_id: str) -> str | None: ...

    def list_revision(
        self, project_id: str, revision_id: str
    ) -> tuple[RenderArtifact, ...]: ...

    def latest_ready(
        self,
        project_id: str,
        revision_id: str,
        kind: str,
    ) -> RenderArtifact | None: ...

    def mark_rendering(self, job_id: str) -> RenderArtifact: ...

    def mark_ready(
        self,
        job_id: str,
        *,
        storage_key: str,
        content_type: str,
        duration_seconds: float,
        file_size: int,
        sha256: str,
        render_time_ms: int,
    ) -> RenderArtifact: ...

    def mark_failed(self, job_id: str, error: str) -> RenderArtifact: ...

    def enqueue_review(
        self,
        project_id: str,
        artifact_id: str,
        *,
        job_id: str,
        provider: str,
        provider_version: str,
    ) -> RenderReviewTicket: ...

    def get_review_job_artifact(self, job_id: str) -> RenderArtifact: ...

    def mark_reviewing(self, job_id: str) -> RenderArtifact: ...

    def save_review_context(
        self,
        job_id: str,
        context: RenderReviewContext,
    ) -> RenderArtifact: ...

    def mark_reviewed(
        self,
        job_id: str,
        report: RenderReviewReport,
    ) -> RenderArtifact: ...

    def mark_review_failed(self, job_id: str, error: str) -> RenderArtifact: ...
