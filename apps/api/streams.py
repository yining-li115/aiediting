"""Server-Sent Events for long-running work.

SSE rather than WebSocket, for the same reason ChatGPT uses it: the traffic is
almost entirely server to client, EventSource reconnects on its own, and it is
plain HTTP so no proxy needs special configuration. The one thing a client
sends mid-job - cancellation - is a separate request, which is simpler than
maintaining a bidirectional connection for it.

The part that is easy to get wrong is reconnection. A page that reloads during
a five-minute analysis must not lose what already happened, so every event
carries a sequence id and the stream replays from ``Last-Event-ID``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from aive.events import bus
from aive.jobs import get_job

# TODO(review): 15s between keepalives. Long enough to be negligible traffic,
# short enough to stay under the idle timeout of common proxies (nginx
# defaults to 60s) so an analysis that is quietly working does not look dead.
KEEPALIVE_S = 15.0


async def job_events(job_id: str, request: Request) -> AsyncIterator[str]:
    last_seen = int(request.headers.get("last-event-id") or 0)

    # Catch up first, then follow. Subscribing before replaying would drop any
    # event published between the two.
    queue = bus.subscribe(job_id)
    try:
        finished = False
        for event in bus.replay(job_id, after_seq=last_seen):
            yield event.as_sse()
            last_seen = event.seq
            if event.type in ("done", "failed"):
                finished = True

        # If the replay already contained the ending, the stream is over. A
        # client reconnecting after the job finished must get a clean close,
        # not a connection that waits forever for an event that has already
        # been sent.
        if finished:
            return

        # Same for a job that reached a terminal state without this stream
        # seeing the event - for instance because the buffer rolled over.
        job = get_job(job_id)
        if bus.is_finished(job_id) or (
            job and job["status"] in ("succeeded", "failed")
        ):
            return

        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_S)
            except asyncio.TimeoutError:
                # A comment line: keeps the connection open through proxies
                # without the client having to handle a heartbeat event type.
                yield ": keepalive\n\n"
                continue

            yield event.as_sse()
            if event.type in ("done", "failed"):
                return
    finally:
        bus.unsubscribe(job_id, queue)


def sse_response(job_id: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        job_events(job_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which turns a live
            # stream into one delivery at the end.
            "X-Accel-Buffering": "no",
        },
    )
