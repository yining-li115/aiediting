"""Hybrid ProjectDoc renderer for approved Remotion overlay components."""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import JsonValue

from ..components import (
    ComponentRegistry,
    ComponentRenderRequest,
    RemotionComponentRuntime,
    RemotionComponentRuntimeError,
)
from ..config import settings
from ..contracts.captions import CLEAN_CAPTION_COMPONENT, CLEAN_CAPTION_VERSION
from ..contracts.components import PublishedComponent
from ..contracts.project_doc import ProjectDoc
from ..contracts.time import FrameRange
from ..contracts.timeline import CaptionTrack, GraphicTrack
from .ffmpeg import ProjectDocFFmpegRenderer, ProjectRenderUnsupported
from .media import _run, probe, probe_duration
from .provider import Quality, RenderResult


@dataclass(frozen=True)
class _ComponentOverlay:
    component: PublishedComponent
    props: dict[str, JsonValue]
    context: dict[str, JsonValue]
    start_frame: int
    duration_frames: int


class ProjectDocRemotionRenderer:
    """Render media with FFmpeg and approved full-frame Remotion overlays.

    Caption and Graphic components use the stable AIVE component runtime ABI.
    Pixel-transforming motion, transitions, and effects remain fail-closed
    until their input-media ABI is specified and implemented.
    """

    name = "ffmpeg-remotion-project-doc"
    version = "1.0.0"

    def __init__(
        self,
        *,
        registry: ComponentRegistry,
        component_runtime: RemotionComponentRuntime,
        base_renderer: ProjectDocFFmpegRenderer | None = None,
        keep_intermediates: bool = False,
    ) -> None:
        self._registry = registry
        self._runtime = component_runtime
        self._base = base_renderer or ProjectDocFFmpegRenderer()
        self._keep_intermediates = keep_intermediates

    def validate(
        self,
        document: ProjectDoc,
        output_range: FrameRange | None = None,
    ) -> None:
        render_range = _render_range(document, output_range)
        overlays = self._overlays(document, render_range)
        if overlays:
            try:
                self._runtime.validate_available()
            except RemotionComponentRuntimeError as exc:
                raise ProjectRenderUnsupported(str(exc)) from exc
        self._base.validate(_base_document(document), output_range)

    def render(
        self,
        document: ProjectDoc,
        quality: Quality,
        out_path: Path,
        sources: dict[str, Path],
        output_range: FrameRange | None = None,
    ) -> RenderResult:
        started = time.perf_counter()
        render_range = _render_range(document, output_range)
        overlays = self._overlays(document, render_range)
        if not overlays:
            return self._base.render(
                document,
                quality,
                out_path,
                sources,
                output_range,
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix=f"aive_remotion_{document.revision_id}_"))
        try:
            base_path = workdir / "base.mp4"
            self._base.render(
                _base_document(document),
                quality,
                base_path,
                sources,
                output_range,
            )
            width, height = _video_dimensions(base_path)
            fps = Fraction(
                document.timeline.output.fps.num,
                document.timeline.output.fps.den,
            )
            rendered: list[tuple[_ComponentOverlay, Path]] = []
            for index, overlay in enumerate(overlays):
                overlay_path = workdir / f"overlay_{index:04d}.mov"
                try:
                    self._runtime.render(
                        ComponentRenderRequest(
                            component=overlay.component,
                            component_props=overlay.props,
                            aive_context=overlay.context,
                            duration_in_frames=overlay.duration_frames,
                            fps=float(fps),
                            width=width,
                            height=height,
                        ),
                        overlay_path,
                    )
                except RemotionComponentRuntimeError as exc:
                    raise ProjectRenderUnsupported(str(exc)) from exc
                rendered.append((overlay, overlay_path))
            _run(
                _overlay_command(
                    base_path,
                    tuple(rendered),
                    out_path,
                    fps,
                    quality,
                )
            )
        finally:
            if not self._keep_intermediates:
                shutil.rmtree(workdir, ignore_errors=True)

        return RenderResult(
            path=out_path,
            quality=quality,
            duration_sec=probe_duration(out_path),
            render_time_ms=int((time.perf_counter() - started) * 1_000),
            file_size=out_path.stat().st_size,
        )

    def _overlays(
        self,
        document: ProjectDoc,
        render_range: FrameRange,
    ) -> tuple[_ComponentOverlay, ...]:
        overlays: list[_ComponentOverlay] = []
        for track in document.timeline.tracks:
            if not track.enabled:
                continue
            if isinstance(track, CaptionTrack):
                if (
                    track.style.component_name == CLEAN_CAPTION_COMPONENT
                    and track.style.component_version == CLEAN_CAPTION_VERSION
                ):
                    continue
                component = self._component(
                    "component",
                    track.style.component_name,
                    track.style.component_version,
                    expected_usage="caption",
                )
                _validate_props(component, track.style.parameters)
                for cue in track.cues:
                    overlap = _overlap(cue.output, render_range)
                    if overlap is None:
                        continue
                    overlays.append(
                        _ComponentOverlay(
                            component=component,
                            props=dict(track.style.parameters),
                            context={
                                "usage": "caption",
                                "elementId": cue.cue_id,
                                "trackId": track.track_id,
                                "revisionId": document.revision_id,
                                "text": cue.text,
                                "speaker": cue.speaker,
                                "timelineStartFrame": cue.output.start_frame,
                                "visibleStartFrame": overlap.start_frame,
                            },
                            start_frame=overlap.start_frame - render_range.start_frame,
                            duration_frames=overlap.duration_frames,
                        )
                    )
            elif isinstance(track, GraphicTrack):
                for graphic in track.graphics:
                    overlap = _overlap(graphic.output, render_range)
                    if overlap is None:
                        continue
                    component = self._component(
                        "component",
                        graphic.component_name,
                        graphic.component_version,
                        expected_usage="graphic",
                    )
                    _validate_props(component, graphic.props)
                    overlays.append(
                        _ComponentOverlay(
                            component=component,
                            props=dict(graphic.props),
                            context={
                                "usage": "graphic",
                                "elementId": graphic.graphic_id,
                                "trackId": track.track_id,
                                "revisionId": document.revision_id,
                                "timelineStartFrame": graphic.output.start_frame,
                                "visibleStartFrame": overlap.start_frame,
                            },
                            start_frame=overlap.start_frame - render_range.start_frame,
                            duration_frames=overlap.duration_frames,
                        )
                    )
        return tuple(overlays)

    def _component(
        self,
        kind: str,
        name: str,
        version: str,
        *,
        expected_usage: str,
    ) -> PublishedComponent:
        component = self._registry.get(kind, name, version)
        if component is None:
            raise ProjectRenderUnsupported(
                f"{kind} capability {name!r} at version {version!r} is not published"
            )
        if component.manifest.usage != expected_usage:
            raise ProjectRenderUnsupported(
                f"component {name!r} is published for {component.manifest.usage!r}, "
                f"not {expected_usage!r}"
            )
        return component


