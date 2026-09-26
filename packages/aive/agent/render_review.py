"""Revision-bound technical and creative review of rendered media."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from math import log10
from pathlib import Path
from typing import Protocol

import cv2

from ..contracts.rendering import (
    RenderArtifact,
    RenderCreativeReview,
    RenderInspection,
    RenderReviewContext,
    RenderReviewIssue,
    RenderReviewReport,
)
from ..contracts.revision import ProjectRevision
from ..contracts.time import FrameRange
from ..contracts.timeline import AudioTrack
from ..material.probe import ffprobe
from ..providers.base import BaseProvider
from ..roles import Role


SAMPLE_FRACTIONS = (0.05, 0.25, 0.5, 0.75, 0.95)


class RenderInspector(Protocol):
    def inspect(
        self,
        media_path: Path,
        sample_dir: Path,
    ) -> tuple[RenderInspection, tuple[Path, ...]]: ...


class RenderReviewerProvider(Protocol):
    name: str
    version: str

    async def review(
        self,
        context: RenderReviewContext,
        media_path: Path,
        sample_paths: tuple[Path, ...],
    ) -> RenderCreativeReview: ...


@dataclass(frozen=True)
class RenderReviewOutcome:
    context: RenderReviewContext
    report: RenderReviewReport


class OfflineRenderReviewerProvider(BaseProvider):
    """Honest offline fallback: technical checks only, no visual claim."""

    role = Role.REVIEWER
    name = "offline-render-reviewer"
    version = "1.0.0"

    async def review(
        self,
        context: RenderReviewContext,
        media_path: Path,
        sample_paths: tuple[Path, ...],
    ) -> RenderCreativeReview:
        async def call() -> RenderCreativeReview:
            return RenderCreativeReview(
                visual_review_performed=False,
                summary=(
                    "Deterministic render checks passed; multimodal creative review "
                    "was not performed by the offline provider."
                ),
            )

        return await self._guarded(
            call,
            project_id=context.project_id,
        )


class OpenCVRenderInspector:
    """Measure decode, timing, dimensions, audio presence, and sampled pixels."""

    def inspect(
        self,
        media_path: Path,
        sample_dir: Path,
    ) -> tuple[RenderInspection, tuple[Path, ...]]:
        info = ffprobe(media_path)
        streams = info.get("streams", [])
        video = next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            None,
        )
        if video is None:
            raise ValueError("rendered artifact has no decodable video stream")
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        fps = _stream_fps(video)
        duration = float(
            info.get("format", {}).get("duration") or video.get("duration") or 0
        )
        if width <= 0 or height <= 0 or fps <= 0 or duration <= 0:
            raise ValueError("rendered artifact reports invalid video metadata")

        offsets = tuple(
            min(duration - min(1 / fps, duration / 2), duration * fraction)
            for fraction in SAMPLE_FRACTIONS
        )
        capture = cv2.VideoCapture(str(media_path))
        if not capture.isOpened():
            raise ValueError("rendered artifact cannot be decoded")
        sample_paths: list[Path] = []
        sampled_offsets: list[float] = []
        black = 0
        flat = 0
        sharpness: list[float] = []
        try:
            for index, offset in enumerate(offsets):
                capture.set(cv2.CAP_PROP_POS_MSEC, max(0, offset) * 1_000)
                ok, frame = capture.read()
                if not ok or frame is None:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                mean = float(gray.mean())
                deviation = float(gray.std())
                black += int(mean < 8)
                flat += int(deviation < 5)
                variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                sharpness.append(
                    0.0 if variance <= 0 else min(1.0, max(0.0, log10(variance) / 3.5))
                )
                path = sample_dir / f"sample_{index:02d}.jpg"
                path.parent.mkdir(parents=True, exist_ok=True)
                if cv2.imwrite(str(path), frame):
                    sample_paths.append(path)
                    sampled_offsets.append(offset)
        finally:
            capture.release()
        count = len(sharpness)
        return (
            RenderInspection(
                duration_seconds=duration,
                width=width,
                height=height,
                fps=fps,
                has_video=True,
                has_audio=any(
                    stream.get("codec_type") == "audio" for stream in streams
                ),
                sampled_frame_count=count,
                black_frame_ratio=black / count if count else 1,
                flat_frame_ratio=flat / count if count else 1,
                mean_sharpness=sum(sharpness) / count if count else 0,
                sample_offsets_seconds=tuple(sampled_offsets),
            ),
            tuple(sample_paths),
        )


class RenderReviewEngine:
    """Combine deterministic media gates with an optional multimodal Reviewer."""

    def __init__(
        self,
        *,
        inspector: RenderInspector | None = None,
        provider: RenderReviewerProvider | None = None,
    ) -> None:
        self._inspector = inspector or OpenCVRenderInspector()
        self._provider = provider or OfflineRenderReviewerProvider()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def provider_version(self) -> str:
        return self._provider.version

    async def review(
        self,
        *,
        revision: ProjectRevision,
        artifact: RenderArtifact,
        media_path: Path,
        review_id: str,
        reviewed_at: datetime,
    ) -> RenderReviewOutcome:
        _validate_binding(revision, artifact)
        with tempfile.TemporaryDirectory(prefix=f"aive_review_{artifact.artifact_id}_") as raw:
            inspection, sample_paths = self._inspector.inspect(media_path, Path(raw))
            context = _build_context(revision, artifact, inspection)
            technical = _technical_issues(context)
            blocking = any(issue.severity == "error" for issue in technical)
            creative = (
                RenderCreativeReview(
                    visual_review_performed=False,
                    summary="Multimodal review was skipped because technical checks failed.",
                )
                if blocking
                else await self._provider.review(context, media_path, sample_paths)
            )
        model_issues = tuple(
            issue.model_copy(update={"source": "model"})
            for issue in creative.issues
        )
        issues = (*technical, *model_issues)
        accepted = not any(issue.severity == "error" for issue in issues)
        technical_summary = (
            "Technical render checks passed."
            if not technical
            else f"Technical render checks produced {len(technical)} issue(s)."
        )
        report = RenderReviewReport(
            review_id=review_id,
            project_id=artifact.project_id,
            revision_id=artifact.revision_id,
            artifact_id=artifact.artifact_id,
            accepted=accepted,
            visual_review_performed=creative.visual_review_performed,
            issues=issues,
            summary=f"{technical_summary} {creative.summary}"[:2_000],
            provider=self._provider.name,
            provider_version=self._provider.version,
            reviewed_at=reviewed_at,
        )
        return RenderReviewOutcome(context=context, report=report)


def _build_context(
    revision: ProjectRevision,
    artifact: RenderArtifact,
    inspection: RenderInspection,
) -> RenderReviewContext:
    document = revision.document
    render_range = artifact.output_range or FrameRange(
        start_frame=0,
        duration_frames=document.timeline.duration_frames,
    )
    fps = document.timeline.output.fps.num / document.timeline.output.fps.den
    return RenderReviewContext(
        project_id=artifact.project_id,
        revision_id=artifact.revision_id,
        document_hash=revision.document_hash,
        artifact_id=artifact.artifact_id,
        kind=artifact.kind,
        quality=artifact.quality,
        intent=document.intent,
        output=document.timeline.output,
        expected_duration_seconds=render_range.duration_frames / fps,
        expects_audio=_expects_audio(document.timeline.tracks, render_range),
        inspection=inspection,
    )


def _technical_issues(
    context: RenderReviewContext,
) -> tuple[RenderReviewIssue, ...]:
    measured = context.inspection
    issues: list[RenderReviewIssue] = []
    tolerance = max(0.15, 2 / context.inspection.fps)
    if abs(measured.duration_seconds - context.expected_duration_seconds) > tolerance:
        issues.append(
            RenderReviewIssue(
                code="output_duration_mismatch",
                severity="error",
                source="technical",
                message=(
                    f"Rendered duration {measured.duration_seconds:.3f}s differs from "
                    f"the expected {context.expected_duration_seconds:.3f}s."
                ),
                repairable=False,
            )
        )
    expected_width, expected_height = context.output.width, context.output.height
    if context.quality == "proxy":
        expected_width = max(2, (expected_width // 2) // 2 * 2)
        expected_height = max(2, (expected_height // 2) // 2 * 2)
    if (measured.width, measured.height) != (expected_width, expected_height):
        issues.append(
            RenderReviewIssue(
                code="output_dimensions_mismatch",
                severity="error",
                source="technical",
                message=(
                    f"Rendered dimensions {measured.width}x{measured.height} differ "
                    f"from {expected_width}x{expected_height}."
                ),
                repairable=False,
            )
        )
    if measured.sampled_frame_count < 3:
        issues.append(
            RenderReviewIssue(
                code="insufficient_decodable_frames",
                severity="error",
                source="technical",
                message="Fewer than three representative frames could be decoded.",
                repairable=False,
            )
        )
    if measured.black_frame_ratio >= 0.4:
        issues.append(
            RenderReviewIssue(
                code="excessive_black_frames",
                severity="error",
                source="technical",
                message="Too many representative frames are effectively black.",
                repairable=True,
            )
        )
    if measured.flat_frame_ratio >= 0.6:
        issues.append(
            RenderReviewIssue(
                code="excessive_flat_frames",
                severity="error",
                source="technical",
                message="Too many representative frames contain almost no detail.",
                repairable=True,
            )
        )
    if context.expects_audio and not measured.has_audio:
        issues.append(
            RenderReviewIssue(
                code="expected_audio_missing",
                severity="error",
                source="technical",
                message="The ProjectDoc contains active audio but the render has no audio stream.",
                repairable=False,
            )
        )
    if measured.mean_sharpness < 0.12:
        issues.append(
            RenderReviewIssue(
                code="low_output_sharpness",
                severity="warning",
                source="technical",
                message="Representative render frames appear unusually soft.",
                repairable=True,
            )
        )
    return tuple(issues)


def _validate_binding(revision: ProjectRevision, artifact: RenderArtifact) -> None:
    if artifact.status != "ready":
        raise ValueError("only a ready render artifact can be reviewed")
    if revision.document.project_id != artifact.project_id:
        raise ValueError("render review artifact belongs to another project")
    if revision.document.revision_id != artifact.revision_id:
        raise ValueError("render review artifact belongs to another revision")


def _expects_audio(tracks: tuple, render_range: FrameRange) -> bool:
    for track in tracks:
        if not isinstance(track, AudioTrack) or not track.enabled:
            continue
        for clip in track.clips:
            if (
                clip.output.start_frame < render_range.end_frame
                and clip.output.end_frame > render_range.start_frame
            ):
                return True
    return False


def _stream_fps(stream: dict) -> float:
    raw = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
    numerator, _, denominator = str(raw).partition("/")
    try:
        divisor = float(denominator or 1)
        return float(numerator) / divisor if divisor else 0
    except ValueError:
        return 0
