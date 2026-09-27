from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.routes.conversations import (
    get_conversation_application,
    get_conversation_workflow,
)
from aive.contracts.rendering import CreateRenderRequest
from tests.unit.test_conversation_application import (
    ScriptedEditProvider,
    _lower_first_audio_proposal,
    _stack,
)


@pytest.fixture
def conversation_api(tmp_path):
    application, workflow, projects, _, document = _stack(
        tmp_path,
        provider=ScriptedEditProvider(_lower_first_audio_proposal()),
    )

    from apps.api.main import app

    app.dependency_overrides[get_conversation_application] = lambda: application
    app.dependency_overrides[get_conversation_workflow] = lambda: workflow
    with TestClient(app) as client:
        yield client, projects, document, application
    app.dependency_overrides.clear()


def test_http_conversation_turn_commits_edit_and_returns_agent_explanation(
    conversation_api,
) -> None:
    client, projects, document, _ = conversation_api
    created = client.post(
        f"/projects/{document.project_id}/conversations",
        json={
            "request_id": "request_conversation_api",
            "project_id": document.project_id,
            "purpose": "primary",
        },
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation"]["conversation_id"]

    accepted = client.post(
        f"/projects/{document.project_id}/conversations/{conversation_id}/turns",
        json={
            "request_id": "request_agent_turn_api",
            "project_id": document.project_id,
            "conversation_id": conversation_id,
            "base_revision_id": document.revision_id,
            "idempotency_key": "agent-turn-api-key",
            "instruction": "把声音调低一点",
            "workspace": {
                "revision_id": document.revision_id,
                "playhead_frame": 15,
            },
        },
    )
    assert accepted.status_code == 200
    turn_id = accepted.json()["turn"]["turn_id"]

    turn_response = client.get(
        f"/projects/{document.project_id}/conversations/{conversation_id}/turns/{turn_id}"
    )
    assert turn_response.status_code == 200
    turn = turn_response.json()
    assert turn["status"] == "completed"
    assert turn["context"]["workspace"]["playhead_frame"] == 15
    assert [step["call"]["tool"] for step in turn["steps"]] == [
        "read_project",
        "propose_patch",
    ]
    assert turn["result_revision_id"] != document.revision_id

    conversation_response = client.get(
        f"/projects/{document.project_id}/conversations/{conversation_id}"
    )
    assert conversation_response.status_code == 200
    messages = conversation_response.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert "ProjectService" not in messages[-1]["content"]
    assert turn["result_revision_id"] not in messages[-1]["content"]
    assert "调整音量" in messages[-1]["content"]
    assert messages[-1]["result_revision_id"] == turn["result_revision_id"]

    current = projects.get_current_revision(document.project_id)
    audio = next(
        track for track in current.document.timeline.tracks if track.kind == "audio"
    )
    assert audio.clips[0].gain_db == -6


def test_http_turn_rejects_a_conversation_path_mismatch(conversation_api) -> None:
    client, _, document, _ = conversation_api
    response = client.post(
        f"/projects/{document.project_id}/conversations/conversation_path/turns",
        json={
            "request_id": "request_wrong_path",
            "project_id": document.project_id,
            "conversation_id": "conversation_body",
            "base_revision_id": document.revision_id,
            "idempotency_key": "wrong-path-key",
            "instruction": "把声音调低一点",
            "workspace": {"revision_id": document.revision_id},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "conversation_path_mismatch"


def test_http_turn_round_trips_a_durable_preview_annotation(
    conversation_api,
) -> None:
    client, _, document, application = conversation_api
    render = application._rendering.enqueue(
        CreateRenderRequest(
            request_id="request_annotation_render_api",
            project_id=document.project_id,
            revision_id=document.revision_id,
            idempotency_key="annotation-render-api",
        )
    ).artifact
    artifacts = application._rendering._artifacts
    artifacts.mark_rendering(render.job_id)
    artifacts.mark_ready(
        render.job_id,
        storage_key="project_continuous/render/annotation-api.mp4",
        content_type="video/mp4",
        duration_seconds=2,
        file_size=10,
        sha256="d" * 64,
        render_time_ms=5,
    )
    created = client.post(
        f"/projects/{document.project_id}/conversations",
        json={
            "request_id": "request_annotation_conversation_api",
            "project_id": document.project_id,
            "purpose": "primary",
        },
    )
    conversation_id = created.json()["conversation"]["conversation_id"]
    annotation = {
        "annotation_id": "annotation_api",
        "revision_id": document.revision_id,
        "artifact_id": render.artifact_id,
        "output_range": {"start_frame": 8, "duration_frames": 5},
        "comment": "这里声音太大，降低一些。",
    }

    accepted = client.post(
        f"/projects/{document.project_id}/conversations/{conversation_id}/turns",
        json={
            "request_id": "request_annotation_turn_api",
            "project_id": document.project_id,
            "conversation_id": conversation_id,
            "base_revision_id": document.revision_id,
            "idempotency_key": "annotation-turn-api",
            "instruction": "按我的预览标记调整音量。",
            "workspace": {
                "revision_id": document.revision_id,
                "preview_annotations": [annotation],
            },
        },
    )

    assert accepted.status_code == 200
    assert accepted.json()["turn"]["workspace"]["preview_annotations"] == [
        {**annotation, "spatial_region": None}
    ]
    conversation = client.get(
        f"/projects/{document.project_id}/conversations/{conversation_id}"
    ).json()
    assert conversation["messages"][0]["workspace"]["preview_annotations"] == [
        {**annotation, "spatial_region": None}
    ]
