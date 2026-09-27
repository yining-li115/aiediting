"""Extract visible evidence for spatial Preview Annotations."""

from __future__ import annotations

from pathlib import Path

import cv2

from ..agent.preview_evidence import PreviewAnnotationImage
from ..contracts.conversation import PreviewAnnotation


class PreviewAnnotationEvidenceError(RuntimeError):
    pass


class OpenCVPreviewAnnotationEvidenceExtractor:
    """Render one representative frame with the normalized selection outlined."""

    def __init__(self, *, max_width: int = 1_280) -> None:
        if max_width <= 0:
            raise ValueError("preview annotation evidence width must be positive")
        self._max_width = max_width

    def extract(
        self,
        annotation: PreviewAnnotation,
        *,
        media_path: Path,
        artifact_start_frame: int,
        output_path: Path,
    ) -> PreviewAnnotationImage:
        region = annotation.spatial_region
        if region is None:
            raise PreviewAnnotationEvidenceError(
                "spatial evidence requires a spatial annotation region"
            )
        evidence_frame = (
            annotation.output_range.start_frame
            + (annotation.output_range.duration_frames - 1) // 2
        )
        relative_frame = evidence_frame - artifact_start_frame
        if relative_frame < 0:
            raise PreviewAnnotationEvidenceError(
                "annotation frame precedes the rendered artifact"
            )
        capture = cv2.VideoCapture(str(media_path))
        if not capture.isOpened():
            raise PreviewAnnotationEvidenceError(
                f"could not open preview artifact {media_path.name!r}"
            )
        try:
            capture.set(cv2.CAP_PROP_POS_FRAMES, relative_frame)
            ok, frame = capture.read()
        finally:
            capture.release()
        if not ok or frame is None:
            raise PreviewAnnotationEvidenceError(
                f"could not read annotation frame {evidence_frame}"
            )

        height, width = frame.shape[:2]
        x1 = min(width - 1, max(0, round(region.x * width)))
        y1 = min(height - 1, max(0, round(region.y * height)))
        x2 = min(width - 1, max(x1 + 1, round((region.x + region.width) * width)))
        y2 = min(
            height - 1,
            max(y1 + 1, round((region.y + region.height) * height)),
        )
        thickness = max(2, min(width, height) // 160)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 165, 255), thickness)

        if width > self._max_width:
            scaled_height = max(1, round(height * self._max_width / width))
            frame = cv2.resize(
                frame,
                (self._max_width, scaled_height),
                interpolation=cv2.INTER_AREA,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output_path), frame):
            raise PreviewAnnotationEvidenceError(
                f"could not write annotation evidence {output_path.name!r}"
            )
        return PreviewAnnotationImage(
            annotation_id=annotation.annotation_id,
            evidence_frame=evidence_frame,
            path=output_path,
        )


__all__ = [
    "OpenCVPreviewAnnotationEvidenceExtractor",
    "PreviewAnnotationEvidenceError",
]
