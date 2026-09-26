"""Model access layer. One protocol per cognitive role, vendors behind it."""

from .base import (
    BaseProvider,
    CostRecord,
    Role,
    capture_provenance,
    record_cost,
    set_cost_sink,
)
from .registry import available_adapters, get_provider, register_adapter
from .roles import (
    ASRProvider,
    EditingAgentProvider,
    EmbeddingProvider,
    MaterialUnderstandingProvider,
    MomentGroupingProvider,
    PlannerProvider,
    ReferenceUnderstandingProvider,
    RerankProvider,
    TasteCriticProvider,
)

__all__ = [
    "Role",
    "BaseProvider",
    "CostRecord",
    "record_cost",
    "set_cost_sink",
    "capture_provenance",
    "get_provider",
    "register_adapter",
    "available_adapters",
    "MaterialUnderstandingProvider",
    "MomentGroupingProvider",
    "ReferenceUnderstandingProvider",
    "PlannerProvider",
    "EmbeddingProvider",
    "RerankProvider",
    "TasteCriticProvider",
    "EditingAgentProvider",
    "ASRProvider",
]
