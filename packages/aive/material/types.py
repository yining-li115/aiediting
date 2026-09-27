"""Internal media-probe values; never authoritative edit state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ProbedAsset:
    asset_id: str
    project_id: str
    storage_key: str
    kind: Literal["video", "photo"]
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    original_name: str | None = None
    proxy_key: str | None = None


__all__ = ["ProbedAsset"]
