"""Renderer port for the authoritative revision-bound ProjectDoc path."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ..contracts.project_doc import ProjectDoc
from ..contracts.time import FrameRange

Quality = Literal["proxy", "hq"]


class RenderResult:
    """Objective output metadata returned by every revision-bound renderer."""

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
class ProjectRendererProvider(Protocol):
    name: str
    version: str

    def validate(
        self,
        document: ProjectDoc,
        output_range: FrameRange | None = None,
    ) -> None: ...

    def render(
        self,
        document: ProjectDoc,
        quality: Quality,
        out_path: Path,
        sources: dict[str, Path],
        output_range: FrameRange | None = None,
    ) -> RenderResult: ...
