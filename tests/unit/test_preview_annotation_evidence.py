from __future__ import annotations

import cv2
import numpy as np

from aive.contracts.conversation import PreviewAnnotation, PreviewSpatialRegion
from aive.contracts.project_doc import FrameRange
from aive.rendering.annotations import OpenCVPreviewAnnotationEvidenceExtractor


class _Capture:
    def __init__(self) -> None:
        self.offset_frame: float | None = None
        self.released = False

    def isOpened(self) -> bool:
        return True

    def set(self, property_id: int, value: float) -> None:
        assert property_id == cv2.CAP_PROP_POS_FRAMES
        self.offset_frame = value

    def read(self):
        return True, np.zeros((100, 200, 3), dtype=np.uint8)

    def release(self) -> None:
        self.released = True


def test_spatial_annotation_extracts_midpoint_and_draws_normalized_box(
    tmp_path,
    monkeypatch,
) -> None:
    capture = _Capture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _: capture)
    annotation = PreviewAnnotation(
        annotation_id="annotation_box",
        revision_id="revision_1",
        artifact_id="artifact_1",
        output_range=FrameRange(start_frame=40, duration_frames=5),
        spatial_region=PreviewSpatialRegion(
            x=0.25,
            y=0.2,
            width=0.5,
            height=0.4,
        ),
        comment="Move this title up.",
    )
    output = tmp_path / "annotation_box.png"

    evidence = OpenCVPreviewAnnotationEvidenceExtractor().extract(
        annotation,
        media_path=tmp_path / "preview.mp4",
        artifact_start_frame=30,
        output_path=output,
    )

    image = cv2.imread(str(output))
    assert evidence.evidence_frame == 42
    assert capture.offset_frame == 12
    assert capture.released is True
    assert image is not None
    assert image[20, 50].sum() > 0
    assert image[50, 100].sum() == 0
