"""Pure commit lifecycle for Patch, restore, and initial project writes."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from ..contracts.base import ContractModel, StableId


CommitRequestKind = Literal["initialize", "patch", "restore"]
CommitState = Literal[
    "received",
    "candidate_created",
    "validated",
    "awaiting_review",
    "committed",
    "rejected",
    "conflict",
    "duplicate",
    "abandoned",
    "failed",
]
CommitIssueKind = Literal["rejection", "conflict", "abandonment", "failure"]


class InvalidCommitTransition(ValueError):
    """Raised before any state is changed when a lifecycle edge is invalid."""


class CommitIssue(ContractModel):
    kind: CommitIssueKind
    code: StableId
    message: str = Field(min_length=1, max_length=1_000)
    retryable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


class CommitAttempt(ContractModel):
    """Immutable snapshot of one idempotent commit attempt's current state."""

    commit_id: StableId
    project_id: StableId
    request_kind: CommitRequestKind
    request_id: StableId
    base_revision_id: StableId | None
    idempotency_key: StableId
    request_hash: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    state: CommitState = "received"
    candidate_revision_id: StableId | None = None
    result_revision_id: StableId | None = None
    issue: CommitIssue | None = None

    @model_validator(mode="after")
    def fields_match_state(self) -> CommitAttempt:
        if self.request_kind == "initialize":
            if self.base_revision_id is not None:
                raise ValueError("initialize commit cannot have base_revision_id")
        elif self.base_revision_id is None:
            raise ValueError(f"{self.request_kind} commit requires base_revision_id")

        if self.state == "received":
            self._require_empty_outcome()
        elif self.state in {"candidate_created", "validated", "awaiting_review"}:
            if self.candidate_revision_id is None:
                raise ValueError(f"{self.state} requires candidate_revision_id")
            if self.result_revision_id is not None or self.issue is not None:
                raise ValueError(f"{self.state} cannot have a terminal outcome")
        elif self.state == "committed":
            if self.candidate_revision_id is None or self.result_revision_id is None:
                raise ValueError("committed state requires candidate and result revisions")
            if self.candidate_revision_id != self.result_revision_id:
                raise ValueError("committed result must be the validated candidate")
            if self.issue is not None:
                raise ValueError("committed state cannot have an issue")
        elif self.state == "duplicate":
            if self.result_revision_id is None:
                raise ValueError("duplicate state requires the existing result revision")
            if self.candidate_revision_id is not None or self.issue is not None:
                raise ValueError("duplicate state cannot create a candidate or issue")
        else:
            if self.result_revision_id is not None:
                raise ValueError(f"{self.state} state cannot have a result revision")
            if self.issue is None:
                raise ValueError(f"{self.state} state requires an issue")
            expected_issue_kind = {
                "rejected": "rejection",
                "conflict": "conflict",
                "abandoned": "abandonment",
                "failed": "failure",
            }[self.state]
            if self.issue.kind != expected_issue_kind:
                raise ValueError(
                    f"{self.state} state requires a {expected_issue_kind} issue"
                )
        return self

    def _require_empty_outcome(self) -> None:
        if (
            self.candidate_revision_id is not None
            or self.result_revision_id is not None
            or self.issue is not None
        ):
            raise ValueError("received state cannot have an outcome")


TERMINAL_COMMIT_STATES = frozenset(
    {"committed", "rejected", "conflict", "duplicate", "abandoned", "failed"}
)

ALLOWED_COMMIT_TRANSITIONS: dict[str, frozenset[str]] = {
    "received": frozenset(
        {"candidate_created", "rejected", "conflict", "duplicate", "failed"}
    ),
    "candidate_created": frozenset({"validated", "rejected", "failed"}),
    "validated": frozenset(
        {"awaiting_review", "committed", "conflict", "failed"}
    ),
    "awaiting_review": frozenset(
        {"committed", "conflict", "abandoned", "failed"}
    ),
    "committed": frozenset(),
    "rejected": frozenset(),
    "conflict": frozenset(),
    "duplicate": frozenset(),
    "abandoned": frozenset(),
    "failed": frozenset(),
}


def begin_commit(
    *,
    commit_id: StableId,
    project_id: StableId,
    request_kind: CommitRequestKind,
    request_id: StableId,
    base_revision_id: StableId | None,
    idempotency_key: StableId,
    request_hash: str,
) -> CommitAttempt:
    """Create the immutable received state for one logical request."""

    return CommitAttempt(
        commit_id=commit_id,
        project_id=project_id,
        request_kind=request_kind,
        request_id=request_id,
        base_revision_id=base_revision_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )


def transition_commit(
    attempt: CommitAttempt,
    next_state: CommitState,
    *,
    candidate_revision_id: StableId | None = None,
    result_revision_id: StableId | None = None,
    issue: CommitIssue | None = None,
) -> CommitAttempt:
    """Return the next validated state without mutating the prior snapshot."""

    if attempt.state in TERMINAL_COMMIT_STATES:
        raise InvalidCommitTransition(
            f"commit state {attempt.state!r} is terminal and cannot transition"
        )
    if next_state not in ALLOWED_COMMIT_TRANSITIONS[attempt.state]:
        raise InvalidCommitTransition(
            f"invalid commit transition {attempt.state} -> {next_state}"
        )

    payload = attempt.model_dump(mode="python")
    payload["state"] = next_state
    if candidate_revision_id is not None:
        payload["candidate_revision_id"] = candidate_revision_id
    if result_revision_id is not None:
        payload["result_revision_id"] = result_revision_id
    if issue is not None:
        payload["issue"] = issue
    return CommitAttempt.model_validate(payload)
