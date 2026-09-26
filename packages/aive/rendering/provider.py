"""Renderer port for the authoritative revision-bound ProjectDoc path."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..contracts.project_doc import ProjectDoc
from ..contracts.time import FrameRange
from ..renderer.base import RenderResult
from ..renderer.profiles import Quality


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