def _base_document(document: ProjectDoc) -> ProjectDoc:
    tracks = []
    for track in document.timeline.tracks:
        if isinstance(track, GraphicTrack):
            continue
        if isinstance(track, CaptionTrack) and not (
            track.style.component_name == CLEAN_CAPTION_COMPONENT
            and track.style.component_version == CLEAN_CAPTION_VERSION
        ):
            continue
        tracks.append(track)
    return document.model_copy(
        update={
            "timeline": document.timeline.model_copy(update={"tracks": tuple(tracks)})
        }
    )


def _render_range(document: ProjectDoc, value: FrameRange | None) -> FrameRange:
    result = value or FrameRange(
        start_frame=0,
        duration_frames=document.timeline.duration_frames,
    )
    if result.end_frame > document.timeline.duration_frames:
        raise ProjectRenderUnsupported(
            "requested render range exceeds the ProjectDoc timeline"
        )
    return result


def _overlap(left: FrameRange, right: FrameRange) -> FrameRange | None:
    start = max(left.start_frame, right.start_frame)
    end = min(left.end_frame, right.end_frame)
    if end <= start:
        return None
    return FrameRange(start_frame=start, duration_frames=end - start)


def _validate_props(component: PublishedComponent, props: dict) -> None:
    try:
        Draft202012Validator(component.manifest.parameter_schema).validate(props)
    except ValidationError as exc:
        raise ProjectRenderUnsupported(
            f"invalid {component.manifest.name} parameters: {exc.message}"
        ) from exc


def _video_dimensions(path: Path) -> tuple[int, int]:
    video = next(
        (
            stream
            for stream in probe(path).get("streams", ())
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    if video is None:
        raise ProjectRenderUnsupported("base render has no video stream")
    return int(video["width"]), int(video["height"])


def _overlay_command(
    base: Path,
    overlays: tuple[tuple[_ComponentOverlay, Path], ...],
    output: Path,
    fps: Fraction,
    quality: Quality,
) -> list[str]:
    command = [settings.ffmpeg_bin, "-y", "-i", str(base)]
    for _, path in overlays:
        command.extend(["-i", str(path)])
    filters: list[str] = []
    previous = "[0:v]"
    for index, (overlay, _) in enumerate(overlays, start=1):
        offset = float(Fraction(overlay.start_frame, 1) / fps)
        shifted = f"overlay_in_{index}"
        output_label = f"overlay_out_{index}"
        filters.append(
            f"[{index}:v]format=rgba,setpts=PTS-STARTPTS+{offset:.9f}/TB[{shifted}]"
        )
        filters.append(
            f"{previous}[{shifted}]overlay=0:0:eof_action=pass:shortest=0:"
            f"format=auto[{output_label}]"
        )
        previous = f"[{output_label}]"
    crf, preset = (30, "veryfast") if quality == "proxy" else (18, "medium")
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            previous,
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return command


__all__ = ["ProjectDocRemotionRenderer"]
