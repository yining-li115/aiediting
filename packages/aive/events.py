"""Progress events, and getting them to a browser.

The pipeline already reports what it is doing - ``analyze_project`` calls back
per segment, ``analyze_reference`` per shot, jobs report a stage. Until now
those callbacks only reached the CLI, which printed them. This module turns
them into a stream a page can subscribe to.

Two design points that matter more than the transport:

* **Events are persisted, not just broadcast.** A subscriber that connects
  late, or reconnects after a reload, must be able to catch up. Without a
  replay log, refreshing the page during a five-minute analysis loses
  everything that happened before the refresh - which is the one thing users
  do most when they think something is stuck.
* **Publishing is decoupled from transport.** Pipeline code calls
  ``publish()``; whether that ends up in an SSE stream, a log, or a test
  assertion is not its concern. The CLI and the web UI consume the same
  events.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# TODO(review): how many events to keep per job for replay. A long analysis
# emits roughly one per segment, so a few hundred covers a large project with
# room to spare. Kept in memory: a restart loses history, which is acceptable
# while the worker and the API are one process and will not be once they are
# not.
REPLAY_BUFFER = 500

# TODO(review): how long a finished job's events stay available. Long enough
# that a user who reloads right after completion still sees the result.
RETAIN_AFTER_FINISH_S = 15 * 60


@dataclass
class Event:
    """One thing that happened, addressed to one job."""

    job_id: str
    seq: int
    type: str
    data: dict[str, Any]
    at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        )
    )

    def as_sse(self) -> str:
        """Server-Sent Events wire format.

        ``id`` is what makes reconnection work: the browser sends it back as
        Last-Event-ID and the server replays from there.
        """
        payload = json.dumps(
            {"job_id": self.job_id, "at": self.at, **self.data},
            ensure_ascii=False,
        )
        return f"id: {self.seq}\nevent: {self.type}\ndata: {payload}\n\n"


class EventBus:
    """Per-job fan-out with a replay buffer."""

    def __init__(self) -> None:
        self._history: dict[str, deque[Event]] = defaultdict(
            lambda: deque(maxlen=REPLAY_BUFFER)
        )
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)
        self._counters: dict[str, int] = defaultdict(int)
        self._finished: dict[str, float] = {}

    # --- producing ---------------------------------------------------------

    def publish(self, job_id: str, type: str, **data: Any) -> Event:
        self._counters[job_id] += 1
        event = Event(
            job_id=job_id, seq=self._counters[job_id], type=type, data=data
        )
        self._history[job_id].append(event)

        for queue in list(self._subscribers[job_id]):
            # Never block the pipeline on a slow reader: a browser tab that
            # stopped consuming must not stall an analysis.
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

        if type in ("done", "failed"):
            # time.monotonic, not the loop clock: publishing happens from the
            # CLI and from tests where no loop is running, and a timekeeping
            # detail must not decide where this can be called from.
            self._finished[job_id] = time.monotonic()
        return event

    # --- consuming ---------------------------------------------------------

    def replay(self, job_id: str, after_seq: int = 0) -> list[Event]:
        return [e for e in self._history.get(job_id, ()) if e.seq > after_seq]

    def subscribe(self, job_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        self._subscribers[job_id].add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[Event]) -> None:
        self._subscribers[job_id].discard(queue)
        if not self._subscribers[job_id]:
            self._subscribers.pop(job_id, None)

    def is_finished(self, job_id: str) -> bool:
        return job_id in self._finished

    # --- housekeeping ------------------------------------------------------

    def forget(self, job_id: str) -> None:
        self._history.pop(job_id, None)
        self._counters.pop(job_id, None)
        self._finished.pop(job_id, None)
        self._subscribers.pop(job_id, None)

    def sweep(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        stale = [
            job_id
            for job_id, at in self._finished.items()
            if now - at > RETAIN_AFTER_FINISH_S and not self._subscribers.get(job_id)
        ]
        for job_id in stale:
            self.forget(job_id)
        return len(stale)


bus = EventBus()


def publish(job_id: str | None, type: str, **data: Any) -> None:
    """Publish, tolerating a missing job id.

    Pipeline functions are used from the CLI as well, where there is no job to
    report to. Making the id optional keeps the call sites free of conditionals.
    """
    if job_id:
        bus.publish(job_id, type, **data)
