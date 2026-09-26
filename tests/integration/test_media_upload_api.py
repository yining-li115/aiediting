from __future__ import annotations

import json
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.routes.assets import (
    get_job_runner,
    get_media_analysis_application,
    get_media_processing_application,
    get_media_upload_application,
)
from apps.api.routes.director import (
    get_director_application,
    get_director_job_runner,
    get_first_cut_workflow,
    get_render_review_application,
)
from apps.api.routes.project_revisions import get_project_revision_application
from apps.api.routes.renders import get_revision_render_application
from aive.agent import (
    DirectorApplication,
    FirstCutWorkflow,
    RenderReviewApplication,
    VideoAgentHarness,
)
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.analysis_repository import SqlAlchemyMediaAnalysisRepository
from aive.db.models import Asset, Base, Job
from aive.db.director_repository import SqlAlchemyDirectorRunRepository
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.media.upload import MediaUploadApplication
from aive.media.analysis import MediaAnalysisApplication
from aive.media.processing import MediaProcessingApplication
from aive.jobs import JobRunner
from aive.project.application import ProjectRevisionApplication
from aive.project.service import CapabilityUse
from aive.rendering import ProjectDocFFmpegRenderer, RevisionRenderApplication
from aive.storage import LocalStorage
from tests.conftest import requires_ffmpeg
from tests.provider_fakes import FixtureFirstCutDirectorProvider


class AcceptCapabilities:
    def rejection_reason(self, capability: CapabilityUse) -> str | None:
        return None


@pytest.fixture
def media_api(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'media-api.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    revision_repository = SqlAlchemyRevisionRepository(sessions)
    asset_repository = SqlAlchemyMediaAssetRepository(sessions)
    ids = iter(
        [
            "commit_initial_media",
            "revision_initial_media",
            "commit_assets_media",
            "revision_assets_media",
            "commit_first_cut_media",
            "revision_first_cut_media",
        ]
    )
    projects = ProjectRevisionApplication(
        revision_repository,
        AcceptCapabilities(),
        asset_authorizer=asset_repository,
        id_factory=lambda _: next(ids),
    )
    storage = LocalStorage(tmp_path / "storage")
    uploads = MediaUploadApplication(
        storage=storage,
        assets=asset_repository,
        projects=projects,
    )
    processing = MediaProcessingApplication(
        storage=storage,
        assets=asset_repository,
        id_factory=lambda _: "job_media_processing",
    )
    analysis = MediaAnalysisApplication(
        storage=storage,
        repository=SqlAlchemyMediaAnalysisRepository(sessions),
        id_factory=lambda _: "job_media_analysis",
    )
    jobs = JobRunner(sessions)
    rendering = RevisionRenderApplication(
        revisions=revision_repository,
        assets=asset_repository,
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=storage,
        renderer=ProjectDocFFmpegRenderer(),
        id_factory=lambda prefix: {
            "artifact": "artifact_first_cut_media",
            "job": "job_render_first_cut_media",
        }[prefix],
    )
    harness = VideoAgentHarness(provider=FixtureFirstCutDirectorProvider())
    director = DirectorApplication(
        runs=SqlAlchemyDirectorRunRepository(sessions),
        projects=projects,
        analysis=SqlAlchemyMediaAnalysisRepository(sessions),
        assets=asset_repository,
        rendering=rendering,
        harness=harness,
        id_factory=lambda prefix: {
            "agent_run": "agent_run_first_cut_media",
            "job": "job_director_first_cut_media",
        }[prefix],
    )
    reviewing = RenderReviewApplication(
        revisions=revision_repository,
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=storage,
        harness=harness,
        id_factory=lambda _: "job_review_first_cut_media",
    )
    first_cuts = FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )

    from apps.api.main import app

    app.dependency_overrides[get_project_revision_application] = lambda: projects
    app.dependency_overrides[get_media_upload_application] = lambda: uploads
    app.dependency_overrides[get_media_processing_application] = lambda: processing
    app.dependency_overrides[get_media_analysis_application] = lambda: analysis
    app.dependency_overrides[get_job_runner] = lambda: jobs
    app.dependency_overrides[get_director_job_runner] = lambda: jobs
    app.dependency_overrides[get_revision_render_application] = lambda: rendering
    app.dependency_overrides[get_director_application] = lambda: director
    app.dependency_overrides[get_render_review_application] = lambda: reviewing
    app.dependency_overrides[get_first_cut_workflow] = lambda: first_cuts
    with TestClient(app) as client:
        yield client, sessions, storage
    app.dependency_overrides.clear()


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/projects",
        json={
            "request_id": "request_create_media",
            "project_id": "project_media",
            "idempotency_key": "create-project-media",
            "origin": "user",
            "intent": {
                "task_mode": "autonomous",
                "target_duration_frames": 900,
                "prompt": "Cut the uploaded footage",
            },
            "output": {
                "width": 1080,
                "height": 1920,
                "fps": {"num": 30, "den": 1},
            },
        },
    )
    assert response.status_code == 201
    return response.json()["revision"]


def _manifest(base_revision_id: str) -> dict:
    return {
        "request_id": "request_upload_media",
        "patch_id": "patch_upload_media",
        "project_id": "project_media",
        "base_revision_id": base_revision_id,
        "idempotency_key": "upload-project-media",
        "origin": "user",
        "items": [{"asset_id": "asset_media_001", "roles": ["source"]}],
    }


