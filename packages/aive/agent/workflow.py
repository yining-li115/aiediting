"""Durable upload-to-first-preview orchestration for autonomous projects."""

from __future__ import annotations

from typing import Any

from ..contracts.director import CreateFirstCutRequest, DirectorRun, FirstCutTicket
from ..jobs import JobRunner
from ..media.analysis import MediaAnalysisApplication
from ..media.processing import MediaProcessingApplication, ProgressReporter
from ..media.repository import MediaProcessingTicket
from ..rendering.application import RevisionRenderApplication
from .application import DirectorApplication
from .render_review_application import RenderReviewApplication


class FirstCutWorkflow:
    """Runs one Director Job and its automatically created Render Job."""

    def __init__(
        self,
        *,
        jobs: JobRunner,
        director: DirectorApplication,
        rendering: RevisionRenderApplication,
        reviewing: RenderReviewApplication,
    ) -> None:
        self._jobs = jobs
        self._director = director
        self._rendering = rendering
        self._reviewing = reviewing

    def enqueue(self, request: CreateFirstCutRequest) -> FirstCutTicket:
        return self._director.enqueue(request)

    def enqueue_automatic(
        self, project_id: str, upstream_job_id: str
    ) -> FirstCutTicket | None:
        return self._director.enqueue_automatic(project_id, upstream_job_id)

    def get(self, project_id: str, run_id: str) -> DirectorRun | None:
        return self._director.get(project_id, run_id)

    def latest(self, project_id: str) -> DirectorRun | None:
        return self._director.latest(project_id)

    async def run(self, director_job_id: str) -> None:
        await self._jobs.run(director_job_id, self._director.process)
        await self.resume_review(director_job_id)

    async def resume_review(self, director_job_id: str) -> None:
        """Continue an interrupted candidate render/review/accept sequence."""

        while True:
            run = self._director.get_job_run(director_job_id)
            if run.status == "autofixing":
                if run.render_autofix_job_id is None:
                    return
                await self._jobs.run(
                    run.render_autofix_job_id,
                    self._director.process_render_autofix,
                )
                run = self._director.get_job_run(director_job_id)
                if run.status not in {"awaiting_review", "reviewing"}:
                    return
            if run.render_job_id is None:
                return
            await self._jobs.run(run.render_job_id, self._rendering.process)
            run = self._director.get_job_run(director_job_id)
            if run.render_artifact_id is None:
                return
            artifact = self._rendering.get(run.project_id, run.render_artifact_id)
            if artifact.status != "ready":
                return
            ticket = self._reviewing.enqueue(run.project_id, artifact.artifact_id)
            review_job_id = ticket.artifact.review_job_id
            if review_job_id is None:
                return
            await self._jobs.run(review_job_id, self._reviewing.process)
            reviewed = self._reviewing.get(run.project_id, artifact.artifact_id)
            if reviewed.review_status in {"accepted", "rejected"}:
                result = self._director.complete_render_review(
                    director_job_id,
                    reviewed,
                )
                if result.status == "autofixing":
                    continue
            return


class AutonomousProjectWorkflow:
    """Sequences durable jobs without making any of them own project state."""

    def __init__(
        self,
        *,
        jobs: JobRunner,
        processing: MediaProcessingApplication,
        analysis: MediaAnalysisApplication,
        first_cuts: FirstCutWorkflow,
    ) -> None:
        self._jobs = jobs
        self._processing = processing
        self._analysis = analysis
        self._first_cuts = first_cuts

    def enqueue_processing(
        self, project_id: str, asset_ids: tuple[str, ...]
    ) -> MediaProcessingTicket:
        return self._processing.enqueue(project_id, asset_ids)

    async def run_processing_then_first_cut(self, processing_job_id: str) -> None:
        project_id, asset_ids = self._processing.job_targets(processing_job_id)
        analysis_job_id: str | None = None

        async def work(handle: ProgressReporter) -> dict[str, Any]:
            nonlocal analysis_job_id
            result = await self._processing.process(handle)
            ticket = self._analysis.enqueue(project_id, asset_ids)
            analysis_job_id = ticket.job_id
            return {**result, "analysis_job_id": ticket.job_id}

        await self._jobs.run(processing_job_id, work)
        if analysis_job_id is None:
            job = self._jobs.get(processing_job_id)
            result = job.get("result") if job is not None else None
            if isinstance(result, dict):
                analysis_job_id = result.get("analysis_job_id")
        if analysis_job_id is not None:
            await self.run_analysis_then_first_cut(analysis_job_id)

    async def run_analysis_then_first_cut(self, analysis_job_id: str) -> None:
        project_id, _ = self._analysis.job_targets(analysis_job_id)
        director_job_id: str | None = None

        async def work(handle: ProgressReporter) -> dict[str, Any]:
            nonlocal director_job_id
            result = await self._analysis.process(handle)
            ticket = self._first_cuts.enqueue_automatic(project_id, analysis_job_id)
            director_job_id = ticket.run.job_id if ticket is not None else None
            return {**result, "director_job_id": director_job_id}

        await self._jobs.run(analysis_job_id, work)
        if director_job_id is None:
            job = self._jobs.get(analysis_job_id)
            result = job.get("result") if job is not None else None
            if isinstance(result, dict):
                director_job_id = result.get("director_job_id")
        if director_job_id is not None:
            await self._first_cuts.run(director_job_id)
