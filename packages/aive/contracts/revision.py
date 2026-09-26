"""Immutable project revision and restore request contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ChangeOrigin, ContractModel, StableId
from .project_doc import ProjectDoc


RevisionKind = Literal["initial", "patch", "restore"]


class RevisionProvenance(ContractModel):
    """Why an immutable ProjectDoc revision exists."""

    kind: RevisionKind
    origin: ChangeOrigin
    actor_id: StableId | None = None
    request_id: StableId | None = None
    patch_id: StableId | None = None
    restored_from_revision_id: StableId | None = None
    summary: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def identifiers_match_kind(self) -> RevisionProvenance:
        if self.kind == "initial":
            if self.patch_id is not None or self.restored_from_revision_id is not None:
                raise ValueError("initial provenance cannot reference a patch or restore")
        elif self.kind == "patch":
            if self.patch_id is None:
                raise ValueError("patch provenance requires patch_id")
            if self.restored_from_revision_id is not None:
                raise ValueError("patch provenance cannot reference a restored revision")
        elif self.kind == "restore":
            if self.restored_from_revision_id is None:
                raise ValueError("restore provenance requires restored_from_revision_id")
            if self.patch_id is not None:
                raise ValueError("restore provenance cannot reference a patch")
        return self


class ProjectRevision(ContractModel):
    """An immutable ProjectDoc candidate or committed historical snapshot.

    Commit state is deliberately not stored on this value. A validated
    candidate becomes current only when ProjectService atomically advances the
    project's head; the document and its provenance never change in place.
    """

    commit_id: StableId
    created_at: datetime
    document_hash: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    provenance: RevisionProvenance
    document: ProjectDoc

    @model_validator(mode="after")
    def validate_revision(self) -> ProjectRevision:
        if self.created_at.utcoffset() is None:
            raise ValueError("revision created_at must be timezone-aware")

        parent_id = self.document.parent_revision_id
        if self.provenance.kind == "initial":
            if parent_id is not None:
                raise ValueError("initial revision cannot have a parent")
        elif parent_id is None:
            raise ValueError("non-initial revision requires a parent")

        restored_from = self.provenance.restored_from_revision_id
        if self.provenance.kind == "restore" and restored_from == parent_id:
            raise ValueError("restore source must differ from the current parent")
        return self


class RestoreRevisionRequest(ContractModel):
    """Request to copy an older snapshot into a new child revision."""

    request_version: Literal["1.0"] = "1.0"
    request_id: StableId
    project_id: StableId
    base_revision_id: StableId
    source_revision_id: StableId
    idempotency_key: StableId
    origin: ChangeOrigin
    actor_id: StableId | None = None
    summary: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def source_is_historical(self) -> RestoreRevisionRequest:
        if self.source_revision_id == self.base_revision_id:
            raise ValueError("source revision must differ from base revision")
        return self
