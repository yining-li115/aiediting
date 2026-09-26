from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.contracts.project_doc import (
    AssetRef,
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
from aive.contracts.rendering import CreateRenderRequest
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.models import Asset, Base, Project, ProjectRevisionRow
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.project.hashing import canonical_model_hash
from aive.renderer.base import RenderResult
from aive.rendering.application import RevisionRenderApplication
from aive.storage import LocalStorage


NOW = datetime(2026, 9, 26, 21, 0, tzinfo=timezone.utc)


class FakeRenderer:
    name = "fake-project-renderer"
    version = "1"

    def validate(self, document, output_range=None) -> None:
        assert document.revision_id

    def render(
        self,
        document,
        quality,
        out_path,
        sources,
        output_range=None,
    ) -> RenderResult:
        assert sources == {"asset_001": self.source_path}
        out_path.write_bytes(b"rendered mp4")
        return RenderResult(
            path=out_path,
            quality=quality,
            duration_sec=4.0,
            render_time_ms=25,
            file_size=12,
        )


class Handle:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.events: list[tuple[str, dict]] = []

    def update(self, stage=None, progress=None) -> None:
        self.events.append(("update", {"stage": stage, "progress": progress}))

    def emit(self, type: str, **data) -> None:
        self.events.append((type, data))


def _document(revision_id: str = "revision_001") -> ProjectDoc:
    return ProjectDoc(
        project_id="project_demo",
        revision_id=revision_id,
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=120,
        ),
        assets=(AssetRef(asset_id="asset_001", kind="video"),),
        timeline=Timeline(
            duration_frames=120,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="primary",
                    role="primary",
                    clips=(
                        VideoClip(
                            clip_id="clip",
                            asset_id="asset_001",
                            source=SourceRange(start=0, end=4_000, timescale=1_000),
                            output=FrameRange(
                                start_frame=0,
                                duration_frames=120,
                            ),
                        ),
                    ),
                ),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )


def _application(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    document = _document()
    with sessions.begin() as session:
        session.add(Project(id="project_demo", status="created"))
        session.add(
            ProjectRevisionRow(
                id=document.revision_id,
                project_id=document.project_id,
                parent_revision_id=None,
                commit_id="commit_001",
                document_json=document.model_dump(mode="json"),
                document_hash=canonical_model_hash(document),
                provenance_json={
                    "kind": "initial",
                    "origin": "system",
                    "request_id": "request_initial",
                },
                created_at=NOW,
            )
        )
        session.add(
            Asset(
                id="asset_001",
                project_id="project_demo",
                storage_key="project_demo/original/asset_001.mp4",
                proxy_key="project_demo/proxy/asset_001.mp4",
                thumbnail_key="project_demo/thumbnail/asset_001.jpg",
                readiness="ready",
                kind="video",
                original_name="clip.mp4",
                sha256="a" * 64,
                size_bytes=10,
                duration=4.0,
                width=1080,
                height=1920,
                fps=30.0,
                has_audio=False,
            )
        )
    with sessions.begin() as session:
        session.get(Project, "project_demo").current_revision_id = "revision_001"

    storage = LocalStorage(tmp_path / "storage")
    proxy = storage.put_bytes(
        b"proxy",
        "project_demo/proxy/asset_001.mp4",
    )
    storage.put_bytes(b"original", "project_demo/original/asset_001.mp4")
    renderer = FakeRenderer()
    renderer.source_path = storage.open_local(proxy.key)
    artifacts = SqlAlchemyRenderRepository(sessions)
    application = RevisionRenderApplication(
        revisions=SqlAlchemyRevisionRepository(sessions),
        assets=SqlAlchemyMediaAssetRepository(sessions),
        artifacts=artifacts,
        storage=storage,
        renderer=renderer,
        id_factory=lambda prefix: {
            "artifact": "artifact_001",
            "job": "job_render_001",
        }[prefix],
    )
    return application, artifacts, sessions


def _request() -> CreateRenderRequest:
    return CreateRenderRequest(
        request_id="request_render",
        project_id="project_demo",
        revision_id="revision_001",
        idempotency_key="render-key",
    )


def test_enqueue_process_and_deliver_one_exact_revision(tmp_path) -> None:
    application, _, _ = _application(tmp_path)

    ticket = application.enqueue(_request())
    result = asyncio.run(application.process(Handle(ticket.artifact.job_id)))
    delivery = application.delivery("project_demo", ticket.artifact.artifact_id)

    assert result["status"] == "ready"
    assert delivery.path.read_bytes() == b"rendered mp4"
    assert delivery.artifact.revision_id == "revision_001"
    assert application.current_preview("project_demo") == delivery.artifact


def test_old_revision_preview_never_becomes_the_new_current_preview(tmp_path) -> None:
    application, _, sessions = _application(tmp_path)
    ticket = application.enqueue(_request())
    asyncio.run(application.process(Handle(ticket.artifact.job_id)))

    second = _document("revision_002").model_copy(
        update={"parent_revision_id": "revision_001"}
    )
    with sessions.begin() as session:
        session.add(
            ProjectRevisionRow(
                id="revision_002",
                project_id="project_demo",
                parent_revision_id="revision_001",
                commit_id="commit_002",
                document_json=second.model_dump(mode="json"),
                document_hash=canonical_model_hash(second),
                provenance_json={
                    "kind": "patch",
                    "origin": "user",
                    "patch_id": "patch_002",
                },
                created_at=NOW,
            )
        )
    with sessions.begin() as session:
        session.get(Project, "project_demo").current_revision_id = "revision_002"

    assert application.current_preview("project_demo") is None
