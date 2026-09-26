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
    # (3) splits in two. Turning a sentence into constraints and turning
    # constraints into a cut are different jobs with different failure modes,
    # and they will not stay on the same model: extraction is cheap and nearly
    # solved, planning is the part that decides whether the video is any good.
    # They happen to share an endpoint today; that is configuration.
    INTENT = "intent"  # (3a) prompt -> Intent
    PLANNER = "planner"  # (3b) conditioning + pool -> EditPlanSpec
    EMBEDDING = "embedding"  # (4a) recall
    RERANK = "rerank"  # (4b) precision
    CRITIC = "critic"  # (5) which cut is better - the only tunable-required one
    AGENT_FAST = "agent_fast"  # (6a) local deterministic edits
    AGENT_DEEP = "agent_deep"  # (6b) edits that need re-planning
    DIRECTOR = "director"  # autonomous first-cut creative orchestration
    REVIEWER = "reviewer"  # revision-bound rendered-output quality gate
    ASR = "asr"
    RENDERER = "renderer"
