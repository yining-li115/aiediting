"""Durable application service for revision-bound render review Jobs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol

from ..contracts.rendering import RenderArtifact, RenderReviewTicket
from ..jobs import Stage
from ..project.repository import RevisionRepository
from ..rendering.repository import RenderArtifactNotFound, RenderRepository
from ..specs.base import new_id
from ..storage import StorageProvider
from .harness import VideoAgentHarness


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RenderReviewProgressReporter(Protocol):
    job_id: str

    def update(self, stage: Stage | None = None, progress: float | None = None) -> None:
        ...

    def emit(self, type: str, **data) -> None:
        ...


class RenderReviewApplication:
    """Persist Harness review evidence without giving it repository access."""

    def __init__(
        self,
        *,
        revisions: RevisionRepository,
        artifacts: RenderRepository,
        storage: StorageProvider,
        harness: VideoAgentHarness,
        id_factory: Callable[[str], str] = new_id,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._revisions = revisions
        self._artifacts = artifacts
        self._storage = storage
        self._harness = harness
        self._id_factory = id_factory
        self._clock = clock

    def enqueue(self, project_id: str, artifact_id: str) -> RenderReviewTicket:
        artifact = self.get(project_id, artifact_id)
        return self._artifacts.enqueue_review(
            project_id,
            artifact.artifact_id,
            job_id=self._id_factory("job"),
            provider=self._harness.render_reviewer_name,
            provider_version=self._harness.render_reviewer_version,
        )

    async def process(self, handle: RenderReviewProgressReporter) -> dict:
        artifact = self._artifacts.get_review_job_artifact(handle.job_id)
        if artifact.review_status in {"accepted", "rejected"}:
            return artifact.model_dump(mode="json")
        self._artifacts.mark_reviewing(handle.job_id)
        handle.update(stage=Stage.REVIEWING, progress=0.1)
        try:
            revision = self._revisions.get_revision(
                artifact.project_id,
                artifact.revision_id,
            )
            if revision is None:
                raise RenderArtifactNotFound(
                    f"review revision {artifact.revision_id!r} no longer exists"
                )
            storage_key = self._artifacts.get_storage_key(
                artifact.project_id,
                artifact.artifact_id,
            )
            if storage_key is None:
                raise RenderArtifactNotFound(
                    f"render artifact {artifact.artifact_id!r} has no stored media"
                )
            media_path = self._storage.open_local(storage_key)
            outcome = await self._harness.review_render(
                revision=revision,
                artifact=artifact,
                media_path=media_path,
                review_id=_stable_review_id(artifact.artifact_id),
                reviewed_at=self._clock(),
            )
            self._artifacts.save_review_context(handle.job_id, outcome.context)
            reviewed = self._artifacts.mark_reviewed(handle.job_id, outcome.report)
            handle.update(progress=1.0)
            handle.emit(
                "render_reviewed",
                artifact_id=reviewed.artifact_id,
                revision_id=reviewed.revision_id,
                accepted=outcome.report.accepted,
                visual_review_performed=outcome.report.visual_review_performed,
                issue_count=len(outcome.report.issues),
            )
            return reviewed.model_dump(mode="json")
        except Exception as exc:
            self._artifacts.mark_review_failed(
                handle.job_id,
                str(exc) or exc.__class__.__name__,
            )
            raise

    def get(self, project_id: str, artifact_id: str) -> RenderArtifact:
        artifact = self._artifacts.get(project_id, artifact_id)
        if artifact is None:
            raise RenderArtifactNotFound(
                f"render artifact {artifact_id!r} does not exist in project "
                f"{project_id!r}"
            )
        return artifact


def _stable_review_id(artifact_id: str) -> str:
    token = sha256(f"render-review:{artifact_id}".encode()).hexdigest()[:24]
    return f"review_{token}"
