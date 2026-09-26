"""Application service for sandbox validation and immutable publication."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ..contracts.components import (
    ComponentApproval,
    ComponentCandidate,
    ComponentSearchQuery,
    ComponentValidationIssue,
    ComponentValidationReport,
    CreateComponentCandidateRequest,
    PublishedComponent,
)
from .repository import ComponentCandidateConflict, ComponentRegistry
from .sandbox import (
    ComponentSandbox,
    ComponentSandboxArtifacts,
    source_archive,
    source_digest,
)


class ComponentApplicationError(RuntimeError):
    pass


class ComponentCandidateRejected(ComponentApplicationError):
    pass


class ComponentRegistryApplication:
    """Keep generated code out of Git while retaining audited immutable artifacts."""

    def __init__(
        self,
        *,
        registry: ComponentRegistry,
        sandbox: ComponentSandbox,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._registry = registry
        self._sandbox = sandbox
        self._clock = clock

    def create_candidate(
        self,
        request: CreateComponentCandidateRequest,
    ) -> ComponentCandidate:
        digest = source_digest(request)
        existing = self._registry.get_candidate(request.candidate_id)
        if existing is not None:
            if existing.manifest == request.manifest and existing.source_digest == digest:
                return existing
            raise ComponentCandidateConflict(
                f"candidate {request.candidate_id!r} was reused for different source"
            )

        preflight = _preflight(request)
        artifacts = preflight or self._sandbox.validate(request)
        candidate = ComponentCandidate(
            candidate_id=request.candidate_id,
            request_id=request.request_id,
            manifest=request.manifest,
            source_digest=digest,
            bundle_digest=(
                _sha256(artifacts.bundle_archive)
                if artifacts.bundle_archive is not None
                else None
            ),
            preview_digest=(
                _sha256(artifacts.preview_png)
                if artifacts.preview_png is not None
                else None
            ),
            status="validated" if artifacts.report.accepted else "rejected",
            report=artifacts.report,
            created_at=self._clock(),
        )
        return self._registry.save_candidate(
            candidate,
            source_archive=artifacts.source_archive,
            bundle_archive=artifacts.bundle_archive,
            preview_png=artifacts.preview_png,
        )

    def publish(
        self,
        *,
        candidate_id: str,
        approved_by: str,
        summary: str,
    ) -> PublishedComponent:
        candidate = self._registry.get_candidate(candidate_id)
        if candidate is None:
            raise ComponentApplicationError(
                f"component candidate {candidate_id!r} does not exist"
            )
        if candidate.status != "validated":
            raise ComponentCandidateRejected(
                f"component candidate {candidate_id!r} did not pass validation"
            )
        if candidate.bundle_digest is None or candidate.preview_digest is None:
            raise ComponentCandidateRejected(
                f"component candidate {candidate_id!r} has incomplete artifacts"
            )
        existing = self._registry.get(
            candidate.manifest.capability_kind,
            candidate.manifest.name,
            candidate.manifest.version,
        )
        if existing is not None and existing.candidate_id == candidate.candidate_id:
            return existing
        component = PublishedComponent(
            manifest=candidate.manifest,
            candidate_id=candidate.candidate_id,
            source_digest=candidate.source_digest,
            bundle_digest=candidate.bundle_digest,
            preview_digest=candidate.preview_digest,
            approval=ComponentApproval(
                candidate_id=candidate.candidate_id,
                approved_by=approved_by,
                summary=summary,
                approved_at=self._clock(),
            ),
        )
        return self._registry.publish(component)

    def get_candidate(self, candidate_id: str) -> ComponentCandidate | None:
        return self._registry.get_candidate(candidate_id)

    def search(self, query: ComponentSearchQuery) -> tuple[PublishedComponent, ...]:
        return self._registry.search(query)


def _preflight(
    request: CreateComponentCandidateRequest,
) -> ComponentSandboxArtifacts | None:
    issue: ComponentValidationIssue | None = None
    try:
        Draft202012Validator.check_schema(request.manifest.parameter_schema)
        Draft202012Validator(request.manifest.parameter_schema).validate(
            request.manifest.example_props
        )
    except SchemaError as exc:
        issue = ComponentValidationIssue(
            code="parameter_schema_invalid",
            severity="error",
            message=_jsonschema_message(exc),
        )
    except ValidationError as exc:
        issue = ComponentValidationIssue(
            code="example_props_invalid",
            severity="error",
            message=_jsonschema_message(exc),
        )
    if issue is None and not request.manifest.license.commercial_use_allowed:
        issue = ComponentValidationIssue(
            code="commercial_license_required",
            severity="error",
            message="component license does not permit commercial use",
        )
    if issue is None:
        return None
    return ComponentSandboxArtifacts(
        report=ComponentValidationReport(
            accepted=False,
            sandboxed=False,
            network_disabled=False,
            build_succeeded=False,
            preview_succeeded=False,
            runtime_version=request.manifest.remotion_version,
            issues=(issue,),
            duration_ms=0,
        ),
        source_archive=source_archive(request),
        bundle_archive=None,
        preview_png=None,
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _jsonschema_message(exc: SchemaError | ValidationError) -> str:
    path = ".".join(str(part) for part in exc.absolute_path)
    prefix = f"{path}: " if path else ""
    return f"{prefix}{exc.message}"[:2_000]


__all__ = [
    "ComponentApplicationError",
    "ComponentCandidateRejected",
    "ComponentRegistryApplication",
]
