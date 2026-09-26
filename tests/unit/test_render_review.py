from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from aive.agent.render_review import RenderReviewEngine
from aive.contracts.project_doc import (
    DependencyRef,
    DependencySet,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    Timeline,
)
from aive.contracts.rendering import (
    RenderArtifact,
    RenderCreativeReview,
    RenderInspection,
    RenderReviewIssue,
)
from aive.contracts.revision import ProjectRevision, RevisionProvenance
from aive.project.hashing import canonical_model_hash


NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


class FakeInspector:
    def __init__(self, inspection: RenderInspection) -> None:
        self.inspection = inspection

    def inspect(self, media_path: Path, sample_dir: Path):
        assert media_path.name == "preview.mp4"
        return self.inspection, ()


class RecordingReviewer:
    name = "recording-reviewer"
    version = "1"

    def __init__(self, creative: RenderCreativeReview) -> None:
        self.creative = creative
        self.calls = 0

    async def review(self, context, media_path, sample_paths):
        self.calls += 1
        return self.creative


def _revision() -> ProjectRevision:
    document = ProjectDoc(
        project_id="project_render_review",
        revision_id="revision_render_review",
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
    return ProjectRevision(
        commit_id="commit_render_review",
        created_at=NOW,
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(
            kind="patch",
            origin="agent",
            patch_id="patch_render_review",
        ),
        document=document.model_copy(
            update={"parent_revision_id": "revision_before_review"}
        ),
    )


def _artifact() -> RenderArtifact:
    return RenderArtifact(
        artifact_id="artifact_render_review",
        project_id="project_render_review",
        revision_id="revision_render_review",
        job_id="job_render_review",
        kind="preview",
        quality="proxy",
        status="ready",
        renderer="fake-renderer",
        renderer_version="1",
        content_type="video/mp4",
        duration_seconds=2,
        file_size=100,
        sha256="b" * 64,
        created_at=NOW,
        updated_at=NOW,
    )


def _inspection(**updates) -> RenderInspection:
    values = {
        "duration_seconds": 2,
        "width": 540,
        "height": 960,
        "fps": 30,
        "has_video": True,
        "has_audio": False,
        "sampled_frame_count": 5,
        "black_frame_ratio": 0,
        "flat_frame_ratio": 0,
        "mean_sharpness": 0.8,
    }
    values.update(updates)
    return RenderInspection(**values)


async def test_render_review_combines_technical_and_model_evidence(tmp_path) -> None:
    reviewer = RecordingReviewer(
        RenderCreativeReview(
            visual_review_performed=True,
            summary="The visual story is coherent.",
        )
    )
    engine = RenderReviewEngine(
        inspector=FakeInspector(_inspection()),
        provider=reviewer,
    )

    outcome = await engine.review(
        revision=_revision(),
        artifact=_artifact(),
        media_path=tmp_path / "preview.mp4",
        review_id="review_render_review",
        reviewed_at=NOW,
    )

    assert outcome.report.accepted is True
    assert outcome.report.visual_review_performed is True
    assert outcome.context.document_hash == _revision().document_hash
    assert reviewer.calls == 1


async def test_blocking_technical_issue_skips_paid_model_review(tmp_path) -> None:
    reviewer = RecordingReviewer(
        RenderCreativeReview(
            visual_review_performed=True,
            summary="This must not be called.",
        )
    )
    engine = RenderReviewEngine(
        inspector=FakeInspector(_inspection(width=720)),
        provider=reviewer,
    )

    outcome = await engine.review(
        revision=_revision(),
        artifact=_artifact(),
        media_path=tmp_path / "preview.mp4",
        review_id="review_render_review",
        reviewed_at=NOW,
    )

    assert outcome.report.accepted is False
    assert outcome.report.visual_review_performed is False
    assert {issue.code for issue in outcome.report.issues} == {
        "output_dimensions_mismatch"
    }
    assert reviewer.calls == 0


async def test_model_review_can_reject_a_technically_valid_render(tmp_path) -> None:
    reviewer = RecordingReviewer(
        RenderCreativeReview(
            visual_review_performed=True,
            issues=(
                RenderReviewIssue(
                    code="weak_hook",
                    severity="error",
                    source="model",
                    message="The opening does not establish the subject.",
                    repairable=True,
                ),
            ),
            summary="The opening needs revision.",
        )
    )
    engine = RenderReviewEngine(
        inspector=FakeInspector(_inspection()),
        provider=reviewer,
    )

    outcome = await engine.review(
        revision=_revision(),
        artifact=_artifact(),
        media_path=tmp_path / "preview.mp4",
        review_id="review_render_review",
        reviewed_at=NOW,
    )

    assert outcome.report.accepted is False
    assert outcome.report.issues[0].source == "model"
