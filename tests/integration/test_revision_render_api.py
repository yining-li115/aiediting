from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.routes.renders import (
    get_render_job_runner,
    get_revision_render_application,
)
from aive.contracts.project_doc import (
    AssetRef,
    AudioClip,
    AudioTrack,
    DependencyRef,
    DependencySet,
    FrameRange,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    SourceRange,
    Timeline,
    VideoClip,
    VideoTrack,
)
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.models import Asset, Base, Project, ProjectRevisionRow
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.jobs import JobRunner
from aive.project.hashing import canonical_model_hash
from aive.rendering import ProjectDocFFmpegRenderer, RevisionRenderApplication
from aive.storage import LocalStorage
from tests.conftest import requires_ffmpeg


NOW = datetime(2026, 9, 26, 22, 0, tzinfo=timezone.utc)


def _document() -> ProjectDoc:
    return ProjectDoc(
        project_id="project_render_api",
        revision_id="revision_render_api",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=30,
        ),
        assets=(AssetRef(asset_id="asset_render_api", kind="video"),),
        timeline=Timeline(
            duration_frames=30,
            output=OutputSettings(
                width=160,
                height=284,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="primary",
                    role="primary",
                    clips=(
                        VideoClip(
                            clip_id="clip",
                            asset_id="asset_render_api",
                            source=SourceRange(start=0, end=1_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=30),
                        ),
                    ),
                ),
                AudioTrack(
                    track_id="source_audio",
                    role="source",
                    clips=(
                        AudioClip(
                            clip_id="audio",
                            asset_id="asset_render_api",
                            source=SourceRange(start=0, end=1_000, timescale=1_000),
                            output=FrameRange(start_frame=0, duration_frames=30),
                            fade_in_frames=3,
                            fade_out_frames=3,
                        ),
                    ),
                ),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )


@pytest.fixture
def render_api(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'render-api.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    storage = LocalStorage(tmp_path / "storage")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=purple:s=160x284:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:d=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    original_key = "project_render_api/original/asset_render_api.mp4"
    proxy_key = "project_render_api/proxy/asset_render_api.mp4"
    storage.put(source, original_key)
    storage.put(source, proxy_key)
    document = _document()
    with sessions.begin() as session:
        session.add(Project(id=document.project_id, status="created"))
        session.add(
            ProjectRevisionRow(
                id=document.revision_id,
                project_id=document.project_id,
                parent_revision_id=None,
                commit_id="commit_render_api",
                document_json=document.model_dump(mode="json"),
                document_hash=canonical_model_hash(document),
                provenance_json={
                    "kind": "initial",
                    "origin": "system",
                    "request_id": "request_initial_render_api",
                },
                created_at=NOW,
            )
        )
        session.add(
            Asset(
                id="asset_render_api",
                project_id=document.project_id,
                storage_key=original_key,
                proxy_key=proxy_key,
                thumbnail_key="project_render_api/thumbnail/asset_render_api.jpg",
                readiness="ready",
                kind="video",
                original_name="source.mp4",
                sha256="a" * 64,
                size_bytes=source.stat().st_size,
                duration=1.0,
                width=160,
                height=284,
                fps=30.0,
                has_audio=True,
            )
        )
    with sessions.begin() as session:
        session.get(Project, document.project_id).current_revision_id = (
            document.revision_id
        )

    ids = iter(("artifact_render_api", "job_render_api"))
    application = RevisionRenderApplication(
        revisions=SqlAlchemyRevisionRepository(sessions),
        assets=SqlAlchemyMediaAssetRepository(sessions),
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=storage,
        renderer=ProjectDocFFmpegRenderer(),
        id_factory=lambda _: next(ids),
    )
    jobs = JobRunner(sessions)

    from apps.api.main import app

    app.dependency_overrides[get_revision_render_application] = lambda: application
    app.dependency_overrides[get_render_job_runner] = lambda: jobs
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@requires_ffmpeg
def test_http_render_job_produces_revision_bound_browser_media(render_api) -> None:
    response = render_api.post(
        "/projects/project_render_api/renders",
        json={
            "request_id": "request_render_api",
            "project_id": "project_render_api",
            "revision_id": "revision_render_api",
            "idempotency_key": "render-api-key",
            "kind": "preview",
            "quality": "proxy",
        },
    )

    assert response.status_code == 200
    artifact_id = response.json()["artifact"]["artifact_id"]
    artifact = render_api.get(
        f"/projects/project_render_api/renders/{artifact_id}"
    )
    assert artifact.status_code == 200
    assert artifact.json()["status"] == "ready"
    assert artifact.json()["revision_id"] == "revision_render_api"

    current = render_api.get("/projects/project_render_api/renders/current")
    assert current.status_code == 200
    assert current.json()["artifact_id"] == artifact_id

    content = render_api.get(
        f"/projects/project_render_api/renders/{artifact_id}/content"
    )
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("video/mp4")
    assert len(content.content) > 100


@requires_ffmpeg
def test_http_range_preview_renders_only_the_requested_frames(render_api) -> None:
    response = render_api.post(
        "/projects/project_render_api/renders",
        json={
            "request_id": "request_range_render_api",
            "project_id": "project_render_api",
            "revision_id": "revision_render_api",
            "idempotency_key": "range-render-api-key",
            "kind": "preview",
            "quality": "proxy",
            "output_range": {"start_frame": 6, "duration_frames": 12},
        },
    )

    assert response.status_code == 200
    artifact_id = response.json()["artifact"]["artifact_id"]
    artifact = render_api.get(
        f"/projects/project_render_api/renders/{artifact_id}"
    ).json()
    assert artifact["status"] == "ready"
    assert artifact["output_range"] == {
        "start_frame": 6,
        "duration_frames": 12,
    }
    assert 0.35 <= artifact["duration_seconds"] <= 0.5
