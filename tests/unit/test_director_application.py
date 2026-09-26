from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.agent.application import DirectorApplication
from aive.agent.harness import VideoAgentHarness
from aive.agent.render_review import RenderReviewEngine
from aive.agent.render_review_application import RenderReviewApplication
from aive.agent.workflow import FirstCutWorkflow
from aive.contracts.director import CreateFirstCutRequest, FirstCutDecision, MusicStrategy
from aive.contracts.patch import Patch, PatchScope
from aive.contracts.patch_project import SetIntent
from aive.contracts.project_doc import (
    AssetRef,
    DependencyRef,
    DependencySet,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    Timeline,
)
from aive.contracts.rendering import (
    RenderCreativeReview,
    RenderInspection,
    RenderReviewIssue,
)
from aive.db.analysis_repository import SqlAlchemyMediaAnalysisRepository
from aive.db.director_repository import SqlAlchemyDirectorRunRepository
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.models import Asset, Base, MomentRow, Project, ProjectRevisionRow
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.db.render_repository import SqlAlchemyRenderRepository
from aive.jobs import JobRunner
from aive.project.application import ProjectRevisionApplication
from aive.project.capabilities import UnconfiguredCapabilityValidator
from aive.project.hashing import canonical_model_hash
from aive.renderer.base import RenderResult
from aive.rendering.application import RevisionRenderApplication
from aive.storage import LocalStorage
from tests.provider_fakes import FixtureFirstCutDirectorProvider


NOW = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)


class FakeRenderer:
    name = "fake-project-renderer"
    version = "1"

    def validate(self, document, output_range=None) -> None:
        assert document.timeline.tracks

    def render(self, document, quality, out_path, sources, output_range=None):
        out_path.write_bytes(b"first-cut-preview")
        return RenderResult(
            path=out_path,
            quality=quality,
            duration_sec=document.timeline.duration_frames / 30,
            render_time_ms=5,
            file_size=17,
        )


class FakeInspector:
    def inspect(self, media_path, sample_dir):
        assert media_path.read_bytes() == b"first-cut-preview"
        return (
            RenderInspection(
                duration_seconds=2,
                width=80,
                height=142,
                fps=30,
                has_video=True,
                has_audio=False,
                sampled_frame_count=5,
                black_frame_ratio=0,
                flat_frame_ratio=0,
                mean_sharpness=0.8,
            ),
            (),
        )


class RejectingInspector(FakeInspector):
    def inspect(self, media_path, sample_dir):
        inspection, samples = super().inspect(media_path, sample_dir)
        return inspection.model_copy(update={"width": 100}), samples


class FailingRenderReviewer:
    name = "failing-render-reviewer"
    version = "1"

    async def review(self, context, media_path, sample_paths):
        raise RuntimeError("reviewer unavailable")


class RejectOnceRenderReviewer:
    name = "reject-once-render-reviewer"
    version = "1"

    def __init__(self, *, always_reject: bool = False) -> None:
        self.always_reject = always_reject
        self.calls = 0

    async def review(self, context, media_path, sample_paths):
        self.calls += 1
        if self.calls == 1 or self.always_reject:
            return RenderCreativeReview(
                visual_review_performed=True,
                issues=(
                    RenderReviewIssue(
                        code="weak_opening",
                        severity="error",
                        source="model",
                        message="Use a stronger opening Moment.",
                        repairable=True,
                        start_seconds=0,
                        end_seconds=1,
                    ),
                ),
                summary="The opening needs a stronger hook.",
            )
        return RenderCreativeReview(
            visual_review_performed=True,
            summary="The replacement opening is coherent.",
        )


class FailingDirectorProvider:
    name = "failing-director"
    version = "1"

    async def create_first_cut(self, context):
        raise RuntimeError("model unavailable")


class RepairingDirectorProvider:
    name = "repairing-director"
    version = "1"

    async def create_first_cut(self, context):
        return FirstCutDecision(
            moment_ids=("invented_moment",),
            summary="Needs repair",
            explanation="Needs repair",
            music_strategy=MusicStrategy(
                mode="music_bed", asset_id="invented_music_asset"
            ),
        )

    async def repair_first_cut(self, context, decision, review):
        return FirstCutDecision(
            moment_ids=("moment_director",),
            summary="Reviewed first cut",
            explanation="Use the available opening.",
        )


class UnrepairableDirectorProvider(RepairingDirectorProvider):
    async def repair_first_cut(self, context, decision, review):
        return decision


