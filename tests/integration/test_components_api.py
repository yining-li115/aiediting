from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from aive.components import (
    ComponentRegistryApplication,
    ComponentSandboxArtifacts,
    StorageComponentRegistry,
)
from aive.contracts.components import (
    ComponentLicense,
    ComponentSourceFile,
    ComponentValidationReport,
    CreateComponentCandidateRequest,
    RemotionComponentManifest,
)
from aive.storage import LocalStorage
from apps.api.component_dependencies import (
    get_component_application,
    get_component_registry,
)


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


class _AcceptedSandbox:
    def validate(self, request):
        return ComponentSandboxArtifacts(
            report=ComponentValidationReport(
                accepted=True,
                sandboxed=True,
                network_disabled=True,
                build_succeeded=True,
                preview_succeeded=True,
                runtime_version="4.0.529",
                duration_ms=5,
            ),
            source_archive=b"source",
            bundle_archive=b"bundle",
            preview_png=b"\x89PNG\r\n\x1a\npreview",
        )


def _request() -> CreateComponentCandidateRequest:
    return CreateComponentCandidateRequest(
        request_id="request_component_api",
        candidate_id="candidate_component_api",
        manifest=RemotionComponentManifest(
            capability_kind="component",
            usage="graphic",
            name="graphic.api-proof",
            version="1.0.0",
            remotion_version="4.0.529",
            parameter_schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            example_props={"title": "API"},
            license=ComponentLicense(
                license_id="generated-commercial-v1",
                source="generated",
                commercial_use_allowed=True,
            ),
        ),
        source_files=(
            ComponentSourceFile(
                path="component.tsx",
                content="export const Component = () => null;",
            ),
        ),
    )


def test_candidate_review_publish_and_search_http_loop(tmp_path) -> None:
    registry = StorageComponentRegistry(LocalStorage(tmp_path / "registry"))
    application = ComponentRegistryApplication(
        registry=registry,
        sandbox=_AcceptedSandbox(),
        clock=lambda: NOW,
    )
    candidate = application.create_candidate(_request())

    from apps.api.main import app

    app.dependency_overrides[get_component_application] = lambda: application
    app.dependency_overrides[get_component_registry] = lambda: registry
    try:
        with TestClient(app) as client:
            fetched = client.get(f"/components/candidates/{candidate.candidate_id}")
            preview = client.get(
                f"/components/candidates/{candidate.candidate_id}/preview"
            )
            published = client.post(
                f"/components/candidates/{candidate.candidate_id}/publish",
                json={
                    "candidate_id": candidate.candidate_id,
                    "approved_by": "user_owner",
                    "summary": "Approved after preview inspection.",
                },
            )
            found = client.get("/components", params={"usage": "graphic"})
    finally:
        app.dependency_overrides.clear()

    assert fetched.status_code == 200
    assert fetched.json()["status"] == "validated"
    assert preview.status_code == 200
    assert preview.content.startswith(b"\x89PNG")
    assert published.status_code == 200
    assert published.json()["approval"]["approved_by"] == "user_owner"
    assert found.status_code == 200
    assert [item["manifest"]["name"] for item in found.json()] == [
        "graphic.api-proof"
    ]


def test_publish_path_and_body_candidate_must_match(tmp_path) -> None:
    registry = StorageComponentRegistry(LocalStorage(tmp_path / "registry"))
    application = ComponentRegistryApplication(
        registry=registry,
        sandbox=_AcceptedSandbox(),
        clock=lambda: NOW,
    )

    from apps.api.main import app

    app.dependency_overrides[get_component_application] = lambda: application
    try:
        with TestClient(app) as client:
            response = client.post(
                "/components/candidates/candidate_path/publish",
                json={
                    "candidate_id": "candidate_body",
                    "approved_by": "user_owner",
                    "summary": "Mismatched request.",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "request candidate_id must match the URL candidate_id"
    )
