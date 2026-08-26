"""In-process job runner.

The pipeline takes minutes, so requests cannot block on it. A jobs table plus
an in-process worker and client polling is enough for single-person
development and internal testing; a real queue (RQ / Celery) is deliberately
deferred until closed beta, since it adds deployment and debugging surface
without answering any product question.
"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from .db.models import Job
from .db.session import session_scope
from .events import publish
from .specs.base import new_id


class Stage(str, Enum):
    """Progress labels shown to the client."""

    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    MATCHING = "matching"
    SCORING = "scoring"
    RENDERING_PROXY = "rendering_proxy"
    RENDERING_HQ = "rendering_hq"
    DONE = "done"
    FAILED = "failed"


class JobHandle:
    """Passed into the worker so it can report progress as it goes."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def update(
        self,
        stage: Stage | None = None,
        progress: float | None = None,
    ) -> None:
        with session_scope() as session:
            job = session.get(Job, self.job_id)
            if job is None:
                return
            if stage is not None:
                job.stage = stage.value
            if progress is not None:
                job.progress = max(0.0, min(1.0, progress))

        publish(
            self.job_id,
            "stage",
            stage=stage.value if stage else None,
            progress=progress,
        )

    def emit(self, type: str, **data) -> None:
        """Report something specific that just happened.

        Used for the per-item events - a segment analysed, a candidate scored -
        that make a long job feel like it is doing something rather than
        sitting on a percentage.
        """
        publish(self.job_id, type, **data)


def create_job(kind: str, project_id: str | None = None) -> str:
    job_id = new_id("job")
    with session_scope() as session:
        session.add(
            Job(id=job_id, project_id=project_id, kind=kind, status="queued")
        )
    return job_id


def kind_of(job_id: str) -> str | None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        return job.kind if job else None


def _finish(job_id: str, status: str, result: Any = None, error: str | None = None):
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        job.stage = Stage.DONE.value if status == "succeeded" else Stage.FAILED.value
        job.progress = 1.0 if status == "succeeded" else job.progress
        job.result_json = result
        job.error = error


async def run_job(
    job_id: str, work: Callable[[JobHandle], Awaitable[Any]]
) -> None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is not None:
            job.status = "running"
    publish(job_id, "started", kind=kind_of(job_id))
    try:
        result = await work(JobHandle(job_id))
        _finish(job_id, "succeeded", result=result)
        publish(job_id, "done", result=result)
    except Exception as exc:  # noqa: BLE001 - the job records its own failure
        message = f"{exc}"
        _finish(job_id, "failed", error=f"{message}\n{traceback.format_exc()[:4000]}")
        # Failure is an event like any other: a stream that simply stops
        # leaves the page unable to tell "still working" from "died".
        publish(job_id, "failed", error=message)


def spawn(
    kind: str,
    work: Callable[[JobHandle], Awaitable[Any]],
    project_id: str | None = None,
) -> str:
    """Queue a job on the running event loop and return its id immediately.

    Must be called from inside a running loop; ``asyncio.create_task`` raises a
    clear error otherwise, which is better than the silent loop creation that
    ``get_event_loop()`` used to do.
    """
    job_id = create_job(kind, project_id)
    asyncio.create_task(run_job(job_id, work))
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        return {
            "job_id": job.id,
            "kind": job.kind,
            "status": job.status,
            "stage": job.stage,
            "progress": job.progress,
            "error": job.error,
            "result": job.result_json,
        }
