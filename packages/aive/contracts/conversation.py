"""Contracts for project conversations and bounded continuous-edit Agent turns."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .media import DatabaseId
from .moments import MomentEvidence
from .patch import Patch, PatchScope
from .patch_project import SetCreativePlan, SetTimelineDuration
from .patch_timeline import (
    DeleteAudioClip,
    DeleteVisualClip,
    ReplaceAudioSource,
    ReplaceVisualSource,
    SetAudioFades,
    SetAudioGain,
    SetAudioOutput,
    SetAudioPlayback,
    SetVideoPlayback,
    SetVisualCrop,
    SetVisualOutput,
)
from .project_doc import ProjectDoc
from .time import FrameRange


ConversationPurpose = Literal["primary"]
ConversationRole = Literal["user", "assistant"]
AgentTurnStatus = Literal["queued", "running", "completed", "failed"]
AgentToolName = Literal[
    "read_project",
    "search_moments",
    "inspect_media",
    "propose_patch",
]


class CreateConversationRequest(ContractModel):
    request_version: Literal["1.0"] = "1.0"
    request_id: StableId
    project_id: DatabaseId
    purpose: ConversationPurpose = "primary"


class WorkspaceSelection(ContractModel):
    """Transient right-side workspace state supplied with one user turn."""

    revision_id: StableId
    selected_target_ids: tuple[StableId, ...] = ()
    playhead_frame: int | None = Field(default=None, ge=0)
    visible_range: FrameRange | None = None
    comparison_revision_id: StableId | None = None
    preview_frame: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def target_ids_are_unique(self) -> WorkspaceSelection:
        if len(self.selected_target_ids) != len(set(self.selected_target_ids)):
            raise ValueError("workspace selection contains duplicate target IDs")
        return self


class CreateAgentTurnRequest(ContractModel):
    request_version: Literal["1.0"] = "1.0"
    request_id: StableId
    project_id: DatabaseId
    conversation_id: StableId
    base_revision_id: StableId
    idempotency_key: StableId
    instruction: str = Field(min_length=1, max_length=8_000)
    workspace: WorkspaceSelection

    @model_validator(mode="after")
    def workspace_targets_base_revision(self) -> CreateAgentTurnRequest:
        if self.workspace.revision_id != self.base_revision_id:
            raise ValueError("workspace revision must equal the turn base revision")
        return self


class ConversationMessage(ContractModel):
    message_id: StableId
    project_id: DatabaseId
    conversation_id: StableId
    role: ConversationRole
    content: str = Field(min_length=1, max_length=8_000)
    agent_turn_id: StableId | None = None
    base_revision_id: StableId | None = None
    result_revision_id: StableId | None = None
    created_at: datetime


class Conversation(ContractModel):
    conversation_id: StableId
    project_id: DatabaseId
    purpose: ConversationPurpose
    messages: tuple[ConversationMessage, ...] = ()
    created_at: datetime
    updated_at: datetime


class ConversationTicket(ContractModel):
    conversation: Conversation
    replayed: bool = False


class AgentTurnContext(ContractModel):
    """Frozen ProjectDoc, evidence, and UI context for one editing turn."""

    context_version: Literal["1.0"] = "1.0"
    turn_id: StableId
    project_id: DatabaseId
    conversation_id: StableId
    base_revision_id: StableId
    document_hash: str = Field(
        min_length=16,
        max_length=128,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    instruction: str = Field(min_length=1, max_length=8_000)
    document: ProjectDoc
    moments: tuple[MomentEvidence, ...] = ()
    conversation_history: tuple[ConversationMessage, ...] = Field(
        default=(),
        max_length=20,
    )
    history_truncated: bool = False
    workspace: WorkspaceSelection
    allowed_tools: tuple[AgentToolName, ...]
    max_tool_steps: int = Field(ge=1, le=12)

    @model_validator(mode="after")
    def context_is_exactly_bound(self) -> AgentTurnContext:
        if self.document.project_id != self.project_id:
            raise ValueError("Agent turn document belongs to another project")
        if self.document.revision_id != self.base_revision_id:
            raise ValueError("Agent turn document is not the base revision")
        if self.workspace.revision_id != self.base_revision_id:
            raise ValueError("Agent turn workspace is not bound to the base revision")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("Agent turn contains duplicate allowed tools")
        for moment in self.moments:
            if moment.project_id != self.project_id:
                raise ValueError("Agent turn contains a cross-project Moment")
        for message in self.conversation_history:
            if (
                message.project_id != self.project_id
                or message.conversation_id != self.conversation_id
            ):
                raise ValueError("Agent turn contains cross-conversation history")
        return self


class MomentSearchQuery(ContractModel):
    text: str | None = Field(default=None, max_length=2_000)
    semantic_roles: tuple[str, ...] = ()
    require_speech: bool | None = None
    usable_only: bool = True
    minimum_quality: float | None = Field(default=None, ge=0, le=1)
    limit: int = Field(default=20, ge=1, le=50)


class MomentSearchHit(ContractModel):
    moment_id: StableId
    score: float = Field(ge=0)
    reasons: tuple[str, ...] = ()


ContinuousEditOperation = Annotated[
    SetCreativePlan
    | SetTimelineDuration
    | DeleteVisualClip
    | ReplaceVisualSource
    | SetVisualOutput
    | SetVideoPlayback
    | SetVisualCrop
    | DeleteAudioClip
    | ReplaceAudioSource
    | SetAudioOutput
    | SetAudioPlayback
    | SetAudioGain
    | SetAudioFades,
    Field(discriminator="op"),
]


class ReadProjectCall(ContractModel):
    tool: Literal["read_project"] = "read_project"
    call_id: StableId


class SearchMomentsCall(ContractModel):
    tool: Literal["search_moments"] = "search_moments"
    call_id: StableId
    query: MomentSearchQuery


class InspectMediaCall(ContractModel):
    tool: Literal["inspect_media"] = "inspect_media"
    call_id: StableId
    moment_ids: tuple[StableId, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def moment_ids_are_unique(self) -> InspectMediaCall:
        if len(self.moment_ids) != len(set(self.moment_ids)):
            raise ValueError("inspect_media contains duplicate Moment IDs")
        return self


class PatchProposal(ContractModel):
    summary: str = Field(min_length=1, max_length=1_000)
    rationale: str = Field(min_length=1, max_length=2_000)
    scope: PatchScope
    operations: tuple[ContinuousEditOperation, ...] = Field(
        min_length=1,
        max_length=24,
    )

    @model_validator(mode="after")
    def operation_ids_are_unique(self) -> PatchProposal:
        operation_ids = [operation.operation_id for operation in self.operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("Patch proposal contains duplicate operation IDs")
        return self


class ProposePatchCall(ContractModel):
    tool: Literal["propose_patch"] = "propose_patch"
    call_id: StableId
    proposal: PatchProposal


AgentToolCall = Annotated[
    ReadProjectCall | SearchMomentsCall | InspectMediaCall | ProposePatchCall,
    Field(discriminator="tool"),
]


class AgentToolResult(ContractModel):
    """Closed result envelope; exactly one tool-specific payload is populated."""

    call_id: StableId
    tool: AgentToolName
    document: ProjectDoc | None = None
    hits: tuple[MomentSearchHit, ...] | None = None
    moments: tuple[MomentEvidence, ...] | None = None
    proposal: PatchProposal | None = None
    error: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def payload_matches_tool(self) -> AgentToolResult:
        values = {
            "read_project": self.document,
            "search_moments": self.hits,
            "inspect_media": self.moments,
            "propose_patch": self.proposal,
        }
        if self.error is not None:
            if any(value is not None for value in values.values()):
                raise ValueError("failed tool result cannot contain a payload")
            return self
        if values[self.tool] is None:
            raise ValueError(f"{self.tool} result is missing its payload")
        if any(value is not None for name, value in values.items() if name != self.tool):
            raise ValueError("tool result contains a payload for another tool")
        return self


class AgentToolStep(ContractModel):
    step_index: int = Field(ge=0, le=11)
    call: AgentToolCall
    result: AgentToolResult

    @model_validator(mode="after")
    def result_matches_call(self) -> AgentToolStep:
        if self.call.call_id != self.result.call_id or self.call.tool != self.result.tool:
            raise ValueError("tool result does not match its call")
        return self


class AgentStepDecision(ContractModel):
    """One provider decision in the bounded tool loop."""

    kind: Literal["tool_call", "finish"]
    call: AgentToolCall | None = None
    response: str | None = Field(default=None, min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def decision_has_one_action(self) -> AgentStepDecision:
        if self.kind == "tool_call" and (self.call is None or self.response is not None):
            raise ValueError("tool_call decision requires only a call")
        if self.kind == "finish" and (self.response is None or self.call is not None):
            raise ValueError("finish decision requires only a response")
        return self


class AgentTurn(ContractModel):
    turn_id: StableId
    job_id: StableId
    project_id: DatabaseId
    conversation_id: StableId
    request_id: StableId
    idempotency_key: StableId
    base_revision_id: StableId
    instruction: str = Field(min_length=1, max_length=8_000)
    workspace: WorkspaceSelection
    status: AgentTurnStatus
    provider: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=128)
    context: AgentTurnContext | None = None
    steps: tuple[AgentToolStep, ...] = ()
    patch: Patch | None = None
    result_revision_id: StableId | None = None
    render_artifact_id: StableId | None = None
    render_job_id: StableId | None = None
    assistant_message_id: StableId | None = None
    error: str | None = Field(default=None, max_length=2_000)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def completed_turn_has_message(self) -> AgentTurn:
        if self.status == "completed" and self.assistant_message_id is None:
            raise ValueError("completed Agent turn requires an assistant message")
        if self.result_revision_id is not None and self.patch is None:
            raise ValueError("edited Agent turn requires its applied Patch")
        if self.status == "failed" and not self.error:
            raise ValueError("failed Agent turn requires an error")
        return self


class AgentTurnTicket(ContractModel):
    turn: AgentTurn
    user_message: ConversationMessage
    replayed: bool = False


__all__ = [
    "AgentStepDecision",
    "AgentToolCall",
    "AgentToolName",
    "AgentToolResult",
    "AgentToolStep",
    "AgentTurn",
    "AgentTurnContext",
    "AgentTurnStatus",
    "AgentTurnTicket",
    "Conversation",
    "ConversationMessage",
    "ConversationPurpose",
    "ConversationRole",
    "ConversationTicket",
    "ContinuousEditOperation",
    "CreateAgentTurnRequest",
    "CreateConversationRequest",
    "InspectMediaCall",
    "MomentSearchHit",
    "MomentSearchQuery",
    "PatchProposal",
    "ProposePatchCall",
    "ReadProjectCall",
    "SearchMomentsCall",
    "WorkspaceSelection",
]
