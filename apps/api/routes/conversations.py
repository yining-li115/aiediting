"""HTTP adapter for project conversations and continuous-edit Agent turns."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from aive.agent import ConversationApplication, ConversationWorkflow, VideoAgentHarness
from aive.contracts.conversation import (
    AgentTurn,
    AgentTurnTicket,
    Conversation,
    ConversationTicket,
    CreateAgentTurnRequest,
    CreateConversationRequest,
)
from aive.db.analysis_repository import SqlAlchemyMediaAnalysisRepository
from aive.db.conversation_repository import SqlAlchemyConversationRepository
from aive.db.media_repository import SqlAlchemyMediaAssetRepository
from aive.db.session import get_session_factory
from aive.jobs import JobRunner
from aive.media.audio_asset_analysis import AudioAssetAnalysisApplication
from aive.providers import get_provider
from aive.rendering import RevisionRenderApplication
from aive.rendering.annotations import OpenCVPreviewAnnotationEvidenceExtractor
from aive.roles import Role
from aive.storage import get_storage

from .director import get_video_agent_harness
from .project_revisions import get_project_revision_application
from .renders import get_revision_render_application


router = APIRouter(prefix="/projects/{project_id}", tags=["project conversations"])


def get_conversation_application(
    harness: VideoAgentHarness = Depends(get_video_agent_harness),
) -> ConversationApplication:
    sessions = get_session_factory()
    return ConversationApplication(
        repository=SqlAlchemyConversationRepository(sessions),
        projects=get_project_revision_application(),
        analysis=SqlAlchemyMediaAnalysisRepository(sessions),
        rendering=get_revision_render_application(),
        harness=harness,
        assets=SqlAlchemyMediaAssetRepository(sessions),
        audio_assets=AudioAssetAnalysisApplication(
            storage=get_storage(),
            assets=SqlAlchemyMediaAssetRepository(sessions),
            provider=get_provider(Role.AUDIO),
        ),
        annotation_evidence=OpenCVPreviewAnnotationEvidenceExtractor(),
    )


def get_conversation_workflow(
    conversations: ConversationApplication = Depends(get_conversation_application),
    rendering: RevisionRenderApplication = Depends(get_revision_render_application),
) -> ConversationWorkflow:
    return ConversationWorkflow(
        jobs=JobRunner(get_session_factory()),
        conversations=conversations,
        rendering=rendering,
    )


@router.post("/conversations", response_model=ConversationTicket)
def create_conversation(
    project_id: str,
    request: CreateConversationRequest,
    application: ConversationApplication = Depends(get_conversation_application),
) -> ConversationTicket:
    _require_matching_project(project_id, request.project_id)
    return application.create_conversation(request)


@router.get("/conversations/{conversation_id}", response_model=Conversation)
def get_conversation(
    project_id: str,
    conversation_id: str,
    application: ConversationApplication = Depends(get_conversation_application),
) -> Conversation:
    conversation = application.get_conversation(project_id, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation


@router.post(
    "/conversations/{conversation_id}/turns",
    response_model=AgentTurnTicket,
)
def create_agent_turn(
    project_id: str,
    conversation_id: str,
    request: CreateAgentTurnRequest,
    background: BackgroundTasks,
    workflow: ConversationWorkflow = Depends(get_conversation_workflow),
    application: ConversationApplication = Depends(get_conversation_application),
) -> AgentTurnTicket:
    _require_matching_project(project_id, request.project_id)
    if conversation_id != request.conversation_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "conversation_path_mismatch",
                "message": "request conversation_id must match the URL",
                "retryable": False,
            },
        )
    ticket = application.enqueue_turn(request)
    if ticket.turn.status in {"queued", "running"}:
        background.add_task(workflow.run, ticket.turn.job_id)
    return ticket


@router.get(
    "/conversations/{conversation_id}/turns/{turn_id}",
    response_model=AgentTurn,
)
def get_agent_turn(
    project_id: str,
    conversation_id: str,
    turn_id: str,
    application: ConversationApplication = Depends(get_conversation_application),
) -> AgentTurn:
    turn = application.get_turn(project_id, turn_id)
    if turn is None or turn.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Agent turn not found")
    return turn


def _require_matching_project(path_project_id: str, body_project_id: str) -> None:
    if path_project_id != body_project_id:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "project_path_mismatch",
                "message": "request project_id must match the URL project_id",
                "retryable": False,
            },
        )