class RenderAutoFixDirectorProvider(FixtureFirstCutDirectorProvider):
    name = "render-autofix-director"
    version = "1"

    def __init__(self) -> None:
        self.render_repairs = 0

    async def create_first_cut(self, context):
        return FirstCutDecision(
            moment_ids=("moment_director",),
            summary="Initial opening",
            explanation="Use the first opening.",
        )

    async def repair_rendered_first_cut(self, context, decision, review):
        self.render_repairs += 1
        return FirstCutDecision(
            moment_ids=("moment_alternative",),
            summary="Stronger opening",
            explanation="Use the alternative opening.",
        )


class FailingRenderAutoFixDirectorProvider(RenderAutoFixDirectorProvider):
    async def repair_rendered_first_cut(self, context, decision, review):
        self.render_repairs += 1
        raise RuntimeError("render AutoFix model unavailable")


def _stack(tmp_path, provider=None, inspector=None, render_reviewer=None):
    engine = create_engine(f"sqlite:///{tmp_path / 'director-app.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    storage = LocalStorage(tmp_path / "storage")
    document = ProjectDoc(
        project_id="project_director",
        revision_id="revision_assets",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=60,
            prompt="Create a concise first cut",
        ),
        assets=(AssetRef(asset_id="asset_director", kind="video"),),
        timeline=Timeline(
            duration_frames=60,
            output=OutputSettings(
                width=160,
                height=284,
                fps=Rational(num=30, den=1),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )
    proxy_key = "project_director/proxy/asset_director.mp4"
    original_key = "project_director/original/asset_director.mp4"
    storage.put_bytes(b"source", proxy_key)
    storage.put_bytes(b"source", original_key)
    with sessions.begin() as session:
        session.add(Project(id=document.project_id, status="created"))
        session.add(
            ProjectRevisionRow(
                id=document.revision_id,
                project_id=document.project_id,
                parent_revision_id=None,
                commit_id="commit_assets",
                document_json=document.model_dump(mode="json"),
                document_hash=canonical_model_hash(document),
                provenance_json={"kind": "initial", "origin": "system"},
                created_at=NOW,
            )
        )
        session.add(
            Asset(
                id="asset_director",
                project_id=document.project_id,
                storage_key=original_key,
                proxy_key=proxy_key,
                thumbnail_key="project_director/thumbnail/asset_director.jpg",
                readiness="ready",
                analysis_readiness="ready",
                kind="video",
                original_name="clip.mp4",
                sha256="a" * 64,
                size_bytes=6,
                duration=2.0,
                width=160,
                height=284,
                fps=30.0,
                has_audio=False,
            )
        )
        session.add(
            MomentRow(
                id="moment_director",
                project_id=document.project_id,
                asset_id="asset_director",
                ordinal=0,
                shot_ids_json=["shot_director"],
                start_tick=0,
                end_tick=2_000,
                timescale=1_000,
                analysis_window_count=1,
                keyframe_keys_json=[],
                is_usable=True,
                semantic_status="ready",
                semantic="A clean opening shot",
                semantic_role="opening",
                camera_motion="static",
                transcript_segment_ids_json=[],
                has_speech=False,
            )
        )
        session.add(
            MomentRow(
                id="moment_alternative",
                project_id=document.project_id,
                asset_id="asset_director",
                ordinal=1,
                shot_ids_json=["shot_alternative"],
                start_tick=0,
                end_tick=2_000,
                timescale=1_000,
                analysis_window_count=1,
                keyframe_keys_json=[],
                is_usable=True,
                semantic_status="ready",
                semantic="A stronger alternative opening",
                semantic_role="opening",
                camera_motion="dynamic",
                transcript_segment_ids_json=[],
                has_speech=False,
            )
        )
    with sessions.begin() as session:
        session.get(Project, document.project_id).current_revision_id = document.revision_id

    revisions = SqlAlchemyRevisionRepository(sessions)
    assets = SqlAlchemyMediaAssetRepository(sessions)
    commit_ids = iter(("commit_first_cut", "commit_second"))
    revision_ids = iter(("revision_first_cut", "revision_second"))
    projects = ProjectRevisionApplication(
        revisions,
        UnconfiguredCapabilityValidator(),
        asset_authorizer=assets,
        id_factory=lambda prefix: (
            next(commit_ids) if prefix == "commit" else next(revision_ids)
        ),
        clock=lambda: NOW,
    )
    artifact_ids = iter(("artifact_first_cut", "artifact_autofix"))
    render_job_ids = iter(("job_render_first_cut", "job_render_autofix"))
    rendering = RevisionRenderApplication(
        revisions=revisions,
        assets=assets,
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=storage,
        renderer=FakeRenderer(),
        id_factory=lambda prefix: (
            next(artifact_ids) if prefix == "artifact" else next(render_job_ids)
        ),
    )
    harness = VideoAgentHarness(
        provider=provider or FixtureFirstCutDirectorProvider(),
        render_review_engine=RenderReviewEngine(
            inspector=inspector or FakeInspector(),
            provider=render_reviewer,
        ),
    )
    director = DirectorApplication(
        runs=SqlAlchemyDirectorRunRepository(sessions),
        projects=projects,
        analysis=SqlAlchemyMediaAnalysisRepository(sessions),
        assets=assets,
        rendering=rendering,
        harness=harness,
        id_factory=lambda prefix: {
            "agent_run": "agent_run_first_cut",
            "job": "job_director_first_cut",
        }[prefix],
    )
    review_job_ids = iter(
        (
            "job_review_first_cut",
            "job_review_replay",
            "job_review_autofix",
        )
    )
    reviewing = RenderReviewApplication(
        revisions=revisions,
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=storage,
        harness=harness,
        id_factory=lambda _: next(review_job_ids),
        clock=lambda: NOW,
    )
    return director, rendering, reviewing, projects, JobRunner(sessions)


def _finish_review(director, rendering, reviewing, jobs, director_job_id):
    run = director.get_job_run(director_job_id)
    assert run.render_job_id is not None
    assert asyncio.run(jobs.run(run.render_job_id, rendering.process)) is True
    run = director.get_job_run(director_job_id)
    assert run.render_artifact_id is not None
    ticket = reviewing.enqueue(run.project_id, run.render_artifact_id)
    assert ticket.artifact.review_job_id is not None
    assert asyncio.run(
        jobs.run(ticket.artifact.review_job_id, reviewing.process)
    ) is True
    reviewed = reviewing.get(run.project_id, run.render_artifact_id)
    return director.complete_render_review(director_job_id, reviewed)


def test_director_commits_patch_and_queues_then_renders_preview(tmp_path) -> None:
    director, rendering, reviewing, projects, jobs = _stack(tmp_path)
    request = CreateFirstCutRequest(
        request_id="request_first_cut",
        project_id="project_director",
        base_revision_id="revision_assets",
        idempotency_key="first-cut-key",
    )

    ticket = director.enqueue(request)
    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True
    candidate = director.get_job_run(ticket.run.job_id)
    assert candidate.status == "awaiting_review"
    assert candidate.result_revision_id == "revision_first_cut"
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_assets"
    )

    _finish_review(director, rendering, reviewing, jobs, ticket.run.job_id)
    ready = director.get_job_run(ticket.run.job_id)
    assert ready.status == "ready"
    assert ready.render_review is not None and ready.render_review.accepted is True
    assert projects.get_current_revision("project_director").document.timeline.tracks
    assert rendering.current_preview("project_director").revision_id == (
        "revision_first_cut"
    )

    replay = director.enqueue(request)
    assert replay.replayed is True
    assert replay.run.run_id == ticket.run.run_id
    assert len(projects.list_revision_history("project_director")) == 1


def test_provider_failure_leaves_current_revision_untouched(tmp_path) -> None:
    director, rendering, _, projects, jobs = _stack(
        tmp_path,
        provider=FailingDirectorProvider(),
    )
    ticket = director.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )

    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True

    assert jobs.get(ticket.run.job_id)["status"] == "failed"
    assert director.get_job_run(ticket.run.job_id).status == "failed"
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_assets"
    )
    assert rendering.current_preview("project_director") is None


