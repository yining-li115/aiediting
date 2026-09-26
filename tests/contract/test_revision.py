from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from aive.contracts.project_doc import ProjectDoc
from aive.contracts.revision import (
    ProjectRevision,
    RestoreRevisionRequest,
    RevisionProvenance,
)


def _document(
    *,
    revision_id: str = "revision_01",
    parent_revision_id: str | None = None,
) -> ProjectDoc:
    example = Path(__file__).parents[2] / "examples" / "project_doc_v1.json"
    payload = json.loads(example.read_text(encoding="utf-8"))
    payload["revision_id"] = revision_id
    payload["parent_revision_id"] = parent_revision_id
    return ProjectDoc.model_validate(payload)


def _revision(**overrides: object) -> ProjectRevision:
    values: dict[str, object] = {
        "commit_id": "commit_01",
        "created_at": datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
        "document_hash": "a" * 64,
        "provenance": RevisionProvenance(kind="initial", origin="user"),
        "document": _document(),
    }
    values.update(overrides)
    return ProjectRevision(**values)


def test_initial_revision_is_a_closed_immutable_snapshot() -> None:
    revision = _revision()

    restored = ProjectRevision.model_validate_json(revision.model_dump_json())

    assert restored == revision
    assert restored.document.project_id == "project_demo"
    assert restored.document.parent_revision_id is None
    with pytest.raises(ValidationError, match="frozen_instance"):
        revision.document_hash = "b" * 64


def test_patch_revision_requires_patch_provenance_and_a_parent() -> None:
    provenance = RevisionProvenance(
        kind="patch",
        origin="agent",
        patch_id="patch_01",
        request_id="agent_turn_01",
    )
    revision = _revision(
        provenance=provenance,
        document=_document(
            revision_id="revision_02",
            parent_revision_id="revision_01",
        ),
    )

    assert revision.provenance.patch_id == "patch_01"

    with pytest.raises(ValidationError, match="patch provenance requires patch_id"):
        RevisionProvenance(kind="patch", origin="agent")

    with pytest.raises(ValidationError, match="non-initial revision requires a parent"):
        _revision(provenance=provenance)


def test_restore_creates_a_new_revision_instead_of_mutating_history() -> None:
    request = RestoreRevisionRequest(
        request_id="restore_request_01",
        project_id="project_demo",
        base_revision_id="revision_05",
        source_revision_id="revision_02",
        idempotency_key="restore-project-demo-01",
        origin="user",
        summary="Restore the approved opening and pacing",
    )
    revision = _revision(
        commit_id="commit_restore_01",
        provenance=RevisionProvenance(
            kind="restore",
            origin=request.origin,
            request_id=request.request_id,
            restored_from_revision_id=request.source_revision_id,
        ),
        document=_document(
            revision_id="revision_06",
            parent_revision_id=request.base_revision_id,
        ),
    )

    assert revision.document.revision_id == "revision_06"
    assert revision.document.parent_revision_id == "revision_05"
    assert revision.provenance.restored_from_revision_id == "revision_02"


def test_restore_request_rejects_a_no_op_restore() -> None:
    with pytest.raises(ValidationError, match="source revision must differ"):
        RestoreRevisionRequest(
            request_id="restore_request_01",
            project_id="project_demo",
            base_revision_id="revision_05",
            source_revision_id="revision_05",
            idempotency_key="restore-project-demo-01",
            origin="user",
        )


def test_revision_timestamp_must_include_a_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _revision(created_at=datetime(2026, 9, 26, 10, 0))


def test_revision_schema_is_closed() -> None:
    schema = ProjectRevision.model_json_schema()
    encoded = json.dumps(schema)

    assert schema["additionalProperties"] is False
    assert "RevisionProvenance" in encoded
    assert "ProjectDoc" in encoded
