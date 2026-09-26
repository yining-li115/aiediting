from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import JsonValue, ValidationError

from ..contracts.base import StableId
from ..contracts.patch import Patch
from ..contracts.project import CreateProjectRequest
from ..contracts.project_doc import DependencySet, ProjectDoc
from ..contracts.revision import (
    ProjectRevision,
    RestoreRevisionRequest,
    RevisionProvenance,
)
from ..contracts.timeline import CaptionTrack, GraphicTrack, Timeline, VideoTrack
from .commit_state import CommitAttempt, CommitIssue, begin_commit, transition_commit
from .errors import PatchApplicationError
from .hashing import canonical_model_hash
from .patching import apply_operation
from .validation import analyze_operation, validate_locks, validate_scope


CapabilityKind = Literal["motion", "transition", "effect", "component"]


@dataclass(frozen=True)
class CapabilityUse:
    """One versioned executable definition referenced by a candidate document."""

    kind: CapabilityKind
    name: str
    version: str
    owner_id: str
    parameters: Mapping[str, JsonValue]


class CapabilityValidator(Protocol):
    """Port implemented by the installed motion/effect/component registry."""

    def rejection_reason(self, capability: CapabilityUse) -> str | None: ...


@dataclass(frozen=True)
class CandidateResult:
    revision: ProjectRevision
    attempt: CommitAttempt


class ProjectServiceError(ValueError):
    """Expected candidate rejection together with its terminal commit state."""

    def __init__(self, attempt: CommitAttempt) -> None:
        issue = attempt.issue
        super().__init__(issue.message if issue is not None else attempt.state)
        self.attempt = attempt


