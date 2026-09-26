from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from aive.components import (
    ComponentCandidateConflict,
    ComponentCandidateRejected,
    ComponentPublicationConflict,
    ComponentRegistryApplication,
    ComponentSandboxArtifacts,
    RegisteredCapabilityValidator,
    StorageComponentRegistry,
)
from aive.contracts.components import (
    ComponentLicense,
    ComponentSearchQuery,
    ComponentSourceFile,
    ComponentValidationIssue,
    ComponentValidationReport,
    CreateComponentCandidateRequest,
    RemotionComponentManifest,
)
from aive.project.service import CapabilityUse
from aive.storage import LocalStorage


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


class _Sandbox:
    def __init__(self, *, accepted: bool = True) -> None:
        self.calls = 0
        self.accepted = accepted

    def validate(
        self,
        request: CreateComponentCandidateRequest,
    ) -> ComponentSandboxArtifacts:
        self.calls += 1
        issues = ()
        if not self.accepted:
            issues = (
                ComponentValidationIssue(
                    code="build_failed",
                    severity="error",
                    message="TypeScript did not compile",
                ),
            )
        return ComponentSandboxArtifacts(
            report=ComponentValidationReport(
                accepted=self.accepted,
                sandboxed=True,
                network_disabled=True,
                build_succeeded=self.accepted,
                preview_succeeded=self.accepted,
                runtime_version="4.0.529",
                imported_packages=("react", "remotion"),
                issues=issues,
                duration_ms=12,
            ),
            source_archive=b"source",
            bundle_archive=b"bundle" if self.accepted else None,
            preview_png=b"preview" if self.accepted else None,
        )


def _request(
    *,
    candidate_id: str = "candidate_1",
    commercial: bool = True,
) -> CreateComponentCandidateRequest:
    return CreateComponentCandidateRequest(
        request_id="request_1",
        candidate_id=candidate_id,
        manifest=RemotionComponentManifest(
            capability_kind="component",
            usage="graphic",
            name="graphic.title-card",
            version="1.0.0",
            remotion_version="4.0.529",
            parameter_schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            example_props={"title": "Launch"},
            license=ComponentLicense(
                license_id="generated-commercial-v1",
                source="generated",
                commercial_use_allowed=commercial,
            ),
        ),
        source_files=(
            ComponentSourceFile(
                path="component.tsx",
                content="export const Component = () => null;",
            ),
        ),
    )


def _application(tmp_path: Path, sandbox: _Sandbox):
    registry = StorageComponentRegistry(LocalStorage(tmp_path / "storage"))
    return (
        ComponentRegistryApplication(
            registry=registry,
            sandbox=sandbox,
            clock=lambda: NOW,
        ),
        registry,
    )


def test_validated_candidate_publishes_outside_source_tree(tmp_path: Path) -> None:
    app, registry = _application(tmp_path, _Sandbox())

    candidate = app.create_candidate(_request())
    published = app.publish(
        candidate_id=candidate.candidate_id,
        approved_by="reviewer_1",
        summary="Compiled and rendered in the isolated runtime.",
    )

    assert candidate.status == "validated"
    assert published.manifest.name == "graphic.title-card"
    assert (
        app.publish(
            candidate_id=candidate.candidate_id,
            approved_by="retrying_reviewer",
            summary="Retry after a lost response.",
        )
        == published
    )
    assert app.search(ComponentSearchQuery(text="title")) == (published,)
    assert registry.published_artifact(published, "bundle.tar.gz").read_bytes() == b"bundle"
    assert not (tmp_path / "graphic.title-card.tsx").exists()


def test_published_name_and_version_cannot_be_replaced(tmp_path: Path) -> None:
    app, _ = _application(tmp_path, _Sandbox())
    first = app.create_candidate(_request(candidate_id="candidate_1"))
    app.publish(
        candidate_id=first.candidate_id,
        approved_by="reviewer_1",
        summary="Validated first implementation.",
    )
    replacement_request = _request(candidate_id="candidate_2")
    replacement = app.create_candidate(
        replacement_request.model_copy(
            update={
                "source_files": (
                    ComponentSourceFile(
                        path="component.tsx",
                        content="export const Component = () => <div />;",
                    ),
                )
            }
        )
    )

    with pytest.raises(ComponentPublicationConflict):
        app.publish(
            candidate_id=replacement.candidate_id,
            approved_by="reviewer_1",
            summary="Attempted replacement without a version bump.",
        )


def test_candidate_id_replay_is_idempotent_but_mismatch_conflicts(tmp_path: Path) -> None:
    sandbox = _Sandbox()
    app, _ = _application(tmp_path, sandbox)
    request = _request()

    first = app.create_candidate(request)
    replay = app.create_candidate(request)

    assert replay == first
    assert sandbox.calls == 1
    with pytest.raises(ComponentCandidateConflict):
        app.create_candidate(
            request.model_copy(
                update={
                    "source_files": (
                        ComponentSourceFile(
                            path="component.tsx",
                            content="export const Component = () => <div />;",
                        ),
                    )
                }
            )
        )


def test_noncommercial_component_is_rejected_before_sandbox(tmp_path: Path) -> None:
    sandbox = _Sandbox()
    app, _ = _application(tmp_path, sandbox)

    candidate = app.create_candidate(_request(commercial=False))

    assert candidate.status == "rejected"
    assert candidate.report.issues[0].code == "commercial_license_required"
    assert sandbox.calls == 0
    with pytest.raises(ComponentCandidateRejected):
        app.publish(
            candidate_id=candidate.candidate_id,
            approved_by="reviewer_1",
            summary="Do not publish.",
        )


def test_registry_capability_validator_checks_exact_schema(tmp_path: Path) -> None:
    app, registry = _application(tmp_path, _Sandbox())
    candidate = app.create_candidate(_request())
    app.publish(
        candidate_id=candidate.candidate_id,
        approved_by="reviewer_1",
        summary="Validated.",
    )
    validator = RegisteredCapabilityValidator(registry)

    assert (
        validator.rejection_reason(
            CapabilityUse(
                kind="component",
                name="graphic.title-card",
                version="1.0.0",
                owner_id="graphic_1",
                parameters={"title": "Launch"},
            )
        )
        is None
    )
    assert "required property" in (
        validator.rejection_reason(
            CapabilityUse(
                kind="component",
                name="graphic.title-card",
                version="1.0.0",
                owner_id="graphic_1",
                parameters={},
            )
        )
        or ""
    )
