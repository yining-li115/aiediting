from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.routes.project_revisions import get_project_revision_application
from aive.contracts.project_doc import ProjectDoc
from aive.contracts.revision import ProjectRevision, RevisionProvenance
from aive.db.models import Base, Project
from aive.db.project_repository import SqlAlchemyRevisionRepository
from aive.project.application import ProjectRevisionApplication
from aive.project.commit_state import begin_commit, transition_commit
from aive.project.hashing import canonical_model_hash
from aive.project.service import CandidateResult, CapabilityUse


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


class AcceptCapabilities:
    def rejection_reason(self, capability: CapabilityUse) -> str | None:
        return None


def _document() -> ProjectDoc:
    return ProjectDoc.model_validate(
        {
            "project_id": "project_api",
            "revision_id": "revision_initial",
            "intent": {
                "task_mode": "autonomous",
                "target_duration_frames": 120,
                "prompt": "Original prompt",
            },
            "timeline": {
                "duration_frames": 120,
                "output": {
                    "width": 1080,
                    "height": 1920,
                    "fps": {"num": 30, "den": 1},
                },
                "tracks": [],
            },
            "dependencies": {
                "renderer": {"name": "ffmpeg", "version": "dev"},
            },
        }
    )


def _initialize(repository: SqlAlchemyRevisionRepository) -> None:
    document = _document()
    revision = ProjectRevision(
        commit_id="commit_initial",
        created_at=NOW,
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(
            kind="initial",
            origin="system",
            request_id="request_initial",
        ),
        document=document,
    )
    attempt = begin_commit(
        commit_id="commit_initial",
        project_id="project_api",
        request_kind="initialize",
        request_id="request_initial",
        base_revision_id=None,
        idempotency_key="initialize-project-api",
        request_hash="1" * 64,
    )
    attempt = transition_commit(
        attempt,
        "candidate_created",
        candidate_revision_id="revision_initial",
    )
    attempt = transition_commit(attempt, "validated")
    repository.initialize_project(CandidateResult(revision=revision, attempt=attempt))


