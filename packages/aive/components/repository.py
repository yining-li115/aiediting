"""Immutable storage-backed repository for generated render components."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts.components import (
    ComponentCandidate,
    ComponentSearchQuery,
    PublishedComponent,
)
from ..storage import StorageProvider, shared_key


CANDIDATE_KIND = "component-candidates"
REGISTRY_KIND = "component-registry"


class ComponentRegistryError(RuntimeError):
    pass


class ComponentCandidateConflict(ComponentRegistryError):
    pass


class ComponentPublicationConflict(ComponentRegistryError):
    pass


class ComponentCandidateNotFound(ComponentRegistryError):
    pass


@runtime_checkable
class ComponentRegistry(Protocol):
    def get_candidate(self, candidate_id: str) -> ComponentCandidate | None: ...

    def save_candidate(
        self,
        candidate: ComponentCandidate,
        *,
        source_archive: bytes,
        bundle_archive: bytes | None,
        preview_png: bytes | None,
    ) -> ComponentCandidate: ...

    def publish(
        self,
        component: PublishedComponent,
    ) -> PublishedComponent: ...

    def get(
        self,
        capability_kind: str,
        name: str,
        version: str,
    ) -> PublishedComponent | None: ...

    def search(self, query: ComponentSearchQuery) -> tuple[PublishedComponent, ...]: ...

    def published_artifact(
        self,
        component: PublishedComponent,
        filename: str,
    ) -> Path: ...


class StorageComponentRegistry:
    """Store generated code as data, never as files in the source tree."""

    def __init__(self, storage: StorageProvider) -> None:
        self._storage = storage

    def get_candidate(self, candidate_id: str) -> ComponentCandidate | None:
        key = self._candidate_key(candidate_id, "candidate.json")
        if not self._storage.exists(key):
            return None
        return ComponentCandidate.model_validate_json(
            self._storage.open_local(key).read_text(encoding="utf-8")
        )

    def save_candidate(
        self,
        candidate: ComponentCandidate,
        *,
        source_archive: bytes,
        bundle_archive: bytes | None,
        preview_png: bytes | None,
    ) -> ComponentCandidate:
        existing = self.get_candidate(candidate.candidate_id)
        if existing is not None:
            if existing == candidate:
                return existing
            raise ComponentCandidateConflict(
                f"candidate {candidate.candidate_id!r} already exists with different content"
            )

        self._storage.put_bytes(
            source_archive,
            self._candidate_key(candidate.candidate_id, "source.tar.gz"),
        )
        if bundle_archive is not None:
            self._storage.put_bytes(
                bundle_archive,
                self._candidate_key(candidate.candidate_id, "bundle.tar.gz"),
            )
        if preview_png is not None:
            self._storage.put_bytes(
                preview_png,
                self._candidate_key(candidate.candidate_id, "preview.png"),
            )
        self._storage.put_bytes(
            _canonical_json(candidate),
            self._candidate_key(candidate.candidate_id, "candidate.json"),
        )
        return candidate

    def publish(self, component: PublishedComponent) -> PublishedComponent:
        candidate = self.get_candidate(component.candidate_id)
        if candidate is None:
            raise ComponentCandidateNotFound(
                f"component candidate {component.candidate_id!r} does not exist"
            )
        if candidate.status != "validated":
            raise ComponentRegistryError("only validated candidates can be published")
        if (
            candidate.manifest != component.manifest
            or candidate.source_digest != component.source_digest
            or candidate.bundle_digest != component.bundle_digest
            or candidate.preview_digest != component.preview_digest
            or component.approval.candidate_id != component.candidate_id
        ):
            raise ComponentRegistryError(
                "published component must exactly match its validated candidate"
            )

        existing = self.get(
            component.manifest.capability_kind,
            component.manifest.name,
            component.manifest.version,
        )
        if existing is not None:
            if existing == component:
                return existing
            raise ComponentPublicationConflict(
                "component name and version are immutable once published"
            )

        base = self._published_prefix(component)
        self._copy_candidate_artifact(
            component.candidate_id,
            "source.tar.gz",
            f"{base}/source.tar.gz",
        )
        self._copy_candidate_artifact(
            component.candidate_id,
            "bundle.tar.gz",
            f"{base}/bundle.tar.gz",
        )
        self._copy_candidate_artifact(
            component.candidate_id,
            "preview.png",
            f"{base}/preview.png",
        )
        self._storage.put_bytes(
            _canonical_json(component),
            f"{base}/component.json",
        )
        return component

    def get(
        self,
        capability_kind: str,
        name: str,
        version: str,
    ) -> PublishedComponent | None:
        key = self._published_key(capability_kind, name, version)
        if not self._storage.exists(key):
            return None
        return PublishedComponent.model_validate_json(
            self._storage.open_local(key).read_text(encoding="utf-8")
        )

    def search(self, query: ComponentSearchQuery) -> tuple[PublishedComponent, ...]:
        prefix = shared_key(REGISTRY_KIND, "")
        matches: list[PublishedComponent] = []
        needle = (query.text or "").casefold().strip()
        for key in self._storage.iter_keys(prefix):
            if not key.endswith("/component.json"):
                continue
            component = PublishedComponent.model_validate_json(
                self._storage.open_local(key).read_text(encoding="utf-8")
            )
            manifest = component.manifest
            if query.usage is not None and manifest.usage != query.usage:
                continue
            haystack = " ".join(
                (
                    manifest.name,
                    manifest.usage,
                    manifest.license.license_id,
                )
            ).casefold()
            if needle and needle not in haystack:
                continue
            matches.append(component)
        matches.sort(key=lambda item: (item.manifest.name, item.manifest.version))
        return tuple(matches[: query.limit])

    def candidate_artifact(self, candidate_id: str, filename: str) -> Path:
        if filename not in {"source.tar.gz", "bundle.tar.gz", "preview.png"}:
            raise ValueError("unknown component candidate artifact")
        return self._storage.open_local(self._candidate_key(candidate_id, filename))

    def published_artifact(
        self,
        component: PublishedComponent,
        filename: str,
    ) -> Path:
        if filename not in {"source.tar.gz", "bundle.tar.gz", "preview.png"}:
            raise ValueError("unknown published component artifact")
        return self._storage.open_local(f"{self._published_prefix(component)}/{filename}")

    @staticmethod
    def _candidate_key(candidate_id: str, filename: str) -> str:
        return shared_key(CANDIDATE_KIND, f"{candidate_id}/{filename}")

    @staticmethod
    def _published_key(capability_kind: str, name: str, version: str) -> str:
        return shared_key(
            REGISTRY_KIND,
            f"{capability_kind}/{name}/{version}/component.json",
        )

    @staticmethod
    def _published_prefix(component: PublishedComponent) -> str:
        manifest = component.manifest
        return shared_key(
            REGISTRY_KIND,
            f"{manifest.capability_kind}/{manifest.name}/{manifest.version}",
        )

    def _copy_candidate_artifact(
        self,
        candidate_id: str,
        filename: str,
        target_key: str,
    ) -> None:
        source = self._storage.open_local(self._candidate_key(candidate_id, filename))
        self._storage.put(source, target_key)


def _canonical_json(model: ComponentCandidate | PublishedComponent) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = [
    "CANDIDATE_KIND",
    "REGISTRY_KIND",
    "ComponentCandidateConflict",
    "ComponentCandidateNotFound",
    "ComponentPublicationConflict",
    "ComponentRegistry",
    "ComponentRegistryError",
    "StorageComponentRegistry",
]
