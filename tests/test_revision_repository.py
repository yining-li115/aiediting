from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from aive.contracts.patch import Patch
from aive.contracts.project import CreateProjectRequest
from aive.contracts.project_doc import DependencyRef, DependencySet, ProjectDoc
from aive.contracts.revision import (
    ProjectRevision,
    RestoreRevisionRequest,
    RevisionProvenance,
)
from aive.db.models import Base, Project, ProjectCommitRow, ProjectRevisionRow
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.project.commit_state import begin_commit, transition_commit
from aive.project.repository import RevisionWriteResult
from aive.project.service import CandidateResult, CapabilityUse, ProjectService


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


class AcceptCapabilities:
    def rejection_reason(self, capability: CapabilityUse) -> str | None:
        return None


@pytest.fixture
def repository(tmp_path) -> SqlAlchemyRevisionRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'revisions.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with sessions.begin() as session:
        session.add(Project(id="project_demo", status="created"))
    return SqlAlchemyRevisionRepository(sessions)


@pytest.fixture
def empty_repository(tmp_path) -> SqlAlchemyRevisionRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'empty-revisions.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return SqlAlchemyRevisionRepository(sessions)


def _initial_document() -> ProjectDoc:
    path = Path(__file__).parents[1] / "examples" / "project_doc_v1.json"
    return ProjectDoc.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _initial_candidate() -> CandidateResult:
    document = _initial_document()
    revision = ProjectRevision(
        commit_id="commit_initial",
        created_at=NOW,
        document_hash=_document_hash(document),
        provenance=RevisionProvenance(
            kind="initial",
            origin="system",
            request_id="initialize_project_demo",
        ),
        document=document,
    )
    attempt = begin_commit(
        commit_id="commit_initial",
        project_id=document.project_id,
        request_kind="initialize",
        request_id="initialize_project_demo",
        base_revision_id=None,
        idempotency_key="initialize-project-demo",
        request_hash="1" * 64,
    )
    attempt = transition_commit(
        attempt,
        "candidate_created",
        candidate_revision_id=document.revision_id,
    )
    attempt = transition_commit(attempt, "validated")
    return CandidateResult(revision=revision, attempt=attempt)


def _document_hash(document: ProjectDoc) -> str:
    from aive.project.hashing import canonical_model_hash

    return canonical_model_hash(document)


def _patch(
    *,
    patch_id: str,
    idempotency_key: str,
    base_revision_id: str,
    prompt: str,
) -> Patch:
    return Patch.model_validate(
        {
            "patch_id": patch_id,
            "project_id": "project_demo",
            "base_revision_id": base_revision_id,
            "idempotency_key": idempotency_key,
            "origin": "user",
            "request_id": f"request_{patch_id}",
            "scope": {"kind": "global"},
            "operations": [
                {
                    "op": "set_intent",
                    "operation_id": f"operation_{patch_id}",
                    "intent": {
                        "task_mode": "autonomous",
                        "target_duration_frames": 120,
                        "prompt": prompt,
                    },
                }
            ],
        }
    )


def _candidate(
    base: ProjectRevision,
    *,
    patch_id: str,
    idempotency_key: str,
    revision_id: str,
    commit_id: str,
    prompt: str,
    require_review: bool = False,
) -> CandidateResult:
    patch = _patch(
        patch_id=patch_id,
        idempotency_key=idempotency_key,
        base_revision_id=base.document.revision_id,
        prompt=prompt,
    )
    return ProjectService(AcceptCapabilities()).create_patch_candidate(
        base_revision=base,
        patch=patch,
        commit_id=commit_id,
        revision_id=revision_id,
        created_at=NOW + timedelta(minutes=1),
        require_review=require_review,
    )


def _initialize(repository: SqlAlchemyRevisionRepository) -> ProjectRevision:
    result = repository.initialize_project(_initial_candidate())
    assert result.attempt.state == "committed"
    assert result.revision is not None
    return result.revision


def _create_project_candidate(
    *,
    project_id: str = "project_created",
    revision_id: str = "revision_created",
    commit_id: str = "commit_created",
) -> CandidateResult:
    request = CreateProjectRequest(
        request_id="request_created",
        project_id=project_id,
        idempotency_key="create-project-key",
        origin="user",
        intent={
            "task_mode": "autonomous",
            "target_duration_frames": 900,
            "prompt": "New project",
        },
        output={
            "width": 1080,
            "height": 1920,
            "fps": {"num": 30, "den": 1},
        },
    )
    return ProjectService(AcceptCapabilities()).create_initial_candidate(
        request=request,
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="dev")
        ),
        commit_id=commit_id,
        revision_id=revision_id,
        created_at=NOW,
    )


def test_create_project_persists_identity_revision_and_head_atomically(
    empty_repository: SqlAlchemyRevisionRepository,
) -> None:
    candidate = _create_project_candidate()

    result = empty_repository.create_project(candidate)

    assert result.attempt.state == "committed"
    assert result.revision == candidate.revision
    assert empty_repository.get_current_revision("project_created") == candidate.revision
    with empty_repository.session_factory() as session:
        project = session.get(Project, "project_created")
        assert project.mode == "auto"
        assert project.prompt == "New project"
        assert project.current_revision_id == "revision_created"


