"""Durable orchestration for continuous-edit Agent turns and their previews."""

from __future__ import annotations

from ..jobs import JobRunner
from ..rendering.application import RevisionRenderApplication
from .conversation_application import ConversationApplication


class ConversationWorkflow:
    def __init__(
        self,
        *,
        jobs: JobRunner,
        conversations: ConversationApplication,
        rendering: RevisionRenderApplication,
    ) -> None:
        self._jobs = jobs
        self._conversations = conversations
        self._rendering = rendering

    async def run(self, job_id: str) -> None:
        await self._jobs.run(job_id, self._conversations.process)
        turn = self._conversations.get_job_turn(job_id)
        if turn.status == "completed" and turn.render_job_id is not None:
            await self._jobs.run(turn.render_job_id, self._rendering.process)


__all__ = ["ConversationWorkflow"]
