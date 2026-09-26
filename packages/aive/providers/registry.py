"""Role -> implementation lookup.

Business code calls ``get_provider(Role.MATERIAL)``. Which model that resolves
to is decided entirely by ``aive.model_registry`` - this module only maps an
adapter name to a client class.

The split matters: several models share one adapter, and a model swap should
change the catalog entry, not the code path.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from ..model_registry import ModelCard, card_for
from .base import BaseProvider, Role
from .mock import (
    MockAgentProvider,
    MockASRProvider,
    MockEmbeddingProvider,
    MockIntentProvider,
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
    ("mock", Role.INTENT): MockIntentProvider,
    ("mock", Role.PLANNER): MockPlannerProvider,
    ("mock", Role.EMBEDDING): MockEmbeddingProvider,
    ("mock", Role.RERANK): MockRerankProvider,
    ("mock", Role.AGENT_FAST): MockAgentProvider,
    ("mock", Role.AGENT_DEEP): MockAgentProvider,
    ("mock", Role.ASR): MockASRProvider,
    ("passthrough", Role.CRITIC): PassthroughCritic,
}


def _offline_director_factory(**kwargs: Any) -> BaseProvider:
    from ..agent.provider import OfflineDirectorProvider  # noqa: PLC0415

    return OfflineDirectorProvider(**kwargs)


_ADAPTERS[("mock", Role.DIRECTOR)] = _offline_director_factory


def _offline_render_reviewer_factory(**kwargs: Any) -> BaseProvider:
    from ..agent.render_review import (  # noqa: PLC0415
        OfflineRenderReviewerProvider,
    )

    return OfflineRenderReviewerProvider(**kwargs)


_ADAPTERS[("mock", Role.REVIEWER)] = _offline_render_reviewer_factory


def _openai_compatible_factory(role: Role):
    """Built lazily so importing the registry does not pull in httpx clients."""

    def factory(card: ModelCard | None = None, **kwargs: Any) -> BaseProvider:
        from .openai_compatible import (  # noqa: PLC0415
            OpenAICompatibleCriticProvider,
            OpenAICompatibleASRProvider,
            OpenAICompatibleIntentProvider,
            OpenAICompatibleMaterialProvider,
            OpenAICompatiblePlannerProvider,
        )
        from ..agent.provider import (  # noqa: PLC0415
            OpenAICompatibleDirectorProvider,
            VolcanoDirectorProvider,
            VolcanoRenderReviewerProvider,
        )

        director_class = (
            VolcanoDirectorProvider
            if card is not None and card.vendor == "bytedance-volcengine"
            else OpenAICompatibleDirectorProvider
        )

        classes = {
            Role.MATERIAL: OpenAICompatibleMaterialProvider,
            Role.REFERENCE: OpenAICompatibleMaterialProvider,
            Role.INTENT: OpenAICompatibleIntentProvider,
            Role.PLANNER: OpenAICompatiblePlannerProvider,
            Role.AGENT_FAST: OpenAICompatiblePlannerProvider,
            Role.AGENT_DEEP: OpenAICompatiblePlannerProvider,
            Role.CRITIC: OpenAICompatibleCriticProvider,
            Role.ASR: OpenAICompatibleASRProvider,
            Role.DIRECTOR: director_class,
            Role.REVIEWER: VolcanoRenderReviewerProvider,
        }
        assert card is not None, "openai_compatible needs its ModelCard"
        return classes[role](card=card, **kwargs)

    return factory


for _role in (
    Role.MATERIAL,
    Role.REFERENCE,
    Role.INTENT,
    Role.PLANNER,
    Role.CRITIC,
    Role.AGENT_FAST,
    Role.AGENT_DEEP,
    Role.DIRECTOR,
    Role.REVIEWER,
    Role.ASR,
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
    provider = _provider_for_card(role, card, **kwargs)
    if role != Role.DIRECTOR or model_key is not None:
        return provider

    fallback_value = os.environ.get("AIVE_ROLE_DIRECTOR_FALLBACK_MODELS", "")
    fallback_keys = tuple(
        key.strip() for key in fallback_value.split(",") if key.strip()
    )
    if not fallback_keys:
        return provider
    chain_keys = (card.key, *fallback_keys)
    if len(chain_keys) != len(set(chain_keys)):
        raise ValueError("Director fallback model keys must be unique")
    from ..agent.provider import DirectorFailoverProvider  # noqa: PLC0415

    providers = (
        provider,
        *(
            _provider_for_card(role, card_for(role, key), **kwargs)
            for key in fallback_keys
        ),
    )
    return DirectorFailoverProvider(providers)


def _provider_for_card(
    role: Role,
    card: ModelCard,
    **kwargs: Any,
) -> BaseProvider:
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