@pytest.fixture
def revision_api(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with sessions.begin() as session:
        session.add(Project(id="project_api", status="created"))
    repository = SqlAlchemyRevisionRepository(sessions)
    _initialize(repository)
    ids = iter(
        [
            "commit_api_1",
            "revision_api_1",
            "commit_api_2",
            "revision_api_2",
            "commit_api_3",
            "revision_api_3",
        ]
    )
    application = ProjectRevisionApplication(
        repository,
        AcceptCapabilities(),
        id_factory=lambda _: next(ids),
        clock=lambda: NOW + timedelta(minutes=1),
    )

    from apps.api.main import app

    app.dependency_overrides[get_project_revision_application] = lambda: application
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _patch(base_revision_id: str, *, suffix: str, prompt: str) -> dict:
    return {
        "patch_id": f"patch_{suffix}",
        "project_id": "project_api",
        "base_revision_id": base_revision_id,
        "idempotency_key": f"patch-key-{suffix}",
        "origin": "user",
        "request_id": f"request_{suffix}",
        "summary": f"Set prompt to {prompt}",
        "scope": {"kind": "global"},
        "operations": [
            {
                "op": "set_intent",
                "operation_id": f"operation_{suffix}",
                "intent": {
                    "task_mode": "autonomous",
                    "target_duration_frames": 120,
                    "prompt": prompt,
                },
            }
        ],
    }


@pytest.fixture
def empty_revision_api(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'create-api.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    repository = SqlAlchemyRevisionRepository(sessions)
    ids = iter(
        [
            "commit_created_api",
            "revision_created_api",
            "commit_collision_api",
            "revision_collision_api",
        ]
    )
    application = ProjectRevisionApplication(
        repository,
        AcceptCapabilities(),
        id_factory=lambda _: next(ids),
        clock=lambda: NOW,
    )

    from apps.api.main import app

    app.dependency_overrides[get_project_revision_application] = lambda: application
    with TestClient(app) as client:
        yield client, repository
    app.dependency_overrides.clear()


def _create_request() -> dict:
    return {
        "request_id": "request_create_api",
        "project_id": "project_created_api",
        "idempotency_key": "create-project-api",
        "origin": "user",
        "intent": {
            "task_mode": "autonomous",
            "target_duration_frames": 1_800,
            "prompt": "Create from the browser",
            "style_tags": ["cinematic"],
        },
        "output": {
            "width": 1_080,
            "height": 1_920,
            "fps": {"num": 30, "den": 1},
        },
    }


def test_http_create_project_initializes_revision_and_is_replay_safe(
    empty_revision_api,
) -> None:
    client, repository = empty_revision_api
    request = _create_request()

    created = client.post("/projects", json=request)
    replay = client.post("/projects", json=request)

    assert created.status_code == 201
    assert created.json()["attempt"]["state"] == "committed"
    assert created.json()["revision"]["document"]["revision_id"] == "revision_created_api"
    assert replay.status_code == 201
    assert replay.json()["attempt"]["state"] == "duplicate"
    assert replay.json()["replayed"] is True
    assert repository.get_current_revision("project_created_api") == ProjectRevision.model_validate(
        created.json()["revision"]
    )
    assert len(repository.list_revisions("project_created_api")) == 1

    collision = _create_request()
    collision["request_id"] = "request_create_collision"
    collision["idempotency_key"] = "create-project-collision"
    conflict = client.post("/projects", json=collision)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "project_already_exists"


def test_http_patch_history_and_restore_flow(revision_api: TestClient) -> None:
    current = revision_api.get("/projects/project_api/revisions/current")
    assert current.status_code == 200
    assert current.json()["document"]["revision_id"] == "revision_initial"

    edited = revision_api.post(
        "/projects/project_api/patches",
        json=_patch("revision_initial", suffix="edit", prompt="Edited prompt"),
    )
    assert edited.status_code == 200
    edited_body = edited.json()
    assert edited_body["attempt"]["state"] == "committed"
    edited_revision_id = edited_body["revision"]["document"]["revision_id"]

    history = revision_api.get("/projects/project_api/revisions")
    assert history.status_code == 200
    assert [item["document"]["revision_id"] for item in history.json()] == [
        "revision_initial",
        edited_revision_id,
    ]

    restored = revision_api.post(
        "/projects/project_api/restores",
        json={
            "request_id": "request_restore",
            "project_id": "project_api",
            "base_revision_id": edited_revision_id,
            "source_revision_id": "revision_initial",
            "idempotency_key": "restore-key-1",
            "origin": "user",
            "summary": "Restore the original prompt",
        },
    )
    assert restored.status_code == 200
    restored_body = restored.json()
    assert restored_body["attempt"]["state"] == "committed"
    assert restored_body["revision"]["document"]["intent"]["prompt"] == "Original prompt"
    assert restored_body["revision"]["provenance"]["restored_from_revision_id"] == "revision_initial"
    assert len(revision_api.get("/projects/project_api/revisions").json()) == 3


def test_review_candidate_is_hidden_from_history_until_accepted(
    revision_api: TestClient,
) -> None:
    candidate = revision_api.post(
        "/projects/project_api/patches?require_review=true",
        json=_patch("revision_initial", suffix="review", prompt="Review prompt"),
    )
    assert candidate.status_code == 200
    candidate_body = candidate.json()
    assert candidate_body["attempt"]["state"] == "awaiting_review"

    history_before = revision_api.get("/projects/project_api/revisions").json()
    assert len(history_before) == 1

    accepted = revision_api.post(
        f"/projects/project_api/commits/{candidate_body['attempt']['commit_id']}/accept"
    )
    assert accepted.status_code == 200
    assert accepted.json()["attempt"]["state"] == "committed"
    assert len(revision_api.get("/projects/project_api/revisions").json()) == 2


def test_stale_patch_returns_typed_conflict(revision_api: TestClient) -> None:
    first = revision_api.post(
        "/projects/project_api/patches",
        json=_patch("revision_initial", suffix="winner", prompt="Winner"),
    )
    assert first.status_code == 200

    stale = revision_api.post(
        "/projects/project_api/patches",
        json=_patch("revision_initial", suffix="stale", prompt="Stale"),
    )

    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_base_revision"
    current = revision_api.get("/projects/project_api/revisions/current").json()
    assert current["document"]["intent"]["prompt"] == "Winner"


def test_http_retry_returns_the_original_revision(revision_api: TestClient) -> None:
    body = _patch("revision_initial", suffix="retry", prompt="Exactly once")

    first = revision_api.post("/projects/project_api/patches", json=body)
    replay = revision_api.post("/projects/project_api/patches", json=body)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["attempt"]["state"] == "duplicate"
    assert (
        replay.json()["revision"]["document"]["revision_id"]
        == first.json()["revision"]["document"]["revision_id"]
    )
    assert len(revision_api.get("/projects/project_api/revisions").json()) == 2


def test_http_rejects_idempotency_key_reuse_for_different_patch(
    revision_api: TestClient,
) -> None:
    first = _patch("revision_initial", suffix="same-key", prompt="First")
    reused = _patch("revision_initial", suffix="different", prompt="Different")
    reused["idempotency_key"] = first["idempotency_key"]

    assert revision_api.post("/projects/project_api/patches", json=first).status_code == 200
    response = revision_api.post("/projects/project_api/patches", json=reused)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "idempotency_conflict"


def test_project_path_and_body_must_match(revision_api: TestClient) -> None:
    patch = _patch("revision_initial", suffix="wrong-project", prompt="No")
    patch["project_id"] = "project_other"

    response = revision_api.post("/projects/project_api/patches", json=patch)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "project_path_mismatch"
