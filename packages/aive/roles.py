"""Model roles.

A domain concept, not a provider implementation detail: the model registry and
the provider layer both depend on it, so it lives above both.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Current model roles, selected and accounted independently."""

    MATERIAL = "material"  # (1) understands the user's own footage
    STYLE = "style"  # extracts transferable grammar from Style References
    EMBEDDING = "embedding"  # (4a) recall
    RERANK = "rerank"  # (4b) precision
    AGENT_FAST = "agent_fast"
    AGENT_DEEP = "agent_deep"
    DIRECTOR = "director"  # autonomous first-cut creative orchestration
    REVIEWER = "reviewer"  # revision-bound rendered-output quality gate
    ASR = "asr"
    AUDIO = "audio"  # raw sound events, speech meaning, emotion, rhythm
    RENDERER = "renderer"
