"""ProjectService capability validation backed by the published registry."""

from __future__ import annotations

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ..contracts.captions import CLEAN_CAPTION_COMPONENT, CLEAN_CAPTION_VERSION
from ..contracts.components import ComponentUsage
from ..project.capabilities import DefaultCapabilityValidator
from ..project.service import CapabilityUse
from .repository import ComponentRegistry
from .runtime import RemotionComponentRuntime, RemotionComponentRuntimeError


class RegisteredCapabilityValidator:
    """Accept built-ins or exact immutable registry entries, and nothing else."""

    def __init__(
        self,
        registry: ComponentRegistry,
        *,
        supported_usages: frozenset[ComponentUsage] | None = None,
        runtime: RemotionComponentRuntime | None = None,
    ) -> None:
        self._registry = registry
        self._builtins = DefaultCapabilityValidator()
        self._supported_usages = supported_usages
        self._runtime = runtime

    def rejection_reason(self, capability: CapabilityUse) -> str | None:
        if (
            capability.kind == "component"
            and capability.name == CLEAN_CAPTION_COMPONENT
            and capability.version == CLEAN_CAPTION_VERSION
        ):
            return self._builtins.rejection_reason(capability)

        component = self._registry.get(
            capability.kind,
            capability.name,
            capability.version,
        )
        if component is None:
            return (
                f"{capability.kind} capability {capability.name!r} "
                f"at version {capability.version!r} is not published"
            )
        if (
            self._supported_usages is not None
            and component.manifest.usage not in self._supported_usages
        ):
            return (
                f"{component.manifest.usage} capability {capability.name!r} is "
                "published but is not executable by the configured renderer"
            )
        if self._runtime is not None:
            try:
                self._runtime.validate_available()
            except RemotionComponentRuntimeError as exc:
                return f"Remotion runtime is unavailable: {exc}"
        try:
            Draft202012Validator(component.manifest.parameter_schema).validate(
                dict(capability.parameters)
            )
        except ValidationError as exc:
            return f"invalid {capability.name} parameters: {exc.message}"
        return None


__all__ = ["RegisteredCapabilityValidator"]
