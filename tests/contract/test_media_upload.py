from __future__ import annotations

import pytest
from pydantic import ValidationError

from aive.contracts.media import AssetProcessingStatus, UploadAssetsRequest


def _payload() -> dict:
    return {
        "request_id": "request_upload_001",
        "patch_id": "patch_upload_001",
        "project_id": "project_upload_001",
        "base_revision_id": "revision_initial",
        "idempotency_key": "upload-project-001",
        "origin": "user",
        "items": [
            {"asset_id": "asset_video_001", "roles": ["source"]},
            {"asset_id": "asset_reference_001", "roles": ["reference"]},
        ],
    }


def test_upload_request_is_closed_versioned_and_ordered() -> None:
    request = UploadAssetsRequest.model_validate(_payload())

    assert request.request_version == "1.0"
    assert [item.asset_id for item in request.items] == [
        "asset_video_001",
        "asset_reference_001",
    ]

    invalid = _payload()
    invalid["files"] = ["not multipart data"]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        UploadAssetsRequest.model_validate(invalid)


def test_upload_request_rejects_duplicate_asset_ids_and_roles() -> None:
    duplicate_id = _payload()
    duplicate_id["items"][1]["asset_id"] = "asset_video_001"
    with pytest.raises(ValidationError, match="duplicate asset id"):
        UploadAssetsRequest.model_validate(duplicate_id)

    duplicate_role = _payload()
    duplicate_role["items"][0]["roles"] = ["source", "source"]
    with pytest.raises(ValidationError, match="duplicate asset role"):
        UploadAssetsRequest.model_validate(duplicate_role)


def test_upload_asset_id_must_fit_the_asset_table() -> None:
    invalid = _payload()
    invalid["items"][0]["asset_id"] = "a" * 65

    with pytest.raises(ValidationError, match="at most 64 characters"):
        UploadAssetsRequest.model_validate(invalid)


def test_processing_status_is_closed_and_does_not_expose_storage_keys() -> None:
    status = AssetProcessingStatus(
        asset_id="asset_video_001",
        project_id="project_upload_001",
        kind="video",
        original_name="clip.mp4",
        readiness="ready",
        processing_job_id="job_media_001",
        proxy_available=True,
        thumbnail_available=True,
    )

    assert status.readiness == "ready"
    assert "storage_key" not in status.model_dump()
    with pytest.raises(ValidationError, match="Input should be"):
        AssetProcessingStatus.model_validate(
            {**status.model_dump(), "readiness": "analyzed"}
        )
