"""Upload application service: bytes and metadata in, one typed Patch out."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, Protocol

from ..contracts.media import UploadAssetItem, UploadAssetsRequest, UploadedAsset
from ..contracts.patch import Patch, PatchScope
from ..contracts.patch_project import AddAsset
from ..contracts.project_doc import AssetRef, AssetRole
from ..material.probe import ProbeError, ffprobe, probe_asset
from ..project.repository import RevisionWriteResult
from ..specs.enums import AUDIO_SUFFIXES, PHOTO_SUFFIXES, VIDEO_SUFFIXES
from ..storage import ORIGINAL, StorageProvider, project_key, safe_name, sha256_of
from .repository import AssetIdentityConflict, MediaAssetRecord, MediaAssetRepository


UPLOAD_CHUNK_BYTES = 1024 * 1024
DEFAULT_MAX_SOURCE_BYTES = 10 * 1024**3
DEFAULT_MAX_REFERENCE_BYTES = 5 * 1024**3
SUPPORTED_MEDIA_SUFFIXES = VIDEO_SUFFIXES | PHOTO_SUFFIXES | AUDIO_SUFFIXES


@dataclass(frozen=True)
class IncomingMedia:
    file_name: str
    stream: BinaryIO


@dataclass(frozen=True)
class MediaProbeResult:
    kind: Literal["video", "photo", "audio"]
    duration_seconds: float
    width: int
    height: int
    fps: float
    has_audio: bool


@dataclass(frozen=True)
class MediaUploadIssue:
    code: str
    message: str
    asset_id: str | None = None
    file_name: str | None = None


class MediaUploadRejected(ValueError):
    def __init__(self, issues: Sequence[MediaUploadIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


class AssetStorageConflict(RuntimeError):
    pass


class ProjectPatchApplication(Protocol):
    def apply_patch(self, patch: Patch) -> RevisionWriteResult: ...


ProbeMedia = Callable[[Path, str, str, str], MediaProbeResult]


@dataclass(frozen=True)
class MediaUploadResult:
    assets: tuple[UploadedAsset, ...]
    revision_write: RevisionWriteResult


@dataclass(frozen=True)
class _StagedMedia:
    item_index: int
    path: Path
    record: MediaAssetRecord


def _default_probe(
    path: Path,
    project_id: str,
    asset_id: str,
    storage_key: str,
) -> MediaProbeResult:
    if path.suffix.lower() in AUDIO_SUFFIXES:
        info = ffprobe(path)
        streams = info.get("streams", [])
        audio = next(
            (stream for stream in streams if stream.get("codec_type") == "audio"),
            None,
        )
        if audio is None:
            raise ProbeError(f"{path} has no audio stream")
        duration = float(
            info.get("format", {}).get("duration") or audio.get("duration") or 0
        )
        if duration <= 0:
            raise ProbeError(f"{path} reports a non-positive duration")
        return MediaProbeResult(
            kind="audio",
            duration_seconds=duration,
            width=1,
            height=1,
            fps=0,
            has_audio=True,
        )
    asset = probe_asset(path, project_id, asset_id, storage_key)
    return MediaProbeResult(
        kind=asset.kind,
        duration_seconds=asset.duration,
        width=asset.width,
        height=asset.height,
        fps=asset.fps,
        has_audio=asset.has_audio,
    )


class MediaUploadApplication:
    """Registers a complete upload batch before committing its AssetRefs."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        assets: MediaAssetRepository,
        projects: ProjectPatchApplication,
        probe: ProbeMedia = _default_probe,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
        max_reference_bytes: int = DEFAULT_MAX_REFERENCE_BYTES,
    ) -> None:
        self._storage = storage
        self._assets = assets
        self._projects = projects
        self._probe = probe
        self._max_source_bytes = max_source_bytes
        self._max_reference_bytes = max_reference_bytes

    def upload_assets(
        self,
        request: UploadAssetsRequest,
        files: Sequence[IncomingMedia],
    ) -> MediaUploadResult:
        if len(request.items) != len(files):
            raise MediaUploadRejected(
                (
                    MediaUploadIssue(
                        code="file_manifest_mismatch",
                        message=(
                            f"upload manifest declares {len(request.items)} files "
                            f"but multipart body contains {len(files)}"
                        ),
                    ),
                )
            )

        staged: list[_StagedMedia] = []
        issues: list[MediaUploadIssue] = []
        try:
            for index, (item, incoming) in enumerate(zip(request.items, files)):
                try:
                    staged.append(self._stage(request, index, item, incoming))
                except MediaUploadRejected as exc:
                    issues.extend(exc.issues)
            if issues:
                raise MediaUploadRejected(issues)

            records = tuple(item.record for item in staged)
            self._store_and_register(records, staged)
            uploaded = tuple(
                self._to_uploaded(item.record, request.items[item.item_index].roles)
                for item in staged
            )
            patch = Patch(
                patch_id=request.patch_id,
                project_id=request.project_id,
                base_revision_id=request.base_revision_id,
                idempotency_key=request.idempotency_key,
                origin=request.origin,
                request_id=request.request_id,
                summary=request.summary
                or f"Add {len(uploaded)} uploaded media asset(s)",
                scope=PatchScope(
                    kind="bounded",
                    target_ids=tuple(asset.asset.asset_id for asset in uploaded),
                ),
                operations=tuple(
                    AddAsset(
                        operation_id=f"op_add_{asset.asset.asset_id}",
                        asset=asset.asset,
                    )
                    for asset in uploaded
                ),
            )
            return MediaUploadResult(
                assets=uploaded,
                revision_write=self._projects.apply_patch(patch),
            )
        finally:
            for item in staged:
                item.path.unlink(missing_ok=True)

    def _stage(
        self,
        request: UploadAssetsRequest,
        index: int,
        item: UploadAssetItem,
        incoming: IncomingMedia,
    ) -> _StagedMedia:
        name = _display_name(incoming.file_name)
        if not name or len(name) > 255:
            raise _rejection(
                "invalid_file_name",
                "uploaded file name must contain 1 to 255 characters",
                item.asset_id,
                name or None,
            )
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_MEDIA_SUFFIXES:
            raise _rejection(
                "unsupported_media_type",
                f"{name!r} is not a supported video, photo, or audio file",
                item.asset_id,
                name,
            )

        limit = (
            self._max_reference_bytes
            if "reference" in item.roles
            else self._max_source_bytes
        )
        temp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        temp_path = Path(temp.name)
        size = 0
        try:
            with temp:
                while chunk := incoming.stream.read(UPLOAD_CHUNK_BYTES):
                    size += len(chunk)
                    if size > limit:
                        raise _rejection(
                            "file_too_large",
                            f"{name!r} exceeds the {limit}-byte upload limit",
                            item.asset_id,
                            name,
                        )
                    temp.write(chunk)
            if size == 0:
                raise _rejection(
                    "empty_media",
                    f"{name!r} is empty",
                    item.asset_id,
                    name,
                )

            key = project_key(
                request.project_id,
                ORIGINAL,
                safe_name(name, item.asset_id),
            )
            try:
                probed = self._probe(
                    temp_path,
                    request.project_id,
                    item.asset_id,
                    key,
                )
            except ProbeError as exc:
                raise _rejection(
                    "media_probe_failed",
                    str(exc),
                    item.asset_id,
                    name,
                ) from exc
            if "reference" in item.roles and probed.kind != "video":
                raise _rejection(
                    "reference_requires_video",
                    f"{name!r} is a photo; reference uploads must be videos",
                    item.asset_id,
                    name,
                )
            if probed.kind == "audio" and not (
                {"music", "voiceover", "sfx"} & set(item.roles)
            ):
                raise _rejection(
                    "audio_requires_audio_role",
                    f"{name!r} must be uploaded as music, voiceover, or sfx",
                    item.asset_id,
                    name,
                )
            record = MediaAssetRecord(
                asset_id=item.asset_id,
                project_id=request.project_id,
                storage_key=key,
                kind=probed.kind,
                original_name=name,
                sha256=sha256_of(temp_path),
                size_bytes=size,
                duration_seconds=probed.duration_seconds,
                width=probed.width,
                height=probed.height,
                fps=probed.fps,
                has_audio=probed.has_audio,
            )
            return _StagedMedia(item_index=index, path=temp_path, record=record)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _store_and_register(
        self,
        records: tuple[MediaAssetRecord, ...],
        staged: Sequence[_StagedMedia],
    ) -> None:
        written: list[str] = []
        try:
            for item in staged:
                record = item.record
                existing = self._assets.get_asset(record.asset_id)
                if existing is not None and existing != record:
                    raise AssetIdentityConflict(
                        f"asset id {record.asset_id!r} was reused for different media"
                    )
                if self._storage.exists(record.storage_key):
                    stored_hash = sha256_of(
                        self._storage.open_local(record.storage_key)
                    )
                    if stored_hash != record.sha256:
                        raise AssetStorageConflict(
                            f"storage key {record.storage_key!r} contains different media"
                        )
                    continue
                self._storage.put(item.path, record.storage_key)
                written.append(record.storage_key)
            self._assets.register_assets(records)
        except Exception:
            for key in written:
                self._storage.delete(key)
            raise

    @staticmethod
    def _to_uploaded(
        record: MediaAssetRecord,
        roles: tuple[AssetRole, ...],
    ) -> UploadedAsset:
        return UploadedAsset(
            asset=AssetRef(
                asset_id=record.asset_id,
                kind=record.kind,
                roles=roles,
                content_hash=record.sha256,
                label=record.original_name,
            ),
            original_name=record.original_name,
            duration_seconds=record.duration_seconds,
            width=record.width,
            height=record.height,
            fps=record.fps,
            has_audio=record.has_audio,
            size_bytes=record.size_bytes,
        )


def _display_name(raw: str) -> str:
    return raw.replace("\\", "/").rsplit("/", 1)[-1]


def _rejection(
    code: str,
    message: str,
    asset_id: str | None,
    file_name: str | None,
) -> MediaUploadRejected:
    return MediaUploadRejected(
        (
            MediaUploadIssue(
                code=code,
                message=message,
                asset_id=asset_id,
                file_name=file_name,
            ),
        )
    )
