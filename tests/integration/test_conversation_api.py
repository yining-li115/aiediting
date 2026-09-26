from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.api.routes.conversations import (
    get_conversation_application,
    get_conversation_workflow,
)
from tests.unit.test_conversation_application import _stack


@pytest.fixture
def conversation_api(tmp_path):
    application, workflow, projects, _, document = _stack(tmp_path)

    from apps.api.main import app

    app.dependency_overrides[get_conversation_application] = lambda: application
    app.dependency_overrides[get_conversation_workflow] = lambda: workflow
    with TestClient(app) as client:
        yield client, projects, document
    app.dependency_overrides.clear()


def test_http_conversation_turn_commits_edit_and_returns_agent_explanation(
    conversation_api,
) -> None:
    client, projects, document = conversation_api
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
    assert "ProjectService" in messages[-1]["content"]
    assert messages[-1]["result_revision_id"] == turn["result_revision_id"]

    current = projects.get_current_revision(document.project_id)
    audio = next(
        track for track in current.document.timeline.tracks if track.kind == "audio"
    )
    assert audio.clips[0].gain_db == -6


def test_http_turn_rejects_a_conversation_path_mismatch(conversation_api) -> None:
    client, _, document = conversation_api
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
