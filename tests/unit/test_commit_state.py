from __future__ import annotations

import pytest
from pydantic import ValidationError

from aive.project.commit_state import (
    CommitAttempt,
    CommitIssue,
    InvalidCommitTransition,
    begin_commit,
    transition_commit,
)


def _begin(**overrides: object) -> CommitAttempt:
    values: dict[str, object] = {
        "commit_id": "commit_01",
        "project_id": "project_01",
        "request_kind": "patch",
        "request_id": "patch_01",
        "base_revision_id": "revision_01",
        "idempotency_key": "edit-project-01-turn-01",
        "request_hash": "a" * 64,
    }
    values.update(overrides)
    return begin_commit(**values)


def _issue(kind: str, code: str) -> CommitIssue:
    return CommitIssue(
        kind=kind,
        code=code,
        message=code.replace("_", " "),
        retryable=kind in {"conflict", "failure"},
    )


def test_patch_commit_follows_the_only_success_path() -> None:
    received = _begin()
    candidate = transition_commit(
        received,
        "candidate_created",
        candidate_revision_id="revision_02",
    )
    validated = transition_commit(candidate, "validated")
    committed = transition_commit(
        validated,
        "committed",
        result_revision_id="revision_02",
    )

    assert received.state == "received"
    assert candidate.state == "candidate_created"
    assert validated.state == "validated"
    assert committed.state == "committed"
    assert committed.candidate_revision_id == "revision_02"
    assert committed.result_revision_id == "revision_02"


def test_agent_candidate_can_wait_for_review_then_commit() -> None:
    candidate = transition_commit(
        _begin(),
        "candidate_created",
        candidate_revision_id="revision_02",
    )
    validated = transition_commit(candidate, "validated")
    reviewing = transition_commit(validated, "awaiting_review")
    committed = transition_commit(
        reviewing,
        "committed",
        result_revision_id="revision_02",
    )

    assert reviewing.state == "awaiting_review"
    assert committed.state == "committed"


def test_reviewed_candidate_can_be_abandoned_without_moving_project_head() -> None:
    candidate = transition_commit(
        _begin(),
        "candidate_created",
        candidate_revision_id="revision_02",
    )
    reviewing = transition_commit(
        transition_commit(candidate, "validated"),
        "awaiting_review",
    )

    abandoned = transition_commit(
        reviewing,
        "abandoned",
        issue=_issue("abandonment", "user_declined"),
    )

    assert abandoned.result_revision_id is None
    assert abandoned.issue.code == "user_declined"


def test_stale_base_can_conflict_before_build_or_at_atomic_commit() -> None:
    early = transition_commit(
        _begin(),
        "conflict",
        issue=_issue("conflict", "stale_base_revision"),
    )
    assert early.candidate_revision_id is None

    candidate = transition_commit(
        _begin(commit_id="commit_02"),
        "candidate_created",
        candidate_revision_id="revision_02",
    )
    validated = transition_commit(candidate, "validated")
    late = transition_commit(
        validated,
        "conflict",
        issue=_issue("conflict", "project_head_changed"),
    )

    assert late.candidate_revision_id == "revision_02"
    assert late.result_revision_id is None


def test_idempotent_replay_returns_the_existing_revision() -> None:
    duplicate = transition_commit(
        _begin(),
        "duplicate",
        result_revision_id="revision_existing",
    )

    assert duplicate.state == "duplicate"
    assert duplicate.result_revision_id == "revision_existing"
    assert duplicate.candidate_revision_id is None


def test_commit_cannot_skip_candidate_creation_or_validation() -> None:
    with pytest.raises(InvalidCommitTransition, match="received -> committed"):
        transition_commit(
            _begin(),
            "committed",
            result_revision_id="revision_02",
        )


def test_terminal_commit_state_cannot_transition_again() -> None:
    conflict = transition_commit(
        _begin(),
        "conflict",
        issue=_issue("conflict", "stale_base_revision"),
    )

    with pytest.raises(InvalidCommitTransition, match="terminal"):
        transition_commit(
            conflict,
            "candidate_created",
            candidate_revision_id="revision_02",
        )


def test_issue_kind_must_match_terminal_state() -> None:
    with pytest.raises(ValidationError, match="conflict state requires a conflict issue"):
        transition_commit(
            _begin(),
            "conflict",
            issue=_issue("rejection", "lock_violation"),
        )


def test_non_initial_commits_require_a_base_revision() -> None:
    with pytest.raises(ValidationError, match="requires base_revision_id"):
        _begin(base_revision_id=None)

    initial = _begin(
        request_kind="initialize",
        request_id="initialize_project_01",
        base_revision_id=None,
    )
    assert initial.base_revision_id is None