def test_create_project_retry_returns_the_original_initial_revision(
    empty_repository: SqlAlchemyRevisionRepository,
) -> None:
    first = _create_project_candidate()
    retry = _create_project_candidate(
        revision_id="revision_retry",
        commit_id="commit_retry",
    )

    empty_repository.create_project(first)
    result = empty_repository.create_project(retry)

    assert result.attempt.state == "duplicate"
    assert result.revision == first.revision
    assert empty_repository.get_revision("project_created", "revision_retry") is None


def test_failed_create_rolls_back_the_project_row(
    empty_repository: SqlAlchemyRevisionRepository,
) -> None:
    candidate = _create_project_candidate(commit_id="commit_conflict")
    other_document = _initial_document().model_copy(
        update={
            "project_id": "project_other",
            "revision_id": "revision_other",
        }
    )
    with empty_repository.session_factory.begin() as session:
        session.add(Project(id="project_other", status="created"))
        session.add(
            ProjectRevisionRow(
                id="revision_other",
                project_id="project_other",
                parent_revision_id=None,
                commit_id="commit_conflict",
                document_json=other_document.model_dump(mode="json"),
                document_hash=_document_hash(other_document),
                provenance_json={"kind": "initial", "origin": "system"},
                created_at=NOW,
            )
        )

    from aive.project.repository import RevisionIdentityConflict

    with pytest.raises(RevisionIdentityConflict):
        empty_repository.create_project(candidate)

    with empty_repository.session_factory() as session:
        assert session.get(Project, "project_created") is None


