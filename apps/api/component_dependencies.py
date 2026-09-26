"""Composition helpers for the approved Remotion component runtime."""

from __future__ import annotations

from aive.components import (
    ComponentRegistryApplication,
    LocalRemotionComponentRuntime,
    LocalRemotionSandbox,
    StorageComponentRegistry,
)
from aive.config import settings
from aive.rendering import ProjectDocRemotionRenderer
from aive.storage import get_storage


def get_component_registry() -> StorageComponentRegistry:
    return StorageComponentRegistry(get_storage())


def get_component_runtime() -> LocalRemotionComponentRuntime:
    return LocalRemotionComponentRuntime(
        runtime_root=settings.remotion_runtime_root,
        registry=get_component_registry(),
    )


def get_component_application() -> ComponentRegistryApplication:
    return ComponentRegistryApplication(
        registry=get_component_registry(),
        sandbox=LocalRemotionSandbox(
            runtime_root=settings.remotion_runtime_root,
        ),
    )


def get_project_renderer() -> ProjectDocRemotionRenderer:
    return ProjectDocRemotionRenderer(
        registry=get_component_registry(),
        component_runtime=get_component_runtime(),
    )


__all__ = [
    "get_component_application",
    "get_component_registry",
    "get_component_runtime",
    "get_project_renderer",
]