def test_rejected_render_review_keeps_candidate_out_of_project_head(tmp_path) -> None:
    director, rendering, reviewing, projects, jobs = _stack(
        tmp_path,
        inspector=RejectingInspector(),
    )
    ticket = director.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )

    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True
    _finish_review(director, rendering, reviewing, jobs, ticket.run.job_id)

    run = director.get_job_run(ticket.run.job_id)
    assert run.status == "review_rejected"
    assert run.render_review is not None and run.render_review.accepted is False
    assert run.render_autofix_attempts == 0
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_assets"
    )
    assert len(projects.list_revision_history("project_director")) == 0


def test_workflow_resumes_candidate_render_review_and_commit(tmp_path) -> None:
    director, rendering, reviewing, projects, jobs = _stack(tmp_path)
    ticket = director.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )
    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True
    assert director.get_job_run(ticket.run.job_id).status == "awaiting_review"

    workflow = FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )
    asyncio.run(workflow.resume_review(ticket.run.job_id))

    recovered = director.get_job_run(ticket.run.job_id)
    assert recovered.status == "ready"
    assert recovered.render_review_status == "accepted"
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_first_cut"
    )


def test_render_reviewer_failure_keeps_previous_revision_current(tmp_path) -> None:
    director, rendering, reviewing, projects, jobs = _stack(
        tmp_path,
        render_reviewer=FailingRenderReviewer(),
    )
    ticket = director.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )
    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True
    workflow = FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )

    asyncio.run(workflow.resume_review(ticket.run.job_id))

    failed = director.get_job_run(ticket.run.job_id)
    assert failed.status == "review_failed"
    assert failed.error == "reviewer unavailable"
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_assets"
    )


