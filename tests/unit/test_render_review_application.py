from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.agent.harness import VideoAgentHarness
from aive.agent.render_review import RenderReviewEngine
from aive.agent.render_review_application import RenderReviewApplication
from aive.contracts.project_doc import (
    DependencyRef,
    DependencySet,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    Timeline,
)
from aive.contracts.rendering import CreateRenderRequest, RenderInspection
from aive.db.models import Base, Project, ProjectRevisionRow
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.jobs import JobRunner
from aive.project.hashing import canonical_model_hash
from aive.storage import LocalStorage
from tests.provider_fakes import (
    FixtureFirstCutDirectorProvider,
    FixtureRenderReviewerProvider,
)


NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


class FakeInspector:
    def inspect(self, media_path, sample_dir):
        assert media_path.read_bytes() == b"preview"
        return (
            RenderInspection(
                duration_seconds=2,
                width=540,
                height=960,
                fps=30,
                has_video=True,
                has_audio=False,
                sampled_frame_count=5,
            ),
            (),
        )


def test_render_review_application_persists_context_and_report(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'review-app.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    document = ProjectDoc(
        project_id="project_review_app",
        revision_id="revision_review_app",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="Create a concise opening",
        ),
        timeline=Timeline(
            duration_frames=60,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )
    with sessions.begin() as session:
        session.add(
            Project(
                id=document.project_id,
                status="created",
                current_revision_id=document.revision_id,
            )
        )
        session.add(
            ProjectRevisionRow(
                id=document.revision_id,
                project_id=document.project_id,
                parent_revision_id=None,
                commit_id="commit_review_app",
                document_json=document.model_dump(mode="json"),
                document_hash=canonical_model_hash(document),
                provenance_json={"kind": "initial", "origin": "system"},
                created_at=NOW,
            )
        )
    artifacts = SqlAlchemyRenderRepository(sessions)
    artifacts.enqueue(
        CreateRenderRequest(
            request_id="request_review_app",
            project_id=document.project_id,
            revision_id=document.revision_id,
            idempotency_key="render-review-app",
        ),
        artifact_id="artifact_review_app",
        job_id="job_render_review_app",
        request_hash="a" * 64,
        renderer="fake",
        renderer_version="1",
    )
    artifacts.mark_rendering("job_render_review_app")
    storage = LocalStorage(tmp_path / "storage")
    stored = storage.put_bytes(
        b"preview",
        "project_review_app/render/artifact_review_app.mp4",
    )
    artifacts.mark_ready(
        "job_render_review_app",
        storage_key=stored.key,
        content_type="video/mp4",
        duration_seconds=2,
        file_size=stored.size,
        sha256=stored.sha256,
        render_time_ms=1,
    )
    harness = VideoAgentHarness(
        provider=FixtureFirstCutDirectorProvider(),
        render_review_engine=RenderReviewEngine(
            inspector=FakeInspector(),
            provider=FixtureRenderReviewerProvider(),
        ),
    )
    application = RenderReviewApplication(
        revisions=SqlAlchemyRevisionRepository(sessions),
        artifacts=artifacts,
        storage=storage,
        harness=harness,
        id_factory=lambda _: "job_review_app",
        clock=lambda: NOW,
    )
    ticket = application.enqueue(document.project_id, "artifact_review_app")

    assert asyncio.run(JobRunner(sessions).run(ticket.artifact.review_job_id, application.process))

    reviewed = application.get(document.project_id, "artifact_review_app")
    assert reviewed.review_status == "accepted"
    assert reviewed.review_context is not None
    assert reviewed.review is not None
    assert reviewed.review.visual_review_performed is True
