"""Runtime evidence boundary for revision-bound preview annotations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..contracts.conversation import PreviewAnnotation


@dataclass(frozen=True)
class PreviewAnnotationImage:
    """One deterministic frame shown to a multimodal continuous-edit Director."""

    annotation_id: str
    evidence_frame: int
    path: Path


class PreviewAnnotationEvidenceExtractor(Protocol):
    def extract(
        self,
        annotation: PreviewAnnotation,
        *,
        media_path: Path,
        artifact_start_frame: int,
        output_path: Path,
    ) -> PreviewAnnotationImage: ...


__all__ = [
    "PreviewAnnotationEvidenceExtractor",
    "PreviewAnnotationImage",
]
