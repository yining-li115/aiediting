"""Typed response envelopes owned by the HTTP boundary."""

from aive.contracts.base import ContractModel
from aive.contracts.media import UploadedAsset
from aive.contracts.revision import ProjectRevision
from aive.project.commit_state import CommitAttempt


class RevisionMutationResponse(ContractModel):
    attempt: CommitAttempt
    revision: ProjectRevision | None
    replayed: bool = False


class AssetUploadResponse(RevisionMutationResponse):
    assets: tuple[UploadedAsset, ...]
    processing_job_id: str | None = None
