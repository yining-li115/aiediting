"""Progress events and the SSE stream.

The properties that matter are about *reconnection*, not about throughput. A
page reloaded during a five-minute analysis is the normal case - it is what
people do when they think something is stuck - and losing the history at that
moment is the failure this design exists to prevent.
"""

from __future__ import annotations

import asyncio

import pytest

from aive.events import REPLAY_BUFFER, EventBus


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def test_events_are_numbered_in_order(bus) -> None:
    for index in range(3):
        bus.publish("j1", "segment", index=index)
    assert [e.seq for e in bus.replay("j1")] == [1, 2, 3]


def test_replay_lets_a_reconnecting_client_catch_up(bus) -> None:
    """The whole reason events are stored rather than only broadcast."""
    for index in range(5):
        bus.publish("j1", "segment", index=index)

    # A browser that saw up to id 2 reconnects and asks for the rest.
    missed = bus.replay("j1", after_seq=2)
    assert [e.data["index"] for e in missed] == [2, 3, 4]


def test_a_late_subscriber_sees_the_whole_history(bus) -> None:
    bus.publish("j1", "stage", stage="analyzing")
    bus.publish("j1", "segment", index=0)
    assert len(bus.replay("j1")) == 2


async def test_subscribers_receive_live_events(bus) -> None:
    queue = bus.subscribe("j1")
    bus.publish("j1", "segment", index=0)
    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event.type == "segment"


def test_jobs_are_isolated_from_each_other(bus) -> None:
    bus.publish("j1", "segment", index=0)
    bus.publish("j2", "segment", index=99)
    assert len(bus.replay("j1")) == 1
    assert bus.replay("j2")[0].data["index"] == 99


def test_a_stalled_reader_cannot_block_the_pipeline(bus) -> None:
    """A browser tab that stopped consuming must not stall an analysis."""
    queue = bus.subscribe("j1")
    for index in range(1000):  # far beyond the queue's capacity
        bus.publish("j1", "segment", index=index)
    assert queue.qsize() <= 256
    # The history is still complete up to the buffer limit, so the tab can
    # recover by reconnecting and replaying.
    assert len(bus.replay("j1")) == REPLAY_BUFFER


def test_terminal_events_are_marked_finished(bus) -> None:
    """A stream that simply stops leaves a page unable to tell 'still working'
    from 'died'."""
    assert not bus.is_finished("j1")
    bus.publish("j1", "done", result={"ok": True})
    assert bus.is_finished("j1")


def test_sse_format_carries_the_id_and_event_name(bus) -> None:
    event = bus.publish("j1", "segment", segment_id="a001_seg01")
    wire = event.as_sse()
    assert wire.startswith("id: 1\n")
    assert "event: segment\n" in wire
    assert '"segment_id": "a001_seg01"' in wire
    assert wire.endswith("\n\n")  # the blank line that ends an SSE record


def test_publish_tolerates_a_missing_job_id() -> None:
    """Pipeline code runs from the CLI too, where there is no job to report to."""
    from aive.events import publish

    publish(None, "segment", index=0)  # must not raise


# --- the HTTP stream --------------------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """The API with an isolated database, for streaming tests."""
    from fastapi.testclient import TestClient

    import aive.db.session as session_mod
    from aive.config import settings

    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path/'sse.db'}")
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)
    session_mod.init_db()

    # Imported the way uvicorn imports it. Reaching it through a different
    # path is how a broken import survived the test suite.
    from apps.api.main import app

    with TestClient(app) as client:
        yield client


def read_events(response, limit: int = 20) -> list[tuple[str, str]]:
    """Parse an SSE body into (id, event) pairs."""
    out: list[tuple[str, str]] = []
    current_id = ""
    for line in response.iter_lines():
        if line.startswith("id:"):
            current_id = line.split(":", 1)[1].strip()
        elif line.startswith("event:"):
            out.append((current_id, line.split(":", 1)[1].strip()))
            if len(out) >= limit or out[-1][1] in ("done", "failed"):
                break
    return out


def test_stream_delivers_events_and_ends_on_done(api_client) -> None:
    from aive.events import bus
    from aive.jobs import create_job

    job_id = create_job("analyze", "proj_1")
    bus.publish(job_id, "stage", stage="analyzing", progress=0.1)
    bus.publish(job_id, "segment", segment_id="a001_seg01")
    bus.publish(job_id, "segment", segment_id="a001_seg02")
    bus.publish(job_id, "done", result={"segments": 2})

    with api_client.stream("GET", f"/jobs/{job_id}/stream") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = read_events(response)

    assert [name for _, name in events] == ["stage", "segment", "segment", "done"]
    assert [id_ for id_, _ in events] == ["1", "2", "3", "4"]
    bus.forget(job_id)


def test_reconnecting_replays_only_what_was_missed(api_client) -> None:
    """The reason ids exist: a page reloaded mid-job catches up instead of
    starting blank."""
    from aive.events import bus
    from aive.jobs import create_job

    job_id = create_job("analyze", "proj_1")
    for index in range(4):
        bus.publish(job_id, "segment", index=index)
    bus.publish(job_id, "done", result={})

    with api_client.stream(
        "GET", f"/jobs/{job_id}/stream", headers={"Last-Event-ID": "3"}
    ) as response:
        events = read_events(response)

    # Only ids 4 and 5, not the whole history again.
    assert [id_ for id_, _ in events] == ["4", "5"]
    bus.forget(job_id)


def test_unknown_job_is_rejected(api_client) -> None:
    assert api_client.get("/jobs/job_nope/stream").status_code == 404


def test_the_api_imports_the_way_uvicorn_imports_it() -> None:
    """A regression guard for a real outage.

    `apps/api/main.py` used a bare `import streams`, which resolved in the test
    suite only because the test had put `apps/api` on sys.path. uvicorn loads
    the module as `apps.api.main`, where that import does not exist, so the
    server refused to start while every test passed.

    Importing by the same dotted path the server uses is what makes the suite
    able to catch it.
    """
    import importlib

    module = importlib.import_module("apps.api.main")
    assert module.app.title == "aive"
