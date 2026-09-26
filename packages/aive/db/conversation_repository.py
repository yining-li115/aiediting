"""SQLAlchemy repository for conversations and continuous-edit Agent turns."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..contracts.conversation import (
    AgentToolStep,
    AgentTurn,
    AgentTurnContext,
    AgentTurnTicket,
    Conversation,
    ConversationMessage,
    ConversationTicket,
    CreateAgentTurnRequest,
    CreateConversationRequest,
    WorkspaceSelection,
)
from ..contracts.patch import Patch
from .models import (
    AgentTurnRow,
    ConversationMessageRow,
    ConversationRow,
    Job,
    Project,
    utcnow,
)


class ConversationNotFound(LookupError):
    pass


class AgentTurnNotFound(LookupError):
    pass


class ConversationConflict(RuntimeError):
    pass


class SqlAlchemyConversationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(
        self,
        request: CreateConversationRequest,
        *,
        conversation_id: str,
    ) -> ConversationTicket:
        with self.session_factory.begin() as session:
            if session.get(Project, request.project_id) is None:
                raise ConversationNotFound(
                    f"project {request.project_id!r} does not exist"
                )
            existing = session.scalar(
                select(ConversationRow).where(
                    ConversationRow.project_id == request.project_id,
                    ConversationRow.purpose == request.purpose,
                )
            )
            if existing is not None:
                return ConversationTicket(
                    conversation=_conversation(session, existing),
                    replayed=True,
                )
            now = utcnow()
            row = ConversationRow(
                id=conversation_id,
                project_id=request.project_id,
                purpose=request.purpose,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return ConversationTicket(
                conversation=_conversation(session, row),
                replayed=False,
            )

    def get(self, project_id: str, conversation_id: str) -> Conversation | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ConversationRow).where(
                    ConversationRow.id == conversation_id,
                    ConversationRow.project_id == project_id,
                )
            )
            return _conversation(session, row) if row is not None else None

    def enqueue_turn(
        self,
        request: CreateAgentTurnRequest,
        *,
        turn_id: str,
        job_id: str,
        user_message_id: str,
        request_hash: str,
        provider: str,
        provider_version: str,
    ) -> AgentTurnTicket:
        with self.session_factory.begin() as session:
            replay = session.scalar(
                select(AgentTurnRow).where(
                    AgentTurnRow.project_id == request.project_id,
                    AgentTurnRow.idempotency_key == request.idempotency_key,
                )
            )
            if replay is not None:
                if replay.request_hash != request_hash:
                    raise ConversationConflict(
                        "Agent turn idempotency key was reused for another request"
                    )
                return AgentTurnTicket(
                    turn=_turn(session, replay),
                    user_message=_user_message(session, replay.id),
                    replayed=True,
                )
            conversation = session.scalar(
                select(ConversationRow).where(
                    ConversationRow.id == request.conversation_id,
                    ConversationRow.project_id == request.project_id,
                )
            )
            if conversation is None:
                raise ConversationNotFound(
                    f"conversation {request.conversation_id!r} does not exist"
                )
            if session.get(Job, job_id) is not None:
                raise ConversationConflict(f"job id {job_id!r} already exists")
            now = utcnow()
            session.add(
                Job(
                    id=job_id,
                    project_id=request.project_id,
                    kind="continuous_edit",
                    status="queued",
                    stage="planning",
                    progress=0.0,
                )
            )
            row = AgentTurnRow(
                id=turn_id,
                project_id=request.project_id,
                conversation_id=request.conversation_id,
                job_id=job_id,
                request_id=request.request_id,
                idempotency_key=request.idempotency_key,
                request_hash=request_hash,
                status="queued",
                version_before=request.base_revision_id,
                version_after=None,
                instruction=request.instruction,
                route="deep",
                resolved_scope_json=request.workspace.model_dump(mode="json"),
                model=provider,
                provider_version=provider_version,
                context_json=None,
                steps_json=[],
                patch_json=None,
                render_artifact_id=None,
                render_job_id=None,
                latency_ms=None,
                op_count=0,
                precheck_violations_json=[],
                outcome="pending",
                error=None,
                created_at=now,
                updated_at=now,
            )
            message = ConversationMessageRow(
                id=user_message_id,
                project_id=request.project_id,
                conversation_id=request.conversation_id,
                agent_turn_id=turn_id,
                role="user",
                content=request.instruction,
                base_revision_id=request.base_revision_id,
                result_revision_id=None,
                created_at=now,
            )
            session.add_all((row, message))
            conversation.updated_at = now
            session.flush()
            return AgentTurnTicket(
                turn=_turn(session, row),
                user_message=_message(session, message),
                replayed=False,
            )

    def get_turn(self, project_id: str, turn_id: str) -> AgentTurn | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AgentTurnRow).where(
                    AgentTurnRow.id == turn_id,
                    AgentTurnRow.project_id == project_id,
                )
            )
            return _turn(session, row) if row is not None else None

    def get_job_turn(self, job_id: str) -> AgentTurn:
        with self.session_factory() as session:
            return _turn(session, _job_turn(session, job_id))

    def history_before_turn(self, job_id: str) -> tuple[ConversationMessage, ...]:
        with self.session_factory() as session:
            turn = _job_turn(session, job_id)
            current_message = session.scalar(
                select(ConversationMessageRow).where(
                    ConversationMessageRow.agent_turn_id == turn.id,
                    ConversationMessageRow.role == "user",
                )
            )
            if current_message is None:
                raise ConversationConflict(
                    f"Agent turn {turn.id!r} has no user message"
                )
            rows = session.scalars(
                select(ConversationMessageRow)
                .where(
                    ConversationMessageRow.conversation_id == turn.conversation_id,
                    ConversationMessageRow.agent_turn_id != turn.id,
                    ConversationMessageRow.created_at < current_message.created_at,
                )
                .order_by(
                    ConversationMessageRow.created_at,
                    ConversationMessageRow.id,
                )
            ).all()
            return tuple(_message(session, row) for row in rows)

    def mark_running(self, job_id: str) -> AgentTurn:
        with self.session_factory.begin() as session:
            row = _job_turn(session, job_id)
            if row.status == "queued":
                row.status = "running"
                row.error = None
                row.updated_at = utcnow()
            elif row.status not in {"running", "completed"}:
                raise ConversationConflict(
                    f"Agent turn {row.id!r} cannot run from {row.status!r}"
                )
            session.flush()
            return _turn(session, row)

    def save_context(self, job_id: str, context: AgentTurnContext) -> AgentTurn:
        encoded = context.model_dump(mode="json")
        with self.session_factory.begin() as session:
            row = _job_turn(session, job_id)
            if row.context_json is not None and row.context_json != encoded:
                raise ConversationConflict(
                    f"Agent turn {row.id!r} already has another frozen context"
                )
            row.context_json = encoded
            row.updated_at = utcnow()
            session.flush()
            return _turn(session, row)

    def append_step(self, job_id: str, step: AgentToolStep) -> AgentTurn:
        encoded = step.model_dump(mode="json")
        with self.session_factory.begin() as session:
            row = _job_turn(session, job_id)
            values = list(row.steps_json or [])
            if step.step_index < len(values):
                if values[step.step_index] != encoded:
                    raise ConversationConflict(
                        f"Agent turn {row.id!r} already has another tool step"
                    )
                return _turn(session, row)
            if step.step_index != len(values):
                raise ConversationConflict("Agent tool steps must be appended in order")
            values.append(encoded)
            row.steps_json = values
            row.updated_at = utcnow()
            session.flush()
            return _turn(session, row)

    def complete(
        self,
        job_id: str,
        *,
        assistant_message_id: str,
        response: str,
        patch: Patch | None,
        result_revision_id: str | None,
        render_artifact_id: str | None,
        render_job_id: str | None,
    ) -> AgentTurn:
        with self.session_factory.begin() as session:
            row = _job_turn(session, job_id)
            if row.status == "completed":
                existing = _assistant_message(session, row.id)
                if (
                    existing is None
                    or existing.id != assistant_message_id
                    or existing.content != response
                    or existing.result_revision_id != result_revision_id
                ):
                    raise ConversationConflict(
                        f"Agent turn {row.id!r} already has another result"
                    )
                return _turn(session, row)
            if row.status not in {"queued", "running"}:
                raise ConversationConflict(
                    f"Agent turn {row.id!r} cannot complete from {row.status!r}"
                )
            if (patch is None) != (result_revision_id is None):
                raise ConversationConflict(
                    "Agent turn Patch and result revision must be stored together"
                )
            now = utcnow()
            session.add(
                ConversationMessageRow(
                    id=assistant_message_id,
                    project_id=row.project_id,
                    conversation_id=row.conversation_id,
                    agent_turn_id=row.id,
                    role="assistant",
                    content=response,
                    base_revision_id=row.version_before,
                    result_revision_id=result_revision_id,
                    created_at=now,
                )
            )
            row.status = "completed"
            row.version_after = result_revision_id
            row.patch_json = patch.model_dump(mode="json") if patch else None
            row.render_artifact_id = render_artifact_id
            row.render_job_id = render_job_id
            row.op_count = len(patch.operations) if patch else 0
            row.outcome = "applied" if patch else "no_change"
            row.error = None
            row.updated_at = now
            conversation = session.get(ConversationRow, row.conversation_id)
            if conversation is not None:
                conversation.updated_at = now
            session.flush()
            return _turn(session, row)

    def mark_failed(self, job_id: str, error: str) -> AgentTurn:
        with self.session_factory.begin() as session:
            row = _job_turn(session, job_id)
            if row.status == "completed":
                return _turn(session, row)
            row.status = "failed"
            row.outcome = "rejected"
            row.error = (error or "continuous edit failed")[:2_000]
            row.updated_at = utcnow()
            session.flush()
            return _turn(session, row)

    def incomplete(self) -> tuple[AgentTurn, ...]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AgentTurnRow)
                .where(AgentTurnRow.status.in_(("queued", "running")))
                .order_by(AgentTurnRow.created_at, AgentTurnRow.id)
            ).all()
            return tuple(_turn(session, row) for row in rows)


def _conversation(session: Session, row: ConversationRow) -> Conversation:
    messages = session.scalars(
        select(ConversationMessageRow)
        .where(ConversationMessageRow.conversation_id == row.id)
        .order_by(ConversationMessageRow.created_at, ConversationMessageRow.id)
    ).all()
    return Conversation(
        conversation_id=row.id,
        project_id=row.project_id,
        purpose=row.purpose,
        messages=tuple(_message(session, message) for message in messages),
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _message(session: Session, row: ConversationMessageRow) -> ConversationMessage:
    workspace = None
    if row.role == "user" and row.agent_turn_id is not None:
        turn = session.get(AgentTurnRow, row.agent_turn_id)
        if turn is not None and turn.resolved_scope_json is not None:
            workspace = WorkspaceSelection.model_validate(turn.resolved_scope_json)
    return ConversationMessage(
        message_id=row.id,
        project_id=row.project_id,
        conversation_id=row.conversation_id,
        role=row.role,
        content=row.content,
        agent_turn_id=row.agent_turn_id,
        base_revision_id=row.base_revision_id,
        result_revision_id=row.result_revision_id,
        workspace=workspace,
        created_at=_aware(row.created_at),
    )


def _turn(session: Session, row: AgentTurnRow) -> AgentTurn:
    if (
        row.conversation_id is None
        or row.job_id is None
        or row.request_id is None
        or row.idempotency_key is None
        or row.status is None
        or row.model is None
        or row.provider_version is None
        or row.version_before is None
    ):
        raise ConversationConflict(
            f"legacy Agent turn {row.id!r} cannot enter the continuous-edit path"
        )
    assistant = _assistant_message(session, row.id)
    return AgentTurn(
        turn_id=row.id,
        job_id=row.job_id,
        project_id=row.project_id,
        conversation_id=row.conversation_id,
        request_id=row.request_id,
        idempotency_key=row.idempotency_key,
        base_revision_id=row.version_before,
        instruction=row.instruction,
        workspace=WorkspaceSelection.model_validate(row.resolved_scope_json),
        status=row.status,
        provider=row.model,
        provider_version=row.provider_version,
        context=(
            AgentTurnContext.model_validate(row.context_json)
            if row.context_json is not None
            else None
        ),
        steps=tuple(
            AgentToolStep.model_validate(step) for step in (row.steps_json or [])
        ),
        patch=(Patch.model_validate(row.patch_json) if row.patch_json else None),
        result_revision_id=row.version_after,
        render_artifact_id=row.render_artifact_id,
        render_job_id=row.render_job_id,
        assistant_message_id=assistant.id if assistant is not None else None,
        error=row.error,
        created_at=_aware(row.created_at),
        updated_at=_aware(row.updated_at),
    )


def _job_turn(session: Session, job_id: str) -> AgentTurnRow:
    row = session.scalar(select(AgentTurnRow).where(AgentTurnRow.job_id == job_id))
    if row is None:
        raise AgentTurnNotFound(f"continuous-edit job {job_id!r} has no turn")
    return row


def _user_message(session: Session, turn_id: str) -> ConversationMessage:
    row = session.scalar(
        select(ConversationMessageRow).where(
            ConversationMessageRow.agent_turn_id == turn_id,
            ConversationMessageRow.role == "user",
        )
    )
    if row is None:
        raise ConversationConflict(f"Agent turn {turn_id!r} has no user message")
    return _message(session, row)


def _assistant_message(
    session: Session,
    turn_id: str,
) -> ConversationMessageRow | None:
    return session.scalar(
        select(ConversationMessageRow).where(
            ConversationMessageRow.agent_turn_id == turn_id,
            ConversationMessageRow.role == "assistant",
        )
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


__all__ = [
    "AgentTurnNotFound",
    "ConversationConflict",
    "ConversationNotFound",
    "SqlAlchemyConversationRepository",
]
