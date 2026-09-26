"""Concrete capability policies used at application composition boundaries."""

from __future__ import annotations

from ..contracts.revision import ProjectRevision
from .service import CapabilityUse, CapabilityValidator, iter_capability_uses


class UnconfiguredCapabilityValidator:
    """Fail closed until an executable definition registry is installed.

    Documents without effects or motion remain editable. A document that uses
    an executable definition cannot be committed through the HTTP API until a
    real registry can validate its name, version, and parameters.
    """

    def rejection_reason(self, capability: CapabilityUse) -> str:
        return (
            f"{capability.kind} capability {capability.name!r} "
            f"at version {capability.version!r} is not installed"
        )


class PreserveCommittedCapabilities:
    """Allow exact capabilities inherited from trusted immutable revisions.

    This does not authorize a new definition, version, owner, or parameter
    value. It only keeps an unrelated edit or restore usable when the runtime
    registry is temporarily unavailable; every changed capability still goes
    through the configured validator and therefore fails closed by default.
    """

    def __init__(
        self,
        validator: CapabilityValidator,
        revisions: tuple[ProjectRevision, ...],
    ) -> None:
        self._validator = validator
        self._trusted = tuple(
            capability
            for revision in revisions
            for capability in iter_capability_uses(revision.document)
        )

    def rejection_reason(self, capability: CapabilityUse) -> str | None:
        if capability in self._trusted:
            return None
        return self._validator.rejection_reason(capability)
