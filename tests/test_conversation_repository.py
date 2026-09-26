from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aive.contracts.conversation import (
    CreateAgentTurnRequest,
    CreateConversationRequest,
    WorkspaceSelection,
)
from aive.contracts.project_doc import ProjectDoc
from aive.db.conversation_repository import (
    ConversationConflict,
    SqlAlchemyConversationRepository,
)
from aive.db.models import Base, Project, ProjectRevisionRow
from aive.project.hashing import canonical_model_hash


NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'conversation.db'}", future=True)
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
        session.get(Project, document.project_id).current_revision_id = (
            document.revision_id
        )
    return SqlAlchemyConversationRepository(sessions), document


def _conversation_request(document: ProjectDoc) -> CreateConversationRequest:
    return CreateConversationRequest(
        request_id="request_conversation",
        project_id=document.project_id,
    )


def _turn_request(document: ProjectDoc, **changes) -> CreateAgentTurnRequest:
    values = {
        "request_id": "request_turn",
        "project_id": document.project_id,
        "conversation_id": "conversation_primary",
        "base_revision_id": document.revision_id,
        "idempotency_key": "turn_key",
        "instruction": "把声音调低一点",
        "workspace": WorkspaceSelection(revision_id=document.revision_id),
    }
    values.update(changes)
    return CreateAgentTurnRequest(**values)


def test_primary_conversation_creation_is_replay_safe(repository) -> None:
    repo, document = repository

    first = repo.create(
        _conversation_request(document),
        conversation_id="conversation_primary",
    )
    replay = repo.create(
        _conversation_request(document).model_copy(
            update={"request_id": "request_other"}
        ),
        conversation_id="conversation_unused",
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.conversation.conversation_id == "conversation_primary"


def test_turn_enqueue_messages_and_completion_are_durable(repository) -> None:
    repo, document = repository
    repo.create(
        _conversation_request(document),
        conversation_id="conversation_primary",
    )
    request = _turn_request(document)

    ticket = repo.enqueue_turn(
        request,
        turn_id="turn_1",
        job_id="job_turn_1",
        user_message_id="message_user_1",
        request_hash="a" * 64,
        provider="fixture-director",
        provider_version="1",
    )
    replay = repo.enqueue_turn(
        request,
        turn_id="turn_unused",
        job_id="job_unused",
        user_message_id="message_unused",
        request_hash="a" * 64,
        provider="fixture-director",
        provider_version="1",
    )
    completed = repo.complete(
        ticket.turn.job_id,
        assistant_message_id="message_assistant_1",
        response="当前没有需要修改的内容。",
        patch=None,
        result_revision_id=None,
        render_artifact_id=None,
        render_job_id=None,
    )
    conversation = repo.get(document.project_id, "conversation_primary")
    second = repo.enqueue_turn(
        _turn_request(
            document,
            request_id="request_turn_2",
            idempotency_key="turn_key_2",
            instruction="查找开场镜头",
        ),
        turn_id="turn_2",
        job_id="job_turn_2",
        user_message_id="message_user_2",
        request_hash="b" * 64,
        provider="fixture-director",
        provider_version="1",
    )
    prior_history = repo.history_before_turn(second.turn.job_id)

    assert replay.replayed is True
    assert replay.turn.turn_id == ticket.turn.turn_id
    assert completed.status == "completed"
    assert conversation is not None
    assert [message.role for message in conversation.messages] == [
        "user",
        "assistant",
    ]
    assert conversation.messages[-1].content == "当前没有需要修改的内容。"
    assert [message.message_id for message in prior_history] == [
        "message_user_1",
        "message_assistant_1",
    ]


def test_turn_idempotency_key_cannot_change_instruction(repository) -> None:
    repo, document = repository
    repo.create(
        _conversation_request(document),
        conversation_id="conversation_primary",
    )
    repo.enqueue_turn(
        _turn_request(document),
        turn_id="turn_1",
        job_id="job_turn_1",
        user_message_id="message_user_1",
        request_hash="a" * 64,
        provider="fixture-director",
        provider_version="1",
    )

    with pytest.raises(ConversationConflict, match="idempotency"):
        repo.enqueue_turn(
            _turn_request(document, instruction="另一条指令"),
            turn_id="turn_2",
            job_id="job_turn_2",
            user_message_id="message_user_2",
            request_hash="b" * 64,
            provider="fixture-director",
            provider_version="1",
        )
