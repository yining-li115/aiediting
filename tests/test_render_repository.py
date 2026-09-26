from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.contracts.project_doc import ProjectDoc
from aive.contracts.rendering import (
    CreateRenderRequest,
    RenderInspection,
    RenderReviewContext,
    RenderReviewReport,
)
from aive.db.models import Base, Project, ProjectRevisionRow
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.project.hashing import canonical_model_hash
from aive.rendering.repository import RenderIdempotencyConflict


NOW = datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def repository(tmp_path) -> SqlAlchemyRenderRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'renders.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    source = Path(__file__).parents[1] / "examples" / "project_doc_v1.json"
    first = ProjectDoc.model_validate_json(source.read_text(encoding="utf-8"))
    second = first.model_copy(
        update={
            "revision_id": "revision_002",
            "parent_revision_id": first.revision_id,
        }
    )
    with sessions.begin() as session:
        session.add(Project(id=first.project_id, status="created"))
        session.add_all(
            [
                ProjectRevisionRow(
                    id=document.revision_id,
                    project_id=document.project_id,
                    parent_revision_id=document.parent_revision_id,
                    commit_id=f"commit_{index}",
                    document_json=document.model_dump(mode="json"),
                    document_hash=canonical_model_hash(document),
                    provenance_json={"kind": "initial" if index == 1 else "patch"},
                    created_at=NOW,
                )
                for index, document in enumerate((first, second), start=1)
            ]
        )
    return SqlAlchemyRenderRepository(sessions)


def _request(**changes) -> CreateRenderRequest:
    values = {
        "request_id": "request_render",
        "project_id": "project_demo",
        "revision_id": "revision_001",
        "idempotency_key": "render-key",
        "kind": "preview",
        "quality": "proxy",
    }
    values.update(changes)
    return CreateRenderRequest(**values)


def test_enqueue_is_atomic_and_idempotent(repository) -> None:
    request = _request()

    first = repository.enqueue(
        request,
        artifact_id="artifact_001",
        job_id="job_001",
        request_hash="a" * 64,
        renderer="ffmpeg-project-doc",
        renderer_version="1.0.0",
    )
    replay = repository.enqueue(
        request,
        artifact_id="artifact_unused",
        job_id="job_unused",
        request_hash="a" * 64,
        renderer="ffmpeg-project-doc",
        renderer_version="1.0.0",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.artifact.artifact_id == "artifact_001"
    assert repository.get_job_artifact("job_001").artifact_id == "artifact_001"


def test_idempotency_key_cannot_change_the_revision(repository) -> None:
    repository.enqueue(
        _request(),
        artifact_id="artifact_001",
        job_id="job_001",
        request_hash="a" * 64,
        renderer="ffmpeg-project-doc",
        renderer_version="1.0.0",
    )

    with pytest.raises(RenderIdempotencyConflict):
        repository.enqueue(
            _request(revision_id="revision_002"),
            artifact_id="artifact_002",
            job_id="job_002",
            request_hash="b" * 64,
            renderer="ffmpeg-project-doc",
            renderer_version="1.0.0",
        )


def test_ready_artifacts_are_looked_up_by_exact_revision(repository) -> None:
    request = _request()
    repository.enqueue(
        request,
        artifact_id="artifact_old",
        job_id="job_old",
        request_hash="a" * 64,
        renderer="ffmpeg-project-doc",
        renderer_version="1.0.0",
    )
    repository.mark_rendering("job_old")
    repository.mark_ready(
        "job_old",
        storage_key="project_demo/render/artifact_old.mp4",
        content_type="video/mp4",
        duration_seconds=4.0,
        file_size=100,
        sha256="c" * 64,
        render_time_ms=50,
    )

    assert repository.latest_ready(
        "project_demo", "revision_001", "preview"
    ).artifact_id == "artifact_old"
    assert (
        repository.latest_ready("project_demo", "revision_002", "preview")
        is None
    )


def test_ready_artifact_has_one_durable_review_lifecycle(repository) -> None:
    repository.enqueue(
        _request(),
        artifact_id="artifact_review",
        job_id="job_render_review",
        request_hash="d" * 64,
        renderer="ffmpeg-project-doc",
        renderer_version="1.0.0",
    )
    repository.mark_rendering("job_render_review")
    repository.mark_ready(
        "job_render_review",
        storage_key="project_demo/render/artifact_review.mp4",
        content_type="video/mp4",
        duration_seconds=4,
        file_size=100,
        sha256="e" * 64,
        render_time_ms=50,
    )
    first = repository.enqueue_review(
        "project_demo",
        "artifact_review",
        job_id="job_review",
        provider="offline-render-reviewer",
        provider_version="1",
    )
    replay = repository.enqueue_review(
        "project_demo",
        "artifact_review",
        job_id="job_unused",
        provider="offline-render-reviewer",
        provider_version="1",
    )
    artifact = repository.mark_reviewing("job_review")
    context = RenderReviewContext(
        project_id="project_demo",
        revision_id="revision_001",
        document_hash="a" * 64,
        artifact_id="artifact_review",
        kind="preview",
        quality="proxy",
        intent=ProjectDoc.model_validate_json(
            (Path(__file__).parents[1] / "examples" / "project_doc_v1.json").read_text(
                encoding="utf-8"
            )
        ).intent,
        output=ProjectDoc.model_validate_json(
            (Path(__file__).parents[1] / "examples" / "project_doc_v1.json").read_text(
                encoding="utf-8"
            )
        ).timeline.output,
        expected_duration_seconds=4,
        expects_audio=False,
        inspection=RenderInspection(
            duration_seconds=4,
            width=1080,
            height=1920,
            fps=30,
            has_video=True,
            has_audio=False,
            sampled_frame_count=5,
            black_frame_ratio=0,
            flat_frame_ratio=0,
            mean_sharpness=0.8,
        ),
    )
    report = RenderReviewReport(
        review_id="review_001",
        project_id="project_demo",
        revision_id="revision_001",
        artifact_id="artifact_review",
        accepted=True,
        visual_review_performed=False,
        summary="Technical checks passed.",
        provider="offline-render-reviewer",
        provider_version="1",
        reviewed_at=NOW,
    )

    repository.save_review_context("job_review", context)
    reviewed = repository.mark_reviewed("job_review", report)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.artifact.review_job_id == "job_review"
    assert artifact.review_status == "reviewing"
    assert reviewed.review_status == "accepted"
    assert reviewed.review == report
    assert repository.get_review_job_artifact("job_review").review == report
