from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aive.contracts.components import (
    ComponentApproval,
    ComponentLicense,
    ComponentSourceFile,
    CreateComponentCandidateRequest,
    PublishedComponent,
    RemotionComponentManifest,
)


def _manifest() -> RemotionComponentManifest:
    return RemotionComponentManifest(
        capability_kind="component",
        usage="caption",
        name="caption.word-bounce",
        version="1.0.0",
        remotion_version="4.0.529",
        parameter_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        example_props={"text": "Hello"},
        license=ComponentLicense(
            license_id="generated-commercial-v1",
            source="generated",
            commercial_use_allowed=True,
        ),
    )


def test_component_candidate_is_closed_and_contains_entrypoint() -> None:
    request = CreateComponentCandidateRequest(
        request_id="request_component_1",
        candidate_id="candidate_component_1",
        manifest=_manifest(),
        source_files=(
            ComponentSourceFile(
                path="component.tsx",
                content="export const Component = () => null;",
            ),
        ),
    )

    assert request.manifest.name == "caption.word-bounce"

    with pytest.raises(ValidationError, match="does not contain its entrypoint"):
        request.model_copy(
            update={
                "source_files": (
                    ComponentSourceFile(
                        path="other.tsx",
                        content="export const Component = () => null;",
                    ),
                )
            }
        ).model_validate(
            request.model_copy(
                update={
                    "source_files": (
                        ComponentSourceFile(
                            path="other.tsx",
                            content="export const Component = () => null;",
                        ),
                    )
                }
            ).model_dump()
        )


def test_component_source_cannot_escape_candidate_root() -> None:
    with pytest.raises(ValidationError, match="unsafe"):
        ComponentSourceFile(path="../secret.ts", content="")


def test_manifest_usage_and_capability_must_agree() -> None:
    with pytest.raises(ValidationError, match="require capability_kind"):
        _manifest().model_copy(
            update={"usage": "transition"}
        ).__class__.model_validate(
            _manifest().model_copy(update={"usage": "transition"}).model_dump()
        )


def test_published_component_binds_exact_candidate_approval() -> None:
    component = PublishedComponent(
        manifest=_manifest(),
        candidate_id="candidate_component_1",
        source_digest="a" * 64,
        bundle_digest="b" * 64,
        preview_digest="c" * 64,
        approval=ComponentApproval(
            candidate_id="candidate_component_1",
            approved_by="reviewer_1",
            summary="Build and preview accepted.",
            approved_at=datetime.now(timezone.utc),
        ),
    )

    assert component.approval.candidate_id == component.candidate_id

    with pytest.raises(ValidationError, match="approval must name"):
        PublishedComponent(
            manifest=_manifest(),
            candidate_id="candidate_component_1",
            source_digest="a" * 64,
            bundle_digest="b" * 64,
            preview_digest="c" * 64,
            approval=component.approval.model_copy(
                update={"candidate_id": "candidate_component_2"}
            ),
        )
