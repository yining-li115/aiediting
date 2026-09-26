"""Base types shared by persisted AIVE contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


StableId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]

ChangeOrigin = Literal["user", "editor", "agent", "system"]


class ContractModel(BaseModel):
    """Closed, immutable value object used at a persisted boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)
