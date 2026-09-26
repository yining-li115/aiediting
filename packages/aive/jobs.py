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

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from .db.models import Job
from .db.session import get_session_factory
from .events import publish
from .specs.base import new_id


class Stage(str, Enum):
    """Progress labels shown to the client."""

    INGESTING = "ingesting"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    MATCHING = "matching"
    SCORING = "scoring"
    GENERATING_PROXY = "generating_proxy"
    GENERATING_THUMBNAIL = "generating_thumbnail"
    RENDERING_PROXY = "rendering_proxy"
    RENDERING_HQ = "rendering_hq"
    REVIEWING = "reviewing"
    DONE = "done"
    FAILED = "failed"


class JobHandle:
    """Passed into the worker so it can report progress as it goes."""

    def __init__(
        self,
        job_id: str,
        sessions: sessionmaker[Session] | None = None,
    ) -> None:
        self.job_id = job_id
        self._sessions = sessions or get_session_factory()

    def update(
        self,
        stage: Stage | None = None,
        progress: float | None = None,
    ) -> None:
        with self._sessions.begin() as session:
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


class JobRunner:
    """Durable lifecycle updates around an in-process unit of work."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def create(self, kind: str, project_id: str | None = None) -> str:
        job_id = new_id("job")
        with self._sessions.begin() as session:
            session.add(
                Job(id=job_id, project_id=project_id, kind=kind, status="queued")
            )
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._sessions() as session:
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

    def kind_of(self, job_id: str) -> str | None:
        with self._sessions() as session:
            job = session.get(Job, job_id)
            return job.kind if job else None

    def recoverable(self, kind: str) -> tuple[str, ...]:
        """Requeue durable work interrupted before process shutdown."""
        with self._sessions.begin() as session:
            jobs = session.scalars(
                select(Job)
                .where(
                    Job.kind == kind,
                    Job.status.in_(("queued", "running")),
                )
                .order_by(Job.created_at, Job.id)
            ).all()
            for job in jobs:
                job.status = "queued"
            return tuple(job.id for job in jobs)

    def finish(
        self,
        job_id: str,
        status: str,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        with self._sessions.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.status = status
            job.stage = (
                Stage.DONE.value if status == "succeeded" else Stage.FAILED.value
            )
            job.progress = 1.0 if status == "succeeded" else job.progress
            job.result_json = result
            job.error = error

    async def run(
        self,
        job_id: str,
        work: Callable[[JobHandle], Awaitable[Any]],
    ) -> bool:
        with self._sessions.begin() as session:
            claimed = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "queued")
                .values(status="running")
            )
            if claimed.rowcount != 1:
                return False
        publish(job_id, "started", kind=self.kind_of(job_id))
        try:
            result = await work(JobHandle(job_id, self._sessions))
            self.finish(job_id, "succeeded", result=result)
            publish(job_id, "done", result=result)
            return True
        except Exception as exc:  # noqa: BLE001 - process boundary records failure
            message = f"{exc}"
            self.finish(
                job_id,
                "failed",
                error=f"{message}\n{traceback.format_exc()[:4000]}",
            )
            publish(job_id, "failed", error=message)
            return True


def create_job(kind: str, project_id: str | None = None) -> str:
    return JobRunner(get_session_factory()).create(kind, project_id)


def kind_of(job_id: str) -> str | None:
    return JobRunner(get_session_factory()).kind_of(job_id)


async def run_job(
    job_id: str, work: Callable[[JobHandle], Awaitable[Any]]
) -> bool:
    return await JobRunner(get_session_factory()).run(job_id, work)


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
    return JobRunner(get_session_factory()).get(job_id)
