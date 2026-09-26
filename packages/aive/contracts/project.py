"""Commands that create the durable Project identity and first revision."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .base import ChangeOrigin, ContractModel, StableId
from .project_doc import ProjectIntent
from .timeline import OutputSettings


class CreateProjectRequest(ContractModel):
    """Replay-safe request for one minimal authoritative project."""

    request_version: Literal["1.0"] = "1.0"
    request_id: StableId
    # The durable Project primary key is VARCHAR(64); reject incompatible
    # client-generated IDs at the contract boundary instead of relying on a
    # permissive SQLite development database.
    project_id: Annotated[StableId, Field(max_length=64)]
    idempotency_key: StableId
    origin: ChangeOrigin
    actor_id: StableId | None = None
    summary: str | None = Field(default=None, max_length=1_000)
    intent: ProjectIntent
    output: OutputSettings
