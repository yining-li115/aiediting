"""Renderer protocol and result type.

Hard rule: a renderer receives a TimelineSpec and nothing else. It must never
need an EditPlanSpec. That is what lets a different backend - or later an NLE
project exporter - be written without understanding editing intent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..specs.timeline import TimelineSpec
from .profiles import Quality


class RenderResult:
    __slots__ = ("path", "quality", "duration_sec", "render_time_ms", "file_size")

    def __init__(
        self,
        path: Path,
        quality: str,
        duration_sec: float | None = None,
        render_time_ms: int | None = None,
        file_size: int | None = None,
    ) -> None:
        self.path = path
        self.quality = quality
        self.duration_sec = duration_sec
        self.render_time_ms = render_time_ms
        self.file_size = file_size

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "quality": self.quality,
            "duration_sec": self.duration_sec,
            "render_time_ms": self.render_time_ms,
            "file_size": self.file_size,
        }


@runtime_checkable
class RendererProvider(Protocol):
    def render(
        self,
        timeline: TimelineSpec,
        quality: Quality,
        out_path: Path,
        sources: dict[str, Path],
        time_range: tuple[float, float] | None = None,
    ) -> RenderResult: ...
