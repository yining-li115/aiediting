"""Durable orchestration for processing ready media into understood Moments."""

from __future__ import annotations

from typing import Any

from ..jobs import JobRunner
from .analysis import MediaAnalysisApplication
from .processing import MediaProcessingApplication, ProgressReporter
from .repository import MediaProcessingTicket


class MediaPipelineCoordinator:
    def __init__(
        self,
        *,
        jobs: JobRunner,
        processing: MediaProcessingApplication,
        analysis: MediaAnalysisApplication,
    ) -> None:
        self._jobs = jobs
        self._processing = processing
        self._analysis = analysis

    def enqueue_processing(
        self, project_id: str, asset_ids: tuple[str, ...]
    ) -> MediaProcessingTicket:
        return self._processing.enqueue(project_id, asset_ids)

    async def run_processing_then_analysis(self, processing_job_id: str) -> None:
        analysis_job_id: str | None = None

        async def work(handle: ProgressReporter) -> dict[str, Any]:
            nonlocal analysis_job_id
            result = await self._processing.process(handle)
            project_id, asset_ids = self._processing.job_targets(processing_job_id)
            ticket = self._analysis.enqueue(project_id, asset_ids)
            analysis_job_id = ticket.job_id
            return {**result, "analysis_job_id": ticket.job_id}

        claimed = await self._jobs.run(processing_job_id, work)
        if claimed and analysis_job_id is not None:
            await self._jobs.run(analysis_job_id, self._analysis.process)

    async def run_analysis(self, analysis_job_id: str) -> None:
        await self._jobs.run(analysis_job_id, self._analysis.process)
