from __future__ import annotations

import pytest
from pydantic import ValidationError

from aive.contracts.project import CreateProjectRequest


def _payload() -> dict:
    return {
        "request_id": "request_create_001",
        "project_id": "project_create_001",
        "idempotency_key": "create-project-001",
        "origin": "user",
        "intent": {
            "task_mode": "autonomous",
            "target_duration_frames": 1_800,
            "prompt": "Create a cinematic launch film",
            "style_tags": ["cinematic"],
        },
        "output": {
            "width": 1_080,
            "height": 1_920,
            "fps": {"num": 30, "den": 1},
        },
    }


def test_create_project_request_is_a_closed_versioned_contract() -> None:
    request = CreateProjectRequest.model_validate(_payload())

    assert request.request_version == "1.0"
    assert request.intent.target_duration_frames == 1_800
    assert request.output.fps.num == 30

    invalid = _payload()
    invalid["untrusted_project_state"] = {}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CreateProjectRequest.model_validate(invalid)


def test_create_project_accepts_a_long_form_target_duration() -> None:
    long_form = _payload()
    long_form["intent"]["target_duration_frames"] = 30 * 60 * 20

    request = CreateProjectRequest.model_validate(long_form)

    assert request.intent.target_duration_frames == 36_000


def test_create_project_rejects_an_id_that_cannot_fit_the_project_table() -> None:
    invalid = _payload()
    invalid["project_id"] = "p" * 65

    with pytest.raises(ValidationError, match="at most 64 characters"):
        CreateProjectRequest.model_validate(invalid)
