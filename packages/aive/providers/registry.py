"""Role -> implementation lookup.

Business code calls ``get_provider(Role.MATERIAL)``. Which model that resolves
to is decided entirely by ``aive.model_registry`` - this module only maps an
adapter name to a client class.

The split matters: several models share one adapter, and a model swap should
change the catalog entry, not the code path.
"""

from __future__ import annotations

from typing import Any, Callable

from ..model_registry import ModelCard, card_for
from .base import BaseProvider, Role
from .mock import (
    MockAgentProvider,
    MockASRProvider,
    MockEmbeddingProvider,
    MockMaterialProvider,
    MockPlannerProvider,
    MockReferenceProvider,
    MockRerankProvider,
    PassthroughCritic,
)

Factory = Callable[..., BaseProvider]

# (adapter, role) -> client class. Adapters are implementations; models are
# configuration. A new vendor on an existing adapter needs no entry here.
_ADAPTERS: dict[tuple[str, Role], Factory] = {
    ("mock", Role.MATERIAL): MockMaterialProvider,
    ("mock", Role.REFERENCE): MockReferenceProvider,
    ("mock", Role.PLANNER): MockPlannerProvider,
    ("mock", Role.EMBEDDING): MockEmbeddingProvider,
    ("mock", Role.RERANK): MockRerankProvider,
    ("mock", Role.AGENT_FAST): MockAgentProvider,
    ("mock", Role.AGENT_DEEP): MockAgentProvider,
    ("mock", Role.ASR): MockASRProvider,
    ("passthrough", Role.CRITIC): PassthroughCritic,
}


def _openai_compatible_factory(role: Role):
    """Built lazily so importing the registry does not pull in httpx clients."""

    def factory(card: ModelCard | None = None, **kwargs: Any) -> BaseProvider:
        from .openai_compatible import (  # noqa: PLC0415
            OpenAICompatibleCriticProvider,
            OpenAICompatibleMaterialProvider,
            OpenAICompatiblePlannerProvider,
        )

        classes = {
            Role.MATERIAL: OpenAICompatibleMaterialProvider,
            Role.REFERENCE: OpenAICompatibleMaterialProvider,
            Role.PLANNER: OpenAICompatiblePlannerProvider,
            Role.AGENT_FAST: OpenAICompatiblePlannerProvider,
            Role.AGENT_DEEP: OpenAICompatiblePlannerProvider,
            Role.CRITIC: OpenAICompatibleCriticProvider,
        }
        assert card is not None, "openai_compatible needs its ModelCard"
        return classes[role](card=card, **kwargs)

    return factory


for _role in (
    Role.MATERIAL,
    Role.REFERENCE,
    Role.PLANNER,
    Role.CRITIC,
    Role.AGENT_FAST,
    Role.AGENT_DEEP,
):
    _ADAPTERS[("openai_compatible", _role)] = _openai_compatible_factory(_role)


def _local_openclip_factory(**kwargs: Any) -> BaseProvider:
    """Imported on demand so that torch is only loaded if actually selected."""
    from ..material.embedding import LocalOpenClipProvider  # noqa: PLC0415

    return LocalOpenClipProvider(**kwargs)


_ADAPTERS[("local_openclip", Role.EMBEDDING)] = _local_openclip_factory


def register_adapter(adapter: str, role: Role, factory: Factory) -> None:
    """Adapters register themselves; used by optional/heavy implementations."""
    _ADAPTERS[(adapter, role)] = factory


def available_adapters(role: Role) -> list[str]:
    return sorted(name for (name, r) in _ADAPTERS if r == role)


def get_provider(
    role: Role, model_key: str | None = None, **kwargs: Any
) -> BaseProvider:
    """Resolve a role to a ready-to-use client.

    ``model_key`` is an explicit override, mostly for tests and bake-off runs;
    normal callers pass nothing and get whatever the catalog selects.
    """
    card: ModelCard = card_for(role, model_key)
    factory = _ADAPTERS.get((card.adapter, role))
    if factory is None:
        raise KeyError(
            f"model {card.key!r} needs adapter {card.adapter!r} for role "
            f"{role.value!r}, which is not implemented; "
            f"available adapters for this role: {available_adapters(role)}"
        )
    if card.adapter == "openai_compatible":
        # Vendor adapters are configured entirely from the card: base URL,
        # credentials env var and pinned model id all travel with it.
        return factory(card=card, **kwargs)

    provider = factory(**kwargs)
    # The card is the single source of truth for what gets logged as the model.
    provider.model = card.model_id
    provider.name = card.key
    return provider
