"""Bounded dynamic tool loop for one continuous-edit Agent turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..contracts.conversation import (
    AgentToolStep,
    AgentToolResult,
    AgentTurnContext,
    PatchProposal,
    ProposePatchCall,
)
from .provider import DirectorProvider
from .tools import AgentToolPermissionError, AgentToolRegistry


class AgentToolStepLimitExceeded(RuntimeError):
    pass


class AgentToolLoopError(ValueError):
    pass


@dataclass(frozen=True)
class ContinuousEditOutcome:
    proposal: PatchProposal | None
    response: str
    steps: tuple[AgentToolStep, ...]


class ContinuousEditPipeline:
    """Let the provider choose approved tools while enforcing a hard step cap."""

    def __init__(self, provider: DirectorProvider) -> None:
        self._provider = provider

    async def run(
        self,
        context: AgentTurnContext,
        *,
        existing_steps: tuple[AgentToolStep, ...] = (),
        on_step: Callable[[AgentToolStep], None] | None = None,
        proposal_validator: Callable[[PatchProposal], None] | None = None,
    ) -> ContinuousEditOutcome:
        steps = list(existing_steps)
        _validate_existing_steps(context, steps)
        for step in steps:
            if step.result.proposal is not None:
                return ContinuousEditOutcome(
                    proposal=step.result.proposal,
                    response=step.result.proposal.rationale,
                    steps=tuple(steps),
                )

        registry = AgentToolRegistry(
            context,
            proposal_validator=proposal_validator,
        )
        seen_call_ids = {step.call.call_id for step in steps}
        while len(steps) < context.max_tool_steps:
            decision = await self._provider.next_edit_step(context, tuple(steps))
            if decision.kind == "finish":
                assert decision.response is not None
                return ContinuousEditOutcome(
                    proposal=None,
                    response=decision.response,
                    steps=tuple(steps),
                )
            if decision.call is None:
                raise AgentToolLoopError("provider returned a tool step without a call")
            if decision.call.call_id in seen_call_ids:
                raise AgentToolLoopError(
                    f"provider reused tool call ID {decision.call.call_id!r}"
                )
            try:
                result = registry.execute(decision.call)
            except AgentToolPermissionError as exc:
                if not isinstance(decision.call, ProposePatchCall):
                    raise
                # A syntactically valid proposal can still violate Patch scope,
                # source-audio pairing, or ripple policy.  Persist the rejected
                # call and return its deterministic error to the model so it can
                # correct the proposal inside the same bounded turn.  The bad
                # proposal is never exposed as an outcome and never reaches
                # ProjectService.
                result = AgentToolResult(
                    call_id=decision.call.call_id,
                    tool=decision.call.tool,
                    error=str(exc),
                )
            step = AgentToolStep(
                step_index=len(steps),
                call=decision.call,
                result=result,
            )
            steps.append(step)
            seen_call_ids.add(decision.call.call_id)
            if on_step is not None:
                on_step(step)
            if result.proposal is not None:
                return ContinuousEditOutcome(
                    proposal=result.proposal,
                    response=result.proposal.rationale,
                    steps=tuple(steps),
                )
        raise AgentToolStepLimitExceeded(
            f"Agent turn exceeded its {context.max_tool_steps}-step tool limit"
        )


def _validate_existing_steps(
    context: AgentTurnContext,
    steps: list[AgentToolStep],
) -> None:
    if len(steps) > context.max_tool_steps:
        raise AgentToolLoopError("persisted tool trace exceeds the context limit")
    expected = list(range(len(steps)))
    actual = [step.step_index for step in steps]
    if actual != expected:
        raise AgentToolLoopError("persisted tool trace is not contiguous")
    call_ids = [step.call.call_id for step in steps]
    if len(call_ids) != len(set(call_ids)):
        raise AgentToolLoopError("persisted tool trace contains duplicate call IDs")


__all__ = [
    "AgentToolLoopError",
    "AgentToolStepLimitExceeded",
    "ContinuousEditOutcome",
    "ContinuousEditPipeline",
]
