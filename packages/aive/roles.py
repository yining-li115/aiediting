"""Model roles.

A domain concept, not a provider implementation detail: the model registry and
the provider layer both depend on it, so it lives above both.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """The model roles. Each is selected, configured and priced separately.

    The numbering matches docs/TECHNICAL_PLAN.md section 0.1.
    """

    MATERIAL = "material"  # (1) understands the user's own footage
    REFERENCE = "reference"  # (2) understands the reference video
    PLANNER = "planner"  # (3) produces EditPlanSpec
    EMBEDDING = "embedding"  # (4a) recall
    RERANK = "rerank"  # (4b) precision
    CRITIC = "critic"  # (5) which cut is better - the only tunable-required one
    AGENT_FAST = "agent_fast"  # (6a) local deterministic edits
    AGENT_DEEP = "agent_deep"  # (6b) edits that need re-planning
    ASR = "asr"
    RENDERER = "renderer"