def test_initial_revision_and_head_are_persisted_atomically(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    initial = _initial_candidate()

    result = repository.initialize_project(initial)

    assert result.attempt.state == "committed"
    assert result.revision == initial.revision
    assert repository.get_current_revision("project_demo") == initial.revision
    assert repository.get_revision("project_demo", "revision_001") == initial.revision


def test_validated_candidate_is_committed_and_advances_only_the_expected_head(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    base = _initialize(repository)
    candidate = _candidate(
        base,
        patch_id="patch_002",
        idempotency_key="edit-002",
        revision_id="revision_002",
        commit_id="commit_002",
        prompt="Committed edit",
    )

    result = repository.commit_candidate(candidate)

    assert result.attempt.state == "committed"
    assert result.attempt.result_revision_id == "revision_002"
    assert result.revision == candidate.revision
    assert repository.get_current_revision("project_demo") == candidate.revision
    assert repository.list_revisions("project_demo") == [base, candidate.revision]


def test_compare_and_swap_rejects_second_candidate_from_the_same_old_head(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    base = _initialize(repository)
    winner = _candidate(
        base,
        patch_id="patch_winner",
        idempotency_key="edit-winner",
        revision_id="revision_winner",
        commit_id="commit_winner",
        prompt="Winner",
    )
    stale = _candidate(
        base,
        patch_id="patch_stale",
        idempotency_key="edit-stale",
        revision_id="revision_stale",
        commit_id="commit_stale",
        prompt="Must not overwrite winner",
    )

    repository.commit_candidate(winner)
    result = repository.commit_candidate(stale)

    assert result.attempt.state == "conflict"
    assert result.attempt.issue.code == "project_head_changed"
    assert repository.get_current_revision("project_demo") == winner.revision
    assert repository.get_revision("project_demo", "revision_stale") == stale.revision


def test_same_idempotency_key_and_request_hash_returns_original_result(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    base = _initialize(repository)
    first = _candidate(
        base,
        patch_id="patch_same",
        idempotency_key="edit-same",
        revision_id="revision_002",
        commit_id="commit_002",
        prompt="Exactly once",
    )
    replay = _candidate(
        base,
        patch_id="patch_same",
        idempotency_key="edit-same",
        revision_id="revision_replay",
        commit_id="commit_replay",
        prompt="Exactly once",
    )

    committed = repository.commit_candidate(first)
    duplicate = repository.commit_candidate(replay)

    assert committed.attempt.state == "committed"
    assert duplicate.attempt.state == "duplicate"
    assert duplicate.attempt.result_revision_id == "revision_002"
    assert duplicate.revision == first.revision
    assert repository.get_revision("project_demo", "revision_replay") is None
    assert len(repository.list_revisions("project_demo")) == 2


def test_reusing_idempotency_key_for_different_request_is_a_conflict(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    base = _initialize(repository)
    first = _candidate(
        base,
        patch_id="patch_first",
        idempotency_key="edit-shared",
        revision_id="revision_002",
        commit_id="commit_002",
        prompt="First request",
    )
    reused = _candidate(
        base,
        patch_id="patch_different",
        idempotency_key="edit-shared",
        revision_id="revision_003",
        commit_id="commit_003",
        prompt="Different request",
    )
    repository.commit_candidate(first)

    result = repository.commit_candidate(reused)

    assert result.attempt.state == "conflict"
    assert result.attempt.issue.code == "idempotency_conflict"
    assert result.revision is None
    assert repository.get_current_revision("project_demo") == first.revision


def test_review_candidate_is_durable_but_not_current_until_acceptance(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    base = _initialize(repository)
    candidate = _candidate(
        base,
        patch_id="patch_review",
        idempotency_key="edit-review",
        revision_id="revision_review",
        commit_id="commit_review",
        prompt="Review me",
        require_review=True,
    )

    stored = repository.store_candidate(candidate)

    assert stored.attempt.state == "awaiting_review"
    assert repository.get_current_revision("project_demo") == base
    assert repository.get_revision("project_demo", "revision_review") == candidate.revision

    accepted = repository.commit_saved_candidate("project_demo", "commit_review")
    assert accepted.attempt.state == "committed"
    assert repository.get_current_revision("project_demo") == candidate.revision


def test_review_acceptance_conflicts_if_another_edit_won_during_review(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    base = _initialize(repository)
    review = _candidate(
        base,
        patch_id="patch_review",
        idempotency_key="edit-review",
        revision_id="revision_review",
        commit_id="commit_review",
        prompt="Review me",
        require_review=True,
    )
    winner = _candidate(
        base,
        patch_id="patch_winner",
        idempotency_key="edit-winner",
        revision_id="revision_winner",
        commit_id="commit_winner",
        prompt="Committed while review was open",
    )
    repository.store_candidate(review)
    repository.commit_candidate(winner)

    result = repository.commit_saved_candidate("project_demo", "commit_review")

    assert result.attempt.state == "conflict"
    assert result.attempt.issue.code == "project_head_changed"
    assert repository.get_current_revision("project_demo") == winner.revision


def test_review_acceptance_is_scoped_to_the_route_project(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    base = _initialize(repository)
    candidate = _candidate(
        base,
        patch_id="patch_review_scope",
        idempotency_key="edit-review-scope",
        revision_id="revision_review_scope",
        commit_id="commit_review_scope",
        prompt="Review me",
        require_review=True,
    )
    repository.store_candidate(candidate)

    from aive.project.repository import CommitNotFound

    with pytest.raises(CommitNotFound):
        repository.commit_saved_candidate("project_other", "commit_review_scope")

    assert repository.get_current_revision("project_demo") == base


def test_uncommitted_candidates_are_not_returned_as_project_history(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    base = _initialize(repository)
    candidate = _candidate(
        base,
        patch_id="patch_review_history",
        idempotency_key="edit-review-history",
        revision_id="revision_review_history",
        commit_id="commit_review_history",
        prompt="Not history until accepted",
        require_review=True,
    )
    repository.store_candidate(candidate)

    assert repository.list_revisions("project_demo") == [base]
    assert (
        repository.get_revision("project_demo", "revision_review_history")
        == candidate.revision
    )


def test_restore_is_undo_by_creating_a_new_child_revision(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    original = _initialize(repository)
    edited = _candidate(
        original,
        patch_id="patch_edited",
        idempotency_key="edit-edited",
        revision_id="revision_002",
        commit_id="commit_002",
        prompt="Changed prompt",
    )
    repository.commit_candidate(edited)
    request = RestoreRevisionRequest(
        request_id="restore_001",
        project_id="project_demo",
        base_revision_id="revision_002",
        source_revision_id="revision_001",
        idempotency_key="restore-project-demo-001",
        origin="user",
        summary="Undo the last edit",
    )

    restore = ProjectService(AcceptCapabilities()).create_restore_candidate(
        base_revision=edited.revision,
        source_revision=original,
        request=request,
        commit_id="commit_003",
        revision_id="revision_003",
        created_at=NOW + timedelta(minutes=2),
    )
    result = repository.commit_candidate(restore)

    assert result.attempt.state == "committed"
    assert result.revision.document.revision_id == "revision_003"
    assert result.revision.document.parent_revision_id == "revision_002"
    assert result.revision.document.intent == original.document.intent
    assert result.revision.provenance.kind == "restore"
    assert result.revision.provenance.restored_from_revision_id == "revision_001"
    assert repository.get_revision("project_demo", "revision_002") == edited.revision
    assert repository.get_current_revision("project_demo") == result.revision


def test_schema_enforces_project_scoped_idempotency(
    repository: SqlAlchemyRevisionRepository,
) -> None:
    _initialize(repository)
    sessions = repository.session_factory

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProjectRevisionRow)) == 1
        assert session.scalar(select(func.count()).select_from(ProjectCommitRow)) == 1
    constraint_names = {
        constraint.name for constraint in ProjectCommitRow.__table__.constraints
    }
    assert "uq_project_commits_project_id_idempotency_key" in constraint_names


def test_repository_result_is_an_explicit_domain_value() -> None:
    assert RevisionWriteResult.__annotations__ == {
        "attempt": "CommitAttempt",
        "revision": "ProjectRevision | None",
        "replayed": "bool",
    }