@requires_ffmpeg
def test_http_upload_stores_media_and_commits_one_asset_revision(
    media_api, tmp_path
) -> None:
    client, sessions, storage = media_api
    initial = _create_project(client)
    video = tmp_path / "clip.mp4"
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
            "testsrc2=s=160x284:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
    )
    manifest = _manifest(initial["document"]["revision_id"])

    with video.open("rb") as handle:
        uploaded = client.post(
            "/projects/project_media/assets",
            data={"manifest": json.dumps(manifest)},
            files={"files": ("clip.mp4", handle, "video/mp4")},
        )
    with video.open("rb") as handle:
        replay = client.post(
            "/projects/project_media/assets",
            data={"manifest": json.dumps(manifest)},
            files={"files": ("clip.mp4", handle, "video/mp4")},
        )

    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["attempt"]["state"] == "committed"
    assert body["revision"]["document"]["parent_revision_id"] == (
        initial["document"]["revision_id"]
    )
    assert body["revision"]["document"]["assets"] == [body["assets"][0]["asset"]]
    assert body["assets"][0]["width"] == 160
    assert body["assets"][0]["height"] == 284
    assert replay.status_code == 200
    assert replay.json()["attempt"]["state"] == "duplicate"
    assert replay.json()["replayed"] is True
    assert body["processing_job_id"] == "job_media_processing"
    assert replay.json()["processing_job_id"] == "job_media_processing"
    revisions = client.get("/projects/project_media/revisions").json()
    assert len(revisions) == 3
    assert revisions[-1]["provenance"]["origin"] == "agent"
    assert revisions[-1]["document"]["timeline"]["tracks"][0]["role"] == "primary"

    statuses = client.get("/projects/project_media/assets")
    assert statuses.status_code == 200
    assert statuses.json()[0]["readiness"] == "ready"
    assert statuses.json()[0]["proxy_available"] is True
    assert statuses.json()[0]["thumbnail_available"] is True
    assert statuses.json()[0]["analysis_readiness"] == "ready"
    assert statuses.json()[0]["analysis_job_id"] == "job_media_analysis"
    assert statuses.json()[0]["moment_count"] == 1
    assert statuses.json()[0]["transcript_count"] == 0
    proxy = client.get(
        "/projects/project_media/assets/asset_media_001/proxy"
    )
    thumbnail = client.get(
        "/projects/project_media/assets/asset_media_001/thumbnail"
    )
    assert proxy.status_code == 200
    assert proxy.headers["content-type"].startswith("video/mp4")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"].startswith("image/jpeg")
    moments = client.get("/projects/project_media/moments")
    assert moments.status_code == 200
    assert len(moments.json()) == 1
    assert moments.json()[0]["source"] == {
        "start": 0,
        "end": 1_000,
        "timescale": 1_000,
    }
    transcripts = client.get("/projects/project_media/transcripts")
    assert transcripts.status_code == 200
    assert transcripts.json() == []

    with sessions() as session:
        row = session.get(Asset, "asset_media_001")
        assert row is not None
        assert row.project_id == "project_media"
        assert storage.open_local(row.storage_key).read_bytes() == video.read_bytes()
        assert storage.exists(row.proxy_key)
        assert storage.exists(row.thumbnail_key)
        job = session.get(Job, "job_media_processing")
        assert job is not None
        assert job.status == "succeeded"
        assert job.result_json["ready_count"] == 1
        assert job.result_json["analysis_job_id"] == "job_media_analysis"
        analysis_job = session.get(Job, "job_media_analysis")
        assert analysis_job is not None
        assert analysis_job.status == "succeeded"
        assert analysis_job.result_json["moment_count"] == 1
        assert analysis_job.result_json["transcript_count"] == 0
        assert analysis_job.result_json["director_job_id"] == (
            "job_director_first_cut_media"
        )
        director_job = session.get(Job, "job_director_first_cut_media")
        assert director_job is not None
        assert director_job.status == "succeeded"
        render_job = session.get(Job, "job_render_first_cut_media")
        assert render_job is not None
        assert render_job.status == "succeeded"
        review_job = session.get(Job, "job_review_first_cut_media")
        assert review_job is not None
        assert review_job.status == "succeeded"

    first_cut = client.get("/projects/project_media/first-cuts/latest")
    assert first_cut.status_code == 200
    assert first_cut.json()["status"] == "ready"
    current_preview = client.get("/projects/project_media/renders/current")
    assert current_preview.status_code == 200
    assert current_preview.json()["revision_id"] == "revision_first_cut_media"


def test_rejected_batch_does_not_store_or_commit_any_asset(media_api) -> None:
    client, sessions, storage = media_api
    initial = _create_project(client)

    response = client.post(
        "/projects/project_media/assets",
        data={
            "manifest": json.dumps(
                _manifest(initial["document"]["revision_id"])
            )
        },
        files={"files": ("notes.txt", b"not media", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "media_upload_rejected"
    assert response.json()["detail"]["details"]["issues"][0]["code"] == (
        "unsupported_media_type"
    )
    assert client.get("/projects/project_media/revisions/current").json() == initial
    assert list(storage.iter_keys()) == []
    with sessions() as session:
        assert session.get(Asset, "asset_media_001") is None


def test_generic_patch_cannot_attach_an_unregistered_asset(media_api) -> None:
    client, _, _ = media_api
    initial = _create_project(client)

    response = client.post(
        "/projects/project_media/patches",
        json={
            "patch_id": "patch_unregistered_asset",
            "project_id": "project_media",
            "base_revision_id": initial["document"]["revision_id"],
            "idempotency_key": "unregistered-asset",
            "origin": "user",
            "scope": {"kind": "bounded", "target_ids": ["asset_missing"]},
            "operations": [
                {
                    "op": "add_asset",
                    "operation_id": "op_add_missing",
                    "asset": {"asset_id": "asset_missing", "kind": "video"},
                }
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "asset_not_authorized"
