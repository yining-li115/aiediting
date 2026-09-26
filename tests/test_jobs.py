"""The job runner: progress reporting, success, and failure capture.

A long job that fails silently is worse than one that never starts, so the
failure path is tested as carefully as the happy one.
"""

from __future__ import annotations

import pytest

import aive.db.session as session_mod
from aive.jobs import Stage, create_job, get_job, run_job, spawn


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point the global engine at a throwaway database for one test."""
    from aive.config import settings

    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path/'jobs.db'}")
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)
    session_mod.init_db()
    yield
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)


async def test_successful_job_records_stages_and_result(isolated_db) -> None:
    job_id = create_job("render", project_id="proj_1")
    assert get_job(job_id)["status"] == "queued"

    seen: list[str] = []

    async def work(handle):
        handle.update(stage=Stage.RENDERING_PROXY, progress=0.5)
        seen.append(get_job(job_id)["stage"])
        return {"path": "out/x.mp4"}

    await run_job(job_id, work)

    assert seen == [Stage.RENDERING_PROXY.value]
    job = get_job(job_id)
    assert job["status"] == "succeeded"
    assert job["stage"] == Stage.DONE.value
    assert job["progress"] == 1.0
    assert job["result"] == {"path": "out/x.mp4"}
    assert job["error"] is None


async def test_failed_job_captures_the_error(isolated_db) -> None:
    job_id = create_job("render", project_id="proj_1")

    async def work(handle):
        raise RuntimeError("ffmpeg exploded")

    # A failing job must not propagate; it records its own failure so the
    # client polling GET /jobs/{id} sees why instead of a dead job.
    await run_job(job_id, work)

    job = get_job(job_id)
    assert job["status"] == "failed"
    assert job["stage"] == Stage.FAILED.value
    assert "ffmpeg exploded" in job["error"]


async def test_job_can_only_be_claimed_once(isolated_db) -> None:
    job_id = create_job("media_analysis", project_id="proj_1")
    calls = 0

    async def work(handle):
        nonlocal calls
        calls += 1
        return {"ok": True}

    first = await run_job(job_id, work)
    second = await run_job(job_id, work)

    assert first is True
    assert second is False
    assert calls == 1


async def test_spawn_returns_immediately(isolated_db) -> None:
    import asyncio

    started = asyncio.Event()

    async def work(handle):
        started.set()
        await asyncio.sleep(0)
        return "done"

    job_id = spawn("render", work, project_id="proj_1")
    assert get_job(job_id) is not None  # id usable before the work finishes
    await asyncio.wait_for(started.wait(), timeout=2)
    for _ in range(50):
        if get_job(job_id)["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert get_job(job_id)["status"] == "succeeded"


def test_unknown_job_is_none(isolated_db) -> None:
    assert get_job("job_nope") is None


def test_interrupted_durable_jobs_are_requeued(isolated_db) -> None:
    from aive.db.models import Job
    from aive.db.session import get_session_factory
    from aive.jobs import JobRunner

    sessions = get_session_factory()
    with sessions.begin() as session:
        session.add(
            Job(
                id="job_interrupted",
                project_id="project_001",
                kind="media_processing",
                status="running",
                stage="generating_proxy",
            )
        )

    runner = JobRunner(sessions)

    assert runner.recoverable("media_processing") == ("job_interrupted",)
    assert runner.get("job_interrupted")["status"] == "queued"
