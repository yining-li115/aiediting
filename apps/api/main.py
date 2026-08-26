"""HTTP surface.

Block A exposes only what already works: health, database init, timeline
validation and rendering as a background job. Upload and generation endpoints
arrive with blocks B and C.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .streams import sse_response

from aive.config import settings
from aive.db.session import init_db
from aive.jobs import Stage, create_job, get_job, run_job
from aive.providers.cost import install_db_cost_sink
from aive.renderer import FFmpegRenderer
from aive.specs import TimelineSpec, validate_timeline

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    install_db_cost_sink()
    yield


app = FastAPI(title="aive", version="0.1.0", lifespan=lifespan)

# The web app runs on its own dev server, so it is a different origin.
# TODO(review): wide open for local development. Must be narrowed to the real
# origin before anything is deployed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)





@app.get("/health")
def health() -> dict:
    return {"status": "ok", "media_root": str(settings.media_root)}


@app.post("/validate")
def validate(timeline: dict) -> dict:
    spec = TimelineSpec.model_validate(timeline)
    violations = validate_timeline(spec)
    return {
        "ok": not any(v.severity == "hard" for v in violations),
        "violations": [v.model_dump() for v in violations],
    }


@app.post("/renders")
def create_render(
    payload: dict,
    background: BackgroundTasks,
) -> dict:
    """Queue a render. The client polls GET /jobs/{id}."""
    spec = TimelineSpec.model_validate(payload["timeline"])
    quality = payload.get("quality", "proxy")
    out_path = Path(payload.get("out", f"out/{spec.timeline_id}_{quality}.mp4"))
    root = Path(payload.get("media_root", settings.media_root))

    sources: dict[str, Path] = {}
    for clip in spec.clips:
        matches = list(root.glob(f"{clip.source_id}.*"))
        if not matches:
            raise HTTPException(404, f"no media for source_id {clip.source_id}")
        sources[clip.source_id] = matches[0]

    job_id = create_job("render", spec.project_id)

    async def work(handle):
        handle.update(
            stage=Stage.RENDERING_PROXY if quality == "proxy" else Stage.RENDERING_HQ,
            progress=0.1,
        )
        result = FFmpegRenderer().render(spec, quality, out_path, sources)
        return result.as_dict()

    background.add_task(run_job, job_id, work)
    return {"job_id": job_id}


@app.get("/jobs/{job_id}/stream")
async def job_stream(job_id: str, request: Request):
    """Live progress for one job.

    Polling was the fallback; this is the intended path. Reconnects replay from
    Last-Event-ID, so reloading the page mid-job does not lose the history.
    """
    if get_job(job_id) is None:
        raise HTTPException(404, "unknown job")
    return sse_response(job_id, request)


@app.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job
