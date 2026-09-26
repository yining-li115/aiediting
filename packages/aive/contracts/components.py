"""Contracts for generated Remotion components outside ProjectDoc state."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from .base import ContractModel, StableId


ComponentCapabilityKind = Literal["component", "motion", "transition", "effect"]
ComponentUsage = Literal["caption", "graphic", "motion", "transition", "effect"]
CandidateStatus = Literal["validated", "rejected"]
ValidationSeverity = Literal["warning", "error"]


class ComponentLicense(ContractModel):
    """License evidence frozen with a generated or imported implementation."""

    license_id: str = Field(min_length=1, max_length=128)
    source: Literal["generated", "remotion-official", "third-party"]
    commercial_use_allowed: bool
    source_url: str | None = Field(default=None, max_length=2_000)
    terms_url: str | None = Field(default=None, max_length=2_000)
    attribution: str | None = Field(default=None, max_length=1_000)


class RemotionComponentManifest(ContractModel):
    """Immutable identity and input schema for one render capability version."""

    manifest_version: Literal["1.0"] = "1.0"
    runtime_api_version: Literal["1.0"] = "1.0"
    capability_kind: ComponentCapabilityKind
    usage: ComponentUsage
    name: StableId
    version: str = Field(
        min_length=5,
        max_length=64,
        pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$",
    )
    renderer: Literal["remotion"] = "remotion"
    remotion_version: str = Field(
        min_length=5,
        max_length=64,
        pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$",
    )
    entrypoint: str = Field(default="component.tsx", min_length=1, max_length=240)
    export_name: str = Field(
        default="Component",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_$][A-Za-z0-9_$]*$",
    )
    parameter_schema: dict[str, JsonValue]
    example_props: dict[str, JsonValue] = Field(default_factory=dict)
    license: ComponentLicense
    created_by_run_id: StableId | None = None

    @field_validator("entrypoint")
    @classmethod
    def safe_entrypoint(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.suffix not in {".ts", ".tsx", ".js", ".jsx"}
        ):
            raise ValueError("component entrypoint must be a safe source path")
        return value

    @model_validator(mode="after")
    def usage_matches_capability(self) -> RemotionComponentManifest:
        expected = {
            "caption": "component",
            "graphic": "component",
            "motion": "motion",
            "transition": "transition",
            "effect": "effect",
        }[self.usage]
        if self.capability_kind != expected:
            raise ValueError(
                f"{self.usage} implementations require capability_kind {expected!r}"
            )
        if not self.name.startswith(f"{self.usage}."):
            raise ValueError(
                f"component name must start with {self.usage!r} followed by a dot"
            )
        return self


class ComponentSourceFile(ContractModel):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=200_000)

    @field_validator("path")
    @classmethod
    def safe_source_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.suffix not in {".ts", ".tsx", ".js", ".jsx", ".css", ".json"}
        ):
            raise ValueError("component source path is unsafe or unsupported")
        return value


class CreateComponentCandidateRequest(ContractModel):
    request_version: Literal["1.0"] = "1.0"
    request_id: StableId
    candidate_id: StableId
    manifest: RemotionComponentManifest
    source_files: tuple[ComponentSourceFile, ...] = Field(
        min_length=1,
        max_length=32,
    )

    @model_validator(mode="after")
    def source_tree_is_complete(self) -> CreateComponentCandidateRequest:
        paths = [source.path for source in self.source_files]
        if len(paths) != len(set(paths)):
            raise ValueError("component candidate contains duplicate source paths")
        if self.manifest.entrypoint not in paths:
            raise ValueError("component candidate does not contain its entrypoint")
        total_bytes = sum(len(source.content.encode("utf-8")) for source in self.source_files)
        if total_bytes > 1_000_000:
            raise ValueError("component candidate source exceeds 1 MB")
        return self


class ComponentValidationIssue(ContractModel):
    code: StableId
    severity: ValidationSeverity
    message: str = Field(min_length=1, max_length=2_000)
    source_path: str | None = Field(default=None, max_length=240)


class ComponentValidationReport(ContractModel):
    report_version: Literal["1.0"] = "1.0"
    accepted: bool
    sandboxed: bool
    network_disabled: bool
    build_succeeded: bool
    preview_succeeded: bool
    runtime_version: str = Field(min_length=1, max_length=64)
    imported_packages: tuple[str, ...] = ()
    issues: tuple[ComponentValidationIssue, ...] = ()
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def acceptance_matches_report(self) -> ComponentValidationReport:
        has_errors = any(issue.severity == "error" for issue in self.issues)
        expected = (
            self.sandboxed
            and self.network_disabled
            and self.build_succeeded
            and self.preview_succeeded
            and not has_errors
        )
        if self.accepted != expected:
            raise ValueError("component acceptance disagrees with validation evidence")
        return self


class ComponentCandidate(ContractModel):
    candidate_id: StableId
    request_id: StableId
    manifest: RemotionComponentManifest
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    bundle_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    preview_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: CandidateStatus
    report: ComponentValidationReport
    created_at: datetime

    @model_validator(mode="after")
    def accepted_artifacts_exist(self) -> ComponentCandidate:
        if self.created_at.utcoffset() is None:
            raise ValueError("component candidate timestamp must be timezone-aware")
        if self.status == "validated":
            if not self.report.accepted or not self.bundle_digest or not self.preview_digest:
                raise ValueError("validated candidate requires accepted build artifacts")
        elif self.report.accepted:
            raise ValueError("rejected candidate cannot contain an accepted report")
        return self


class ComponentApproval(ContractModel):
    candidate_id: StableId
    approved_by: StableId
    summary: str = Field(min_length=1, max_length=1_000)
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("component approval timestamp must be timezone-aware")
        return value


class PublishComponentRequest(ContractModel):
    request_version: Literal["1.0"] = "1.0"
    candidate_id: StableId
    approved_by: StableId
    summary: str = Field(min_length=1, max_length=1_000)


class PublishedComponent(ContractModel):
    manifest: RemotionComponentManifest
    candidate_id: StableId
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval: ComponentApproval

    @model_validator(mode="after")
    def approval_matches_candidate(self) -> PublishedComponent:
        if self.approval.candidate_id != self.candidate_id:
            raise ValueError("component approval must name the published candidate")
        return self


class ComponentSearchQuery(ContractModel):
    text: str | None = Field(default=None, max_length=500)
    usage: ComponentUsage | None = None
    limit: int = Field(default=20, ge=1, le=100)


__all__ = [
    "CandidateStatus",
    "ComponentApproval",
    "ComponentCandidate",
    "ComponentCapabilityKind",
    "ComponentLicense",
    "ComponentSearchQuery",
    "ComponentSourceFile",
    "ComponentUsage",
    "ComponentValidationIssue",
    "ComponentValidationReport",
    "CreateComponentCandidateRequest",
    "PublishedComponent",
    "PublishComponentRequest",
    "RemotionComponentManifest",
]