class ProjectService:
    """Build and validate immutable ProjectDoc candidates from typed Patches.

    Persistence and the compare-and-swap that advances a project's current
    revision deliberately live outside this pure application service.
    """

    def __init__(self, capabilities: CapabilityValidator) -> None:
        self._capabilities = capabilities

    def create_initial_candidate(
        self,
        *,
        request: CreateProjectRequest,
        dependencies: DependencySet,
        commit_id: StableId,
        revision_id: StableId,
        created_at: datetime,
    ) -> CandidateResult:
        """Build the first empty, valid ProjectDoc for a new Project."""

        attempt = begin_commit(
            commit_id=commit_id,
            project_id=request.project_id,
            request_kind="initialize",
            request_id=request.request_id,
            base_revision_id=None,
            idempotency_key=request.idempotency_key,
            request_hash=canonical_model_hash(request),
        )
        document = ProjectDoc(
            project_id=request.project_id,
            revision_id=revision_id,
            intent=request.intent,
            timeline=Timeline(
                duration_frames=request.intent.target_duration_frames,
                output=request.output,
            ),
            dependencies=dependencies,
        )
        attempt = transition_commit(
            attempt,
            "candidate_created",
            candidate_revision_id=revision_id,
        )
        candidate = self._validate_document(attempt, document)
        self._validate_capabilities(attempt, candidate)
        revision = ProjectRevision(
            commit_id=commit_id,
            created_at=created_at,
            document_hash=canonical_model_hash(candidate),
            provenance=RevisionProvenance(
                kind="initial",
                origin=request.origin,
                actor_id=request.actor_id,
                request_id=request.request_id,
                summary=request.summary,
            ),
            document=candidate,
        )
        return CandidateResult(
            revision=revision,
            attempt=transition_commit(attempt, "validated"),
        )

    def create_patch_candidate(
        self,
        *,
        base_revision: ProjectRevision,
        patch: Patch,
        commit_id: StableId,
        revision_id: StableId,
        created_at: datetime,
        actor_id: StableId | None = None,
        require_review: bool = False,
    ) -> CandidateResult:
        request_hash = canonical_model_hash(patch)
        attempt = begin_commit(
            commit_id=commit_id,
            project_id=patch.project_id,
            request_kind="patch",
            request_id=patch.request_id or patch.patch_id,
            base_revision_id=patch.base_revision_id,
            idempotency_key=patch.idempotency_key,
            request_hash=request_hash,
        )
        document = base_revision.document

        if patch.project_id != document.project_id:
            self._raise_terminal(
                attempt,
                "rejected",
                "project_mismatch",
                "Patch project_id does not match the base revision",
                details={
                    "patch_project_id": patch.project_id,
                    "base_project_id": document.project_id,
                },
            )
        if patch.base_revision_id != document.revision_id:
            self._raise_terminal(
                attempt,
                "conflict",
                "stale_base_revision",
                "Patch base_revision_id is not the supplied base revision",
                retryable=True,
                details={
                    "patch_base_revision_id": patch.base_revision_id,
                    "actual_base_revision_id": document.revision_id,
                },
            )
        if revision_id == document.revision_id:
            self._raise_terminal(
                attempt,
                "rejected",
                "revision_id_reused",
                "candidate revision_id must differ from its base revision",
            )

        working = document.model_copy(
            update={
                "revision_id": revision_id,
                "parent_revision_id": document.revision_id,
            }
        )
        try:
            for operation in patch.operations:
                try:
                    impact = analyze_operation(
                        working,
                        operation,
                        origin=patch.origin,
                    )
                    validate_scope(patch.scope, impact)
                    validate_locks(working, impact)
                    working = apply_operation(working, operation)
                except PatchApplicationError as exc:
                    raise exc.for_operation(operation.operation_id) from exc
        except PatchApplicationError as exc:
            details = dict(exc.details)
            if exc.operation_id is not None:
                details["operation_id"] = exc.operation_id
            self._raise_terminal(
                attempt,
                "rejected",
                exc.code,
                exc.message,
                details=details,
            )

        attempt = transition_commit(
            attempt,
            "candidate_created",
            candidate_revision_id=revision_id,
        )
        candidate = self._validate_document(attempt, working)
        self._validate_capabilities(attempt, candidate)

        revision = ProjectRevision(
            commit_id=commit_id,
            created_at=created_at,
            document_hash=canonical_model_hash(candidate),
            provenance=RevisionProvenance(
                kind="patch",
                origin=patch.origin,
                actor_id=actor_id,
                request_id=patch.request_id,
                patch_id=patch.patch_id,
                summary=patch.summary,
            ),
            document=candidate,
        )
        attempt = transition_commit(attempt, "validated")
        if require_review:
            attempt = transition_commit(attempt, "awaiting_review")
        return CandidateResult(revision=revision, attempt=attempt)

    def create_restore_candidate(
        self,
        *,
        base_revision: ProjectRevision,
        source_revision: ProjectRevision,
        request: RestoreRevisionRequest,
        commit_id: StableId,
        revision_id: StableId,
        created_at: datetime,
        require_review: bool = False,
    ) -> CandidateResult:
        """Copy historical content into a new child; never move history backward."""

        attempt = begin_commit(
            commit_id=commit_id,
            project_id=request.project_id,
            request_kind="restore",
            request_id=request.request_id,
            base_revision_id=request.base_revision_id,
            idempotency_key=request.idempotency_key,
            request_hash=canonical_model_hash(request),
        )
        base = base_revision.document
        source = source_revision.document
        if request.project_id != base.project_id or source.project_id != base.project_id:
            self._raise_terminal(
                attempt,
                "rejected",
                "project_mismatch",
                "restore revisions must belong to the requested project",
            )
        if request.base_revision_id != base.revision_id:
            self._raise_terminal(
                attempt,
                "conflict",
                "stale_base_revision",
                "restore base_revision_id is not the supplied base revision",
                retryable=True,
                details={
                    "request_base_revision_id": request.base_revision_id,
                    "actual_base_revision_id": base.revision_id,
                },
            )
        if request.source_revision_id != source.revision_id:
            self._raise_terminal(
                attempt,
                "rejected",
                "restore_source_mismatch",
                "restore source_revision_id is not the supplied source revision",
            )
        if revision_id in {base.revision_id, source.revision_id}:
            self._raise_terminal(
                attempt,
                "rejected",
                "revision_id_reused",
                "restore candidate requires a new revision_id",
            )

        working = source.model_copy(
            update={
                "revision_id": revision_id,
                "parent_revision_id": base.revision_id,
            }
        )
        attempt = transition_commit(
            attempt,
            "candidate_created",
            candidate_revision_id=revision_id,
        )
        candidate = self._validate_document(attempt, working)
        self._validate_capabilities(attempt, candidate)
        revision = ProjectRevision(
            commit_id=commit_id,
            created_at=created_at,
            document_hash=canonical_model_hash(candidate),
            provenance=RevisionProvenance(
                kind="restore",
                origin=request.origin,
                actor_id=request.actor_id,
                request_id=request.request_id,
                restored_from_revision_id=request.source_revision_id,
                summary=request.summary,
            ),
            document=candidate,
        )
        attempt = transition_commit(attempt, "validated")
        if require_review:
            attempt = transition_commit(attempt, "awaiting_review")
        return CandidateResult(revision=revision, attempt=attempt)

    def _validate_document(
        self,
        attempt: CommitAttempt,
        working: ProjectDoc,
    ) -> ProjectDoc:
        try:
            return ProjectDoc.model_validate(working.model_dump(mode="python"))
        except ValidationError as exc:
            messages = [
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors()
            ]
            self._raise_terminal(
                attempt,
                "rejected",
                "candidate_invalid",
                "request produced an invalid ProjectDoc candidate",
                details={"errors": messages},
            )

    def _validate_capabilities(
        self,
        attempt: CommitAttempt,
        candidate: ProjectDoc,
    ) -> None:
        for capability in iter_capability_uses(candidate):
            reason = self._capabilities.rejection_reason(capability)
            if reason is not None:
                self._raise_terminal(
                    attempt,
                    "rejected",
                    "unsupported_capability",
                    reason,
                    details={
                        "kind": capability.kind,
                        "name": capability.name,
                        "version": capability.version,
                        "owner_id": capability.owner_id,
                    },
                )

    @staticmethod
    def _raise_terminal(
        attempt: CommitAttempt,
        state: Literal["rejected", "conflict"],
        code: StableId,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        issue = CommitIssue(
            kind="conflict" if state == "conflict" else "rejection",
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
        raise ProjectServiceError(transition_commit(attempt, state, issue=issue))


def iter_capability_uses(document: ProjectDoc) -> Iterator[CapabilityUse]:
    """Yield every executable definition that must pass capability checks."""
    for track in document.timeline.tracks:
        if isinstance(track, VideoTrack):
            for clip in track.clips:
                if clip.motion is not None:
                    yield CapabilityUse(
                        kind="motion",
                        name=clip.motion.definition_id,
                        version=clip.motion.definition_version,
                        owner_id=clip.clip_id,
                        parameters=clip.motion.parameters,
                    )
                if clip.transition_out is not None:
                    yield CapabilityUse(
                        kind="transition",
                        name=clip.transition_out.definition_id,
                        version=clip.transition_out.definition_version,
                        owner_id=clip.clip_id,
                        parameters=clip.transition_out.parameters,
                    )
                for effect in clip.effects:
                    yield CapabilityUse(
                        kind="effect",
                        name=effect.effect_type,
                        version=effect.implementation_version,
                        owner_id=clip.clip_id,
                        parameters=effect.parameters,
                    )
        elif isinstance(track, CaptionTrack):
            yield CapabilityUse(
                kind="component",
                name=track.style.component_name,
                version=track.style.component_version,
                owner_id=track.track_id,
                parameters=track.style.parameters,
            )
        elif isinstance(track, GraphicTrack):
            for graphic in track.graphics:
                yield CapabilityUse(
                    kind="component",
                    name=graphic.component_name,
                    version=graphic.component_version,
                    owner_id=graphic.graphic_id,
                    parameters=graphic.props,
                )