def test_repairable_render_review_creates_new_candidate_and_commits_it(tmp_path) -> None:
    provider = RenderAutoFixDirectorProvider()
    reviewer = RejectOnceRenderReviewer()
    director, rendering, reviewing, projects, jobs = _stack(
        tmp_path,
        provider=provider,
        render_reviewer=reviewer,
    )
    workflow = FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )
    ticket = workflow.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )

    asyncio.run(workflow.run(ticket.run.job_id))

    ready = director.get_job_run(ticket.run.job_id)
    assert ready.status == "ready"
    assert ready.render_autofix_attempts == 1
    assert ready.render_autofix_context is not None
    assert ready.render_autofix_context.rejected_revision_id == "revision_first_cut"
    assert ready.render_autofix_context.rejected_artifact_id == "artifact_first_cut"
    assert ready.result_revision_id == "revision_second"
    assert ready.render_artifact_id == "artifact_autofix"
    assert ready.decision is not None
    assert ready.decision.moment_ids == ("moment_alternative",)
    assert provider.render_repairs == 1
    assert reviewer.calls == 2
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_second"
    )
    assert rendering.get("project_director", "artifact_first_cut").review_status == (
        "rejected"
    )
    assert rendering.current_preview("project_director").artifact_id == (
        "artifact_autofix"
    )


def test_render_autofix_stops_after_one_rejected_replacement(tmp_path) -> None:
    provider = RenderAutoFixDirectorProvider()
    reviewer = RejectOnceRenderReviewer(always_reject=True)
    director, rendering, reviewing, projects, jobs = _stack(
        tmp_path,
        provider=provider,
        render_reviewer=reviewer,
    )
    workflow = FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )
    ticket = workflow.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )

    asyncio.run(workflow.run(ticket.run.job_id))

    rejected = director.get_job_run(ticket.run.job_id)
    assert rejected.status == "review_rejected"
    assert rejected.render_autofix_attempts == 1
    assert provider.render_repairs == 1
    assert reviewer.calls == 2
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_assets"
    )
    assert rendering.current_preview("project_director") is None


def test_render_autofix_provider_failure_preserves_current_revision(tmp_path) -> None:
    provider = FailingRenderAutoFixDirectorProvider()
    reviewer = RejectOnceRenderReviewer()
    director, rendering, reviewing, projects, jobs = _stack(
        tmp_path,
        provider=provider,
        render_reviewer=reviewer,
    )
    workflow = FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )
    ticket = workflow.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )

    asyncio.run(workflow.run(ticket.run.job_id))

    failed = director.get_job_run(ticket.run.job_id)
    assert failed.status == "failed"
    assert failed.render_autofix_attempts == 1
    assert failed.error == "render AutoFix model unavailable"
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_assets"
    )
    assert rendering.current_preview("project_director") is None


def test_render_autofix_cannot_overwrite_user_edit(tmp_path) -> None:
    provider = RenderAutoFixDirectorProvider()
    reviewer = RejectOnceRenderReviewer()
    director, rendering, reviewing, projects, jobs = _stack(
        tmp_path,
        provider=provider,
        render_reviewer=reviewer,
    )
    ticket = director.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )
    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True
    autofixing = _finish_review(
        director,
        rendering,
        reviewing,
        jobs,
        ticket.run.job_id,
    )
    assert autofixing.status == "autofixing"
    assert autofixing.render_autofix_job_id is not None

    base = projects.get_current_revision("project_director")
    user_write = projects.apply_patch(
        Patch(
            patch_id="patch_user_during_autofix",
            project_id="project_director",
            base_revision_id=base.document.revision_id,
            idempotency_key="user-during-autofix",
            origin="user",
            scope=PatchScope(kind="global"),
            operations=(
                SetIntent(
                    operation_id="op_user_during_autofix",
                    intent=base.document.intent.model_copy(
                        update={"prompt": "The user changed direction"}
                    ),
                ),
            ),
        )
    )
    assert asyncio.run(
        jobs.run(
            autofixing.render_autofix_job_id,
            director.process_render_autofix,
        )
    ) is True

    failed = director.get_job_run(ticket.run.job_id)
    assert failed.status == "failed"
    assert "project head changed" in failed.error
    assert projects.get_current_revision("project_director").document.revision_id == (
        user_write.revision.document.revision_id
    )
    assert rendering.current_preview("project_director") is None


