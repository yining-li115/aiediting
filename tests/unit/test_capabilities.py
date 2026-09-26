from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from aive.contracts.project_doc import ProjectDoc
from aive.contracts.revision import ProjectRevision, RevisionProvenance
from aive.project.capabilities import (
    PreserveCommittedCapabilities,
    UnconfiguredCapabilityValidator,
)
from aive.project.hashing import canonical_model_hash
from aive.project.service import iter_capability_uses


def _committed_revision() -> ProjectRevision:
    path = Path(__file__).parents[2] / "examples" / "project_doc_v1.json"
    document = ProjectDoc.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return ProjectRevision(
        commit_id="commit_capabilities",
        created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(kind="initial", origin="system"),
        document=document,
    )


def test_exact_committed_capability_survives_an_unrelated_edit() -> None:
    revision = _committed_revision()
    capability = next(iter_capability_uses(revision.document))
    validator = PreserveCommittedCapabilities(
        UnconfiguredCapabilityValidator(),
        (revision,),
    )

    assert validator.rejection_reason(capability) is None


def test_changed_capability_parameters_still_fail_closed() -> None:
    revision = _committed_revision()
    capability = next(iter_capability_uses(revision.document))
    changed = replace(capability, parameters={"strength": 0.99})
    validator = PreserveCommittedCapabilities(
        UnconfiguredCapabilityValidator(),
        (revision,),
    )

    reason = validator.rejection_reason(changed)
    assert reason is not None
    assert "is not installed" in reason
