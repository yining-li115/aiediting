from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aive.contracts.director import (
    AgentAssetContext,
    AgentRunContext,
    CreateFirstCutRequest,
    DirectorReview,
    DirectorReviewIssue,
    DirectorRun,
    RenderAutoFixContext,
)
from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.project_doc import OutputSettings, ProjectIntent, Rational
from aive.contracts.rendering import RenderReviewIssue, RenderReviewReport
from tests.v2_fixtures import first_cut_decision, source_understanding


NOW = datetime(2026, 9, 26, 23, 30, tzinfo=timezone.utc)


def test_first_cut_request_names_an_exact_base_revision_and_is_closed() -> None:
    request = CreateFirstCutRequest(
        request_id="request_first_cut",
        project_id="project_demo",
        base_revision_id="revision_assets",
        idempotency_key="first-cut-revision-assets",
    )

    assert request.request_version == "1.0"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CreateFirstCutRequest.model_validate(
            {**request.model_dump(), "model": "vendor-model"}
        )


def test_first_cut_decision_contains_a_complete_explicit_edit_blueprint() -> None:
    decision = first_cut_decision(
        moment_id="moment_1",
        source=MediaSourceSpan(start=0, end=3_000),
        duration_frames=90,
    )

    assert decision.moment_ids == ("moment_1",)
    assert decision.edit_blueprint.output_duration_frames == 90


def test_agent_run_context_is_closed_and_project_scoped() -> None:
    context = AgentRunContext(
        project_id="project_demo",
        base_revision_id="revision_assets",
        document_hash="a" * 64,
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=90,
            prompt="Make a warm travel short",
        ),
        output=OutputSettings(
            width=1080,
            height=1920,
            fps=Rational(num=30, den=1),
        ),
        assets=(
            AgentAssetContext(
                asset_id="asset_1",
                kind="video",
                roles=("source",),
                duration_seconds=3,
                width=1080,
                height=1920,
                fps=30,
                has_audio=True,
                analysis_readiness="ready",
            ),
        ),
        moments=(
            MomentEvidence(
                moment_id="moment_1",
                project_id="project_demo",
                asset_id="asset_1",
                ordinal=0,
                shot_ids=("shot_1",),
                source=MediaSourceSpan(start=0, end=3_000),
                analysis_window_count=1,
                keyframe_count=1,
                understanding=source_understanding(
                    shot_ids=("shot_1",),
                    source=MediaSourceSpan(start=0, end=3_000),
                ),
            ),
        ),
    )

    assert context.intent.prompt == "Make a warm travel short"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AgentRunContext.model_validate({**context.model_dump(), "chat_history": []})


def test_first_cut_decision_keeps_model_provenance() -> None:
    decision = first_cut_decision(
        moment_id="moment_1",
        source=MediaSourceSpan(start=0, end=3_000),
        duration_frames=90,
    )

    assert decision.edit_blueprint.provenance.provider == "test-fixture"


def test_review_cannot_accept_error_issues() -> None:
    with pytest.raises(ValidationError, match="acceptance must match"):
        DirectorReview(
            accepted=True,
            summary="Invalid selection",
            issues=(
                DirectorReviewIssue(
                    code="unknown_moment",
                    severity="error",
                    message="The selected Moment does not exist.",
                    repairable=True,
                ),
            ),
        )


def test_committed_run_requires_patch_revision_and_render_identity() -> None:
    with pytest.raises(ValidationError, match="requires revision and render"):
        DirectorRun(
            run_id="run_1",
            job_id="job_1",
            project_id="project_demo",
            base_revision_id="revision_assets",
            request_id="request_first_cut",
            idempotency_key="first-cut-revision-assets",
            status="committed",
            provider="fixture-director",
            provider_version="1",
            created_at=NOW,
            updated_at=NOW,
        )


def test_render_autofix_context_requires_repairable_rejected_review() -> None:
    decision = first_cut_decision(
        moment_id="moment_1",
        source=MediaSourceSpan(start=0, end=3_000),
        duration_frames=90,
    )
    review = RenderReviewReport(
        review_id="review_rejected",
        project_id="project_demo",
        revision_id="revision_candidate",
        artifact_id="artifact_candidate",
        accepted=False,
        visual_review_performed=True,
        issues=(
            RenderReviewIssue(
                code="weak_opening",
                severity="error",
                source="model",
                message="The opening does not establish the subject.",
                repairable=True,
            ),
        ),
        summary="Replace the opening.",
        provider="reviewer",
        provider_version="1",
        reviewed_at=NOW,
    )

    context = RenderAutoFixContext(
        project_id="project_demo",
        base_revision_id="revision_assets",
        rejected_revision_id="revision_candidate",
        rejected_artifact_id="artifact_candidate",
        attempt=1,
        decision=decision,
        render_review=review,
    )

    assert context.attempt == 1
    with pytest.raises(ValidationError, match="only repairable"):
        RenderAutoFixContext(
            project_id="project_demo",
            base_revision_id="revision_assets",
            rejected_revision_id="revision_candidate",
            rejected_artifact_id="artifact_candidate",
            attempt=1,
            decision=decision,
            render_review=review.model_copy(
                update={
                    "issues": (
                        review.issues[0].model_copy(update={"repairable": False}),
                    )
                }
            ),
        )