def test_workflow_recovers_after_rejected_review_was_persisted(tmp_path) -> None:
    provider = RenderAutoFixDirectorProvider()
    reviewer = RejectOnceRenderReviewer()
    director, rendering, reviewing, projects, jobs = _stack(
        tmp_path,
        provider=provider,
        render_reviewer=reviewer,
    )
    workflow = FirstCutWorkflow(
        jobs=jobs,
        director=director,
        rendering=rendering,
        reviewing=reviewing,
    )
    ticket = workflow.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )
    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True
    run = director.get_job_run(ticket.run.job_id)
    assert run.render_job_id is not None
    assert asyncio.run(jobs.run(run.render_job_id, rendering.process)) is True
    run = director.get_job_run(ticket.run.job_id)
    assert run.render_artifact_id is not None
    review_ticket = reviewing.enqueue(run.project_id, run.render_artifact_id)
    assert review_ticket.artifact.review_job_id is not None
    assert asyncio.run(
        jobs.run(review_ticket.artifact.review_job_id, reviewing.process)
    ) is True
    assert director.get_job_run(ticket.run.job_id).status == "review_rejected"

    asyncio.run(workflow.resume_review(ticket.run.job_id))

    recovered = director.get_job_run(ticket.run.job_id)
    assert recovered.status == "ready"
    assert recovered.render_autofix_attempts == 1
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_second"
    )


def test_director_persists_context_review_and_bounded_repair(tmp_path) -> None:
    director, rendering, reviewing, projects, jobs = _stack(
        tmp_path,
        provider=RepairingDirectorProvider(),
    )
    ticket = director.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )

    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True

    _finish_review(director, rendering, reviewing, jobs, ticket.run.job_id)
    run = director.get_job_run(ticket.run.job_id)
    assert run.status == "ready"
    assert run.context is not None
    assert run.context.base_revision_id == "revision_assets"
    assert run.review is not None and run.review.accepted is True
    assert run.repair_attempts == 1
    assert run.decision is not None
    assert "Selected 1 analyzed Moment" in run.decision.explanation
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_first_cut"
    )


def test_autofix_exhaustion_preserves_head_and_failed_review(tmp_path) -> None:
    director, rendering, _, projects, jobs = _stack(
        tmp_path,
        provider=UnrepairableDirectorProvider(),
    )
    ticket = director.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )

    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True

    run = director.get_job_run(ticket.run.job_id)
    assert run.status == "failed"
    assert run.review is not None and run.review.accepted is False
    assert run.repair_attempts == 1
    assert projects.get_current_revision("project_director").document.revision_id == (
        "revision_assets"
    )
    assert rendering.current_preview("project_director") is None


def test_stale_director_cannot_overwrite_a_newer_user_revision(tmp_path) -> None:
    director, _, _, projects, jobs = _stack(tmp_path)
    ticket = director.enqueue(
        CreateFirstCutRequest(
            request_id="request_first_cut",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="first-cut-key",
        )
    )
    base = projects.get_current_revision("project_director")
    updated_intent = base.document.intent.model_copy(
        update={"prompt": "User changed the brief while analysis was running"}
    )
    user_write = projects.apply_patch(
        Patch(
            patch_id="patch_user_edit",
            project_id="project_director",
            base_revision_id="revision_assets",
            idempotency_key="user-edit-key",
            origin="user",
            scope=PatchScope(kind="global"),
            operations=(
                SetIntent(operation_id="op_user_intent", intent=updated_intent),
            ),
        )
    )

    assert asyncio.run(jobs.run(ticket.run.job_id, director.process)) is True

    assert director.get_job_run(ticket.run.job_id).status == "failed"
    assert projects.get_current_revision("project_director").document.revision_id == (
        user_write.revision.document.revision_id
    )
