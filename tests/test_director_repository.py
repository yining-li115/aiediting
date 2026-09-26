from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.contracts.director import CreateFirstCutRequest
from aive.contracts.project_doc import ProjectDoc
from aive.db.director_repository import SqlAlchemyDirectorRunRepository
from aive.db.models import Base, Project, ProjectRevisionRow
from aive.agent.repository import (
    DirectorIdempotencyConflict,
    DirectorRevisionConflict,
)
from aive.project.hashing import canonical_model_hash


NOW = datetime(2026, 9, 26, 23, 45, tzinfo=timezone.utc)


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'director.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    source = Path(__file__).parents[1] / "examples" / "project_doc_v1.json"
    document = ProjectDoc.model_validate_json(source.read_text(encoding="utf-8"))
    with sessions.begin() as session:
        session.add(
            Project(
                id=document.project_id,
                status="created",
                current_revision_id=None,
            )
        )
        session.add(
            ProjectRevisionRow(
                id=document.revision_id,
                project_id=document.project_id,
                parent_revision_id=None,
                commit_id="commit_initial",
                document_json=document.model_dump(mode="json"),
                document_hash=canonical_model_hash(document),
                provenance_json={"kind": "initial", "origin": "system"},
                created_at=NOW,
            )
        )
    with sessions.begin() as session:
        session.get(Project, document.project_id).current_revision_id = document.revision_id
    return SqlAlchemyDirectorRunRepository(sessions), document


def _request(document: ProjectDoc, **changes) -> CreateFirstCutRequest:
    values = {
        "request_id": "request_first_cut",
        "project_id": document.project_id,
        "base_revision_id": document.revision_id,
        "idempotency_key": "first-cut-key",
    }
    values.update(changes)
    return CreateFirstCutRequest(**values)


def test_enqueue_is_atomic_and_idempotent(repository) -> None:
    repo, document = repository
    request = _request(document)

    first = repo.enqueue(
        request,
        run_id="run_first",
        job_id="job_first",
        request_hash="a" * 64,
        provider="offline-director",
        provider_version="1",
    )
    replay = repo.enqueue(
        request,
        run_id="run_unused",
        job_id="job_unused",
        request_hash="a" * 64,
        provider="offline-director",
        provider_version="1",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.run.run_id == "run_first"
    assert repo.get_job_run("job_first") == first.run


def test_idempotency_key_cannot_change_the_request(repository) -> None:
    repo, document = repository
    repo.enqueue(
        _request(document),
        run_id="run_first",
        job_id="job_first",
        request_hash="a" * 64,
        provider="offline-director",
        provider_version="1",
    )

    with pytest.raises(DirectorIdempotencyConflict):
        repo.enqueue(
            _request(document, request_id="request_changed"),
            run_id="run_second",
            job_id="job_second",
            request_hash="b" * 64,
            provider="offline-director",
            provider_version="1",
        )


def test_enqueue_rejects_a_non_current_base_revision(repository) -> None:
    repo, document = repository
    stale = document.model_copy(
        update={
            "revision_id": "revision_stale",
            "parent_revision_id": document.revision_id,
        }
    )
    with repo.session_factory.begin() as session:
        session.add(
            ProjectRevisionRow(
                id=stale.revision_id,
                project_id=stale.project_id,
                parent_revision_id=stale.parent_revision_id,
                commit_id="commit_stale",
                document_json=stale.model_dump(mode="json"),
                document_hash=canonical_model_hash(stale),
                provenance_json={"kind": "patch", "origin": "user", "patch_id": "p"},
                created_at=NOW,
            )
        )

    with pytest.raises(DirectorRevisionConflict, match="current revision"):
        repo.enqueue(
            _request(document, base_revision_id="revision_stale"),
            run_id="run_stale",
            job_id="job_stale",
            request_hash="a" * 64,
            provider="offline-director",
            provider_version="1",
        )
