"""Persistence port for conversations and continuous-edit Agent turns."""

from __future__ import annotations

from typing import Protocol

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
)
from ..contracts.patch import Patch


class ConversationRepository(Protocol):
    def create(
        self,
        request: CreateConversationRequest,
        *,
        conversation_id: str,
    ) -> ConversationTicket: ...

    def get(self, project_id: str, conversation_id: str) -> Conversation | None: ...

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
    ) -> AgentTurnTicket: ...

    def get_turn(self, project_id: str, turn_id: str) -> AgentTurn | None: ...

    def get_job_turn(self, job_id: str) -> AgentTurn: ...

    def history_before_turn(self, job_id: str) -> tuple[ConversationMessage, ...]: ...

    def mark_running(self, job_id: str) -> AgentTurn: ...

    def save_context(self, job_id: str, context: AgentTurnContext) -> AgentTurn: ...

    def append_step(self, job_id: str, step: AgentToolStep) -> AgentTurn: ...

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
    ) -> AgentTurn: ...

    def mark_failed(self, job_id: str, error: str) -> AgentTurn: ...

    def incomplete(self) -> tuple[AgentTurn, ...]: ...


__all__ = ["ConversationRepository"]
