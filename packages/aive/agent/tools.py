"""Read-only tools over the evidence frozen into an AgentRunContext."""

from __future__ import annotations

import re

from pydantic import Field

from ..contracts.base import ContractModel, StableId
from ..contracts.director import AgentRunContext
from ..contracts.moments import MomentEvidence


class MomentToolError(ValueError):
    pass


class MomentSearchQuery(ContractModel):
    text: str | None = Field(default=None, max_length=2_000)
    semantic_roles: tuple[str, ...] = ()
    require_speech: bool | None = None
    usable_only: bool = True
    minimum_quality: float | None = Field(default=None, ge=0, le=1)
    limit: int = Field(default=50, ge=1, le=200)


class MomentSearchHit(ContractModel):
    moment_id: StableId
    score: float = Field(ge=0)
    reasons: tuple[str, ...] = ()


class DirectorMomentTools:
    """Search and inspect only the immutable evidence supplied to this run."""

    def __init__(self, context: AgentRunContext) -> None:
        self._moments = {moment.moment_id: moment for moment in context.moments}

    def search_moments(
        self,
        query: MomentSearchQuery,
    ) -> tuple[MomentSearchHit, ...]:
        query_tokens = _tokens(query.text or "")
        roles = {role.casefold() for role in query.semantic_roles}
        hits: list[tuple[MomentSearchHit, int, int]] = []
        for order, moment in enumerate(self._moments.values()):
            if query.usable_only and not moment.is_usable:
                continue
            if query.require_speech is not None and moment.has_speech != query.require_speech:
                continue
            if roles and moment.semantic_role.casefold() not in roles:
                continue
            quality = moment.technical_quality
            if query.minimum_quality is not None and (
                quality is None or quality < query.minimum_quality
            ):
                continue

            searchable = " ".join(
                part
                for part in (
                    moment.semantic,
                    moment.semantic_role,
                    moment.shot_type,
                    moment.asr_text,
                    " ".join(moment.emotion),
                )
                if part
            )
            overlap = len(query_tokens & _tokens(searchable))
            score = float(overlap * 10)
            reasons: list[str] = []
            if overlap:
                reasons.append("text_match")
            if quality is not None:
                score += quality
                reasons.append("technical_quality")
            if moment.interestingness is not None:
                score += moment.interestingness
                reasons.append("interestingness")
            hits.append(
                (
                    MomentSearchHit(
                        moment_id=moment.moment_id,
                        score=score,
                        reasons=tuple(reasons),
                    ),
                    overlap,
                    order,
                )
            )

        hits.sort(key=lambda item: (-item[1], -item[0].score, item[2]))
        return tuple(item[0] for item in hits[: query.limit])

    def inspect_media(
        self,
        moment_ids: tuple[str, ...],
    ) -> tuple[MomentEvidence, ...]:
        result: list[MomentEvidence] = []
        for moment_id in moment_ids:
            moment = self._moments.get(moment_id)
            if moment is None:
                raise MomentToolError(
                    f"Moment {moment_id!r} is not in this Agent run context"
                )
            result.append(moment)
        return tuple(result)


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", value.casefold()))
