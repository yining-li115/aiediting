"""Shared primitives for the closed Patch v1 operation protocol."""

from __future__ import annotations

from .base import ContractModel, StableId


class PatchOperationBase(ContractModel):
    """Identity shared by every atomic operation in one ordered Patch."""

    operation_id: StableId
