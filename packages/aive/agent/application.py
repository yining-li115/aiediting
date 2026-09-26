"""Application service for autonomous first-cut Agent runs."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from typing import Protocol

from ..contracts.director import CreateFirstCutRequest, DirectorRun, FirstCutTicket
from ..contracts.moments import MomentEvidence
from ..contracts.project_doc import ProjectDoc
from ..contracts.rendering import CreateRenderRequest, RenderArtifact
from ..jobs import Stage
from ..media.analysis_repository import MediaAnalysisRepository
from ..media.repository import AssetProcessingRecord, MediaAssetRepository
from ..project.application import ProjectRevisionApplication
from ..project.hashing import canonical_model_hash
from ..rendering.application import RevisionRenderApplication
from ..specs.base import new_id
from .first_cut import build_first_cut_patch
from .harness import DirectorReviewRejected, VideoAgentHarness
from .render_autofix import RenderAutoFixRejected
from .repository import DirectorRunRepository


class FirstCutInputUnavailable(RuntimeError):
    pass


class DirectorProgressReporter(Protocol):
    job_id: str

    def update(self, stage: Stage | None = None, progress: float | None = None) -> None:
        ...

    def emit(self, type: str, **data) -> None:
        ...


class DirectorApplication:
    """Persist Director proposals and route every candidate through ProjectService."""

    def __init__(
        self,
        *,
        runs: DirectorRunRepository,
        projects: ProjectRevisionApplication,
        analysis: MediaAnalysisRepository,
        assets: MediaAssetRepository,
        rendering: RevisionRenderApplication,
        harness: VideoAgentHarness,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._runs = runs
        self._projects = projects
        self._analysis = analysis
        self._assets = assets
        self._rendering = rendering
        self._harness = harness
        self._id_factory = id_factory

    def enqueue(self, request: CreateFirstCutRequest) -> FirstCutTicket:
        request_hash = canonical_model_hash(request)
        replay = self._runs.get_idempotent(
            request.project_id,
            request.idempotency_key,
            request_hash,
        )
        if replay is not None:
            return replay
        self._validate_inputs(request.project_id, request.base_revision_id)
        return self._runs.enqueue(
            request,
            run_id=self._id_factory("agent_run"),
            job_id=self._id_factory("job"),
            request_hash=request_hash,
            provider=self._harness.provider_name,
            provider_version=self._harness.provider_version,
        )

    def enqueue_automatic(
        self,
        project_id: str,
        upstream_job_id: str,
    ) -> FirstCutTicket | None:
        revision = self._projects.get_current_revision(project_id)
        if revision.document.timeline.tracks:
            return None
        token = _stable_token(project_id, revision.document.revision_id, upstream_job_id)
        return self.enqueue(
            CreateFirstCutRequest(
                request_id=f"first_cut_request_{token}",
                project_id=project_id,
                base_revision_id=revision.document.revision_id,
                idempotency_key=f"first_cut_{token}",
            )
        )

    async def process(self, handle: DirectorProgressReporter) -> dict:
        run = self._runs.get_job_run(handle.job_id)
        if run.status in {
            "committed",
            "awaiting_review",
            "reviewing",
            "ready",
            "render_failed",
            "review_rejected",
            "review_failed",
        }:
            return run.model_dump(mode="json")
        self._runs.mark_running(handle.job_id)
        try:
            handle.update(stage=Stage.PLANNING, progress=0.1)
            revision = self._projects.get_revision(
                run.project_id,
                run.base_revision_id,
            )
            moments = self._analysis.list_moments(run.project_id)
            statuses = self._assets.list_processing_status(run.project_id)
            self._validate_document_inputs(revision.document, statuses, moments)

            current = self._runs.get_job_run(handle.job_id)
            context = current.context
            if context is None:
                context = self._harness.build_context(
                    revision=revision,
                    asset_statuses=statuses,
                    moments=moments,
                )
                current = self._runs.save_context(handle.job_id, context)
            handle.emit(
                "director_context_ready",
                run_id=current.run_id,
                base_revision_id=context.base_revision_id,
                asset_count=len(context.assets),
                moment_count=len(context.moments),
            )

            decision = current.decision
            review = current.review
            if decision is None:
                try:
                    outcome = await self._harness.run_first_cut(context)
                except DirectorReviewRejected as exc:
                    self._runs.save_decision(
                        handle.job_id,
                        exc.decision,
                        exc.review,
                        exc.repair_attempts,
                    )
                    raise
                decision = outcome.decision
                review = outcome.review
                current = self._runs.save_decision(
                    handle.job_id,
                    decision,
                    review,
                    outcome.repair_attempts,
                )
            if review is None or not review.accepted:
                raise FirstCutInputUnavailable(
                    "first-cut decision has not passed Director review"
                )
            handle.emit(
                "director_decision",
                run_id=current.run_id,
                moment_ids=list(decision.moment_ids),
                explanation=decision.explanation,
                repair_attempts=current.repair_attempts,
                review_summary=review.summary,
            )
            handle.update(stage=Stage.MATCHING, progress=0.45)

            patch = build_first_cut_patch(
                document=revision.document,
                moments=moments,
                decision=decision,
                run_id=run.run_id,
                request_id=run.request_id,
                asset_has_audio={asset.asset_id: asset.has_audio for asset in context.assets},
            )
            write = self._projects.apply_patch(patch, require_review=True)
            if write.revision is None:
                raise FirstCutInputUnavailable(
                    "first-cut Patch committed without a result revision"
                )
            result_revision_id = write.revision.document.revision_id
            handle.update(stage=Stage.PLANNING, progress=0.75)
            render = self._rendering.enqueue(
                CreateRenderRequest(
                    request_id=_stable_id("first-cut-render-request", run.run_id),
                    project_id=run.project_id,
                    revision_id=result_revision_id,
                    idempotency_key=_stable_id("first-cut-render", run.run_id),
                    kind="preview",
                    quality="proxy",
                )
            )
            awaiting_review = self._runs.mark_awaiting_review(
                handle.job_id,
                patch=patch,
                revision_id=result_revision_id,
                render_artifact_id=render.artifact.artifact_id,
                render_job_id=render.artifact.job_id,
            )
            handle.update(progress=1.0)
            handle.emit(
                "first_cut_candidate_ready",
                run_id=awaiting_review.run_id,
                revision_id=result_revision_id,
                render_job_id=render.artifact.job_id,
            )
            return awaiting_review.model_dump(mode="json")
        except Exception as exc:
            self._runs.mark_failed(
                handle.job_id,
                str(exc) or exc.__class__.__name__,
            )
            raise

    async def process_render_autofix(self, handle: DirectorProgressReporter) -> dict:
        """Create one replacement candidate from a frozen rejected-render review."""

        run = self._runs.get_autofix_job_run(handle.job_id)
        if run.status != "autofixing":
            return run.model_dump(mode="json")
        handle.update(stage=Stage.AUTOFIXING, progress=0.1)
        try:
            if run.context is None or run.render_autofix_context is None:
                raise FirstCutInputUnavailable("render AutoFix context is unavailable")
            current = self._projects.get_current_revision(run.project_id)
            if current.document.revision_id != run.base_revision_id:
                raise FirstCutInputUnavailable(
                    "project head changed before render AutoFix could be applied"
                )
            draft = self._runs.get_autofix_draft(handle.job_id)
            if draft is None:
                try:
                    outcome = await self._harness.autofix_render(
                        context=run.context,
                        autofix=run.render_autofix_context,
                    )
                except RenderAutoFixRejected as exc:
                    self._runs.save_render_autofix_decision(
                        handle.job_id,
                        exc.decision,
                        exc.review,
                    )
                    raise
                decision, review = outcome.decision, outcome.review
                self._runs.save_render_autofix_decision(
                    handle.job_id,
                    decision,
                    review,
                )
            else:
                decision, review = draft
            handle.update(progress=0.45)
            patch = build_first_cut_patch(
                document=current.document,
                moments=run.context.moments,
                decision=decision,
                run_id=run.run_id,
                request_id=_stable_id(
                    "render-autofix-request",
                    run.run_id,
                    str(run.render_autofix_context.attempt),
                ),
                asset_has_audio={
                    asset.asset_id: asset.has_audio for asset in run.context.assets
                },
                candidate_attempt=run.render_autofix_context.attempt,
            )
            write = self._projects.apply_patch(patch, require_review=True)
            if write.revision is None:
                raise FirstCutInputUnavailable(
                    "render AutoFix Patch produced no candidate revision"
                )
            result_revision_id = write.revision.document.revision_id
            handle.update(progress=0.7)
            render = self._rendering.enqueue(
                CreateRenderRequest(
                    request_id=_stable_id(
                        "render-autofix-render-request",
                        run.run_id,
                        str(run.render_autofix_context.attempt),
                    ),
                    project_id=run.project_id,
                    revision_id=result_revision_id,
                    idempotency_key=_stable_id(
                        "render-autofix-render",
                        run.run_id,
                        str(run.render_autofix_context.attempt),
                    ),
                    kind="preview",
                    quality="proxy",
                )
            )
            replacement = self._runs.mark_render_autofix_candidate(
                handle.job_id,
                patch=patch,
                revision_id=result_revision_id,
                render_artifact_id=render.artifact.artifact_id,
                render_job_id=render.artifact.job_id,
            )
            handle.update(progress=1.0)
            handle.emit(
                "render_autofix_candidate_ready",
                run_id=replacement.run_id,
                attempt=replacement.render_autofix_attempts,
                revision_id=result_revision_id,
                render_job_id=render.artifact.job_id,
            )
            return replacement.model_dump(mode="json")
        except Exception as exc:
            self._runs.mark_render_autofix_failed(
                handle.job_id,
                str(exc) or exc.__class__.__name__,
            )
            raise

    def get(self, project_id: str, run_id: str) -> DirectorRun | None:
        return self._runs.get(project_id, run_id)

    def get_job_run(self, job_id: str) -> DirectorRun:
        return self._runs.get_job_run(job_id)

    def latest(self, project_id: str) -> DirectorRun | None:
        self._projects.get_current_revision(project_id)
        return self._runs.latest(project_id)

    def incomplete_render_reviews(self) -> tuple[DirectorRun, ...]:
        return self._runs.incomplete_render_reviews()

    def complete_render_review(
        self,
        director_job_id: str,
        artifact: RenderArtifact,
    ) -> DirectorRun:
        run = self._runs.get_job_run(director_job_id)
        if run.status in {"ready", "autofixing"}:
            return run
        if run.render_artifact_id != artifact.artifact_id:
            raise FirstCutInputUnavailable(
                "render review artifact does not belong to the Director run"
            )
        if artifact.review_status == "rejected":
            if (
                artifact.review is not None
                and run.context is not None
                and run.decision is not None
                and run.result_revision_id is not None
                and self._harness.can_autofix_render(
                    artifact.review,
                    run.render_autofix_attempts,
                )
            ):
                attempt = run.render_autofix_attempts + 1
                autofix_context = self._harness.build_render_autofix_context(
                    context=run.context,
                    rejected_revision_id=run.result_revision_id,
                    rejected_artifact_id=artifact.artifact_id,
                    attempt=attempt,
                    decision=run.decision,
                    review=artifact.review,
                )
                return self._runs.enqueue_render_autofix(
                    director_job_id,
                    autofix_job_id=_stable_id(
                        "job-render-autofix",
                        run.run_id,
                        str(attempt),
                    ),
                    context=autofix_context,
                )
            return self._runs.mark_review_rejected(director_job_id)
        if artifact.review_status != "accepted" or artifact.review is None:
            raise FirstCutInputUnavailable(
                "Director candidate requires a completed render review"
            )
        if run.result_revision_id is None:
            raise FirstCutInputUnavailable(
                "Director candidate has no revision to accept"
            )
        candidate = self._projects.get_revision(
            run.project_id,
            run.result_revision_id,
        )
        try:
            self._projects.accept_candidate(run.project_id, candidate.commit_id)
            return self._runs.mark_review_accepted(director_job_id)
        except Exception as exc:
            self._runs.mark_failed(
                director_job_id,
                str(exc) or exc.__class__.__name__,
            )
            raise

    def _validate_inputs(self, project_id: str, revision_id: str) -> None:
        current = self._projects.get_current_revision(project_id)
        if current.document.revision_id != revision_id:
            raise FirstCutInputUnavailable(
                "first-cut request must target the current project revision"
            )
        self._validate_document_inputs(
            current.document,
            self._assets.list_processing_status(project_id),
            self._analysis.list_moments(project_id),
        )

    @staticmethod
    def _validate_document_inputs(
        document: ProjectDoc,
        statuses_values: tuple[AssetProcessingRecord, ...],
        moments: tuple[MomentEvidence, ...],
    ) -> None:
        if document.intent.task_mode != "autonomous":
            raise FirstCutInputUnavailable(
                "reference-remake projects require the reference workflow"
            )
        if document.timeline.tracks:
            raise FirstCutInputUnavailable(
                "first cut requires an empty project timeline"
            )
        source_ids = {
            asset.asset_id for asset in document.assets if "source" in asset.roles
        }
        if not source_ids:
            raise FirstCutInputUnavailable("first cut requires source Assets")
        statuses = {status.asset_id: status for status in statuses_values}
        unavailable = sorted(
            asset_id
            for asset_id in source_ids
            if asset_id not in statuses
            or statuses[asset_id].readiness != "ready"
            or statuses[asset_id].analysis_readiness not in {"ready", "partial"}
        )
        if unavailable:
            raise FirstCutInputUnavailable(
                f"source Assets are not understood yet: {unavailable!r}"
            )
        if not any(moment.asset_id in source_ids for moment in moments):
            raise FirstCutInputUnavailable("first cut requires analyzed Moments")


def _stable_token(*parts: str) -> str:
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{_stable_token(*parts)}"
