from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aive.contracts.project_doc import OutputSettings, ProjectIntent, Rational
from aive.contracts.rendering import (
    CreateRenderRequest,
    RenderInspection,
    RenderReviewContext,
    RenderReviewIssue,
    RenderReviewReport,
)


def _request() -> dict:
    return {
        "request_id": "request_render_001",
        "project_id": "project_render_001",
        "revision_id": "revision_render_001",
        "idempotency_key": "render-project-revision-proxy",
        "kind": "preview",
        "quality": "proxy",
    }


def test_render_request_names_exact_revision_and_is_closed() -> None:
    request = CreateRenderRequest.model_validate(_request())

    assert request.revision_id == "revision_render_001"
    assert request.request_version == "1.0"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CreateRenderRequest.model_validate({**_request(), "storage_path": "/tmp/x"})


@pytest.mark.parametrize(
    ("kind", "quality"),
    (("preview", "hq"), ("export", "proxy")),
)
def test_render_kind_has_one_quality_tier(kind: str, quality: str) -> None:
    with pytest.raises(ValidationError, match="quality"):
        CreateRenderRequest.model_validate(
            {**_request(), "kind": kind, "quality": quality}
        )


def _review_context() -> RenderReviewContext:
    return RenderReviewContext(
        project_id="project_render_review",
        revision_id="revision_render_review",
        document_hash="a" * 64,
        artifact_id="artifact_render_review",
        kind="preview",
        quality="proxy",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="Create a concise opening",
        ),
        output=OutputSettings(
            width=540,
            height=960,
            fps=Rational(num=30, den=1),
        ),
        expected_duration_seconds=2,
        expects_audio=False,
        inspection=RenderInspection(
            duration_seconds=2,
            width=1080,
            height=1920,
            fps=30,
            has_video=True,
            has_audio=False,
            sampled_frame_count=5,
        ),
    )


def test_render_review_is_bound_to_exact_revision_and_artifact() -> None:
    report = RenderReviewReport(
        review_id="review_render_review",
        project_id="project_render_review",
        revision_id="revision_render_review",
        artifact_id="artifact_render_review",
        accepted=True,
        visual_review_performed=False,
        summary="Technical render checks passed.",
        provider="offline-render-reviewer",
        provider_version="1",
        reviewed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
    )

    assert report.accepted is True
    assert _review_context().artifact_id == report.artifact_id


def test_render_review_acceptance_must_match_error_issues() -> None:
    with pytest.raises(ValidationError):
        RenderReviewReport(
            review_id="review_render_review",
            project_id="project_render_review",
            revision_id="revision_render_review",
            artifact_id="artifact_render_review",
            accepted=True,
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
            provider="volcano-render-reviewer",
            provider_version="1",
            reviewed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
        )
