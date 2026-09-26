"""Rasterize trusted caption components for FFmpeg composition."""

from __future__ import annotations

import subprocess
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..contracts.captions import CleanCaptionParameters


class CaptionRasterError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaptionOverlay:
    path: Path
    x: int
    y: int


def rasterize_clean_caption(
    *,
    text: str,
    parameters: CleanCaptionParameters,
    width: int,
    height: int,
    scale: float,
    output: Path,
) -> CaptionOverlay:
    """Render one transparent cue image; timing stays in ProjectDoc/FFmpeg."""

    font_size = max(12, round(parameters.font_size * scale))
    stroke_width = max(0, round(parameters.stroke_width * scale))
    horizontal_padding = max(0, round(parameters.horizontal_padding * scale))
    vertical_padding = max(0, round(parameters.vertical_padding * scale))
    line_spacing = max(0, round(parameters.line_spacing * scale))
    corner_radius = max(0, round(parameters.corner_radius * scale))
    bottom_margin = max(0, round(parameters.bottom_margin * scale))
    font = _font(parameters.font_family, parameters.font_weight, font_size)
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    max_text_width = max(
        1,
        round(width * parameters.max_width_ratio) - 2 * horizontal_padding,
    )
    wrapped = _wrap_text(draw, text, font, max_text_width, stroke_width)
    align = parameters.text_align
    bounds = draw.multiline_textbbox(
        (0, 0),
        wrapped,
        font=font,
        spacing=line_spacing,
        align=align,
        stroke_width=stroke_width,
    )
    text_width = max(1, math.ceil(bounds[2] - bounds[0]))
    text_height = max(1, math.ceil(bounds[3] - bounds[1]))
    image_width = min(width, text_width + 2 * horizontal_padding)
    image_height = min(height, text_height + 2 * vertical_padding)
    image = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    painter = ImageDraw.Draw(image)
    background = _rgba(parameters.background_color)
    if background[3]:
        painter.rounded_rectangle(
            (0, 0, image_width - 1, image_height - 1),
            radius=min(corner_radius, image_height // 2),
            fill=background,
        )
    painter.multiline_text(
        (horizontal_padding - bounds[0], vertical_padding - bounds[1]),
        wrapped,
        font=font,
        fill=_rgba(parameters.text_color),
        spacing=line_spacing,
        align=align,
        stroke_width=stroke_width,
        stroke_fill=_rgba(parameters.stroke_color),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")
    return CaptionOverlay(
        path=output,
        x=max(0, (width - image_width) // 2),
        y=max(0, height - bottom_margin - image_height),
    )


@lru_cache(maxsize=64)
def _font(family: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    query = f"{family}:style={'Bold' if weight == 'bold' else 'Regular'}"
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", query],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise CaptionRasterError(
            "fontconfig is required to resolve registered caption fonts"
        ) from exc
    path = Path(result.stdout.strip())
    if result.returncode != 0 or not path.is_file():
        raise CaptionRasterError(f"caption font {family!r} is not installed")
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError as exc:
        raise CaptionRasterError(
            f"caption font {family!r} could not be loaded"
        ) from exc


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    stroke_width: int,
) -> str:
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        tokens = paragraph.split(" ") if " " in paragraph else list(paragraph)
        separator = " " if " " in paragraph else ""
        current = ""
        for token in tokens:
            candidate = token if not current else f"{current}{separator}{token}"
            bounds = draw.textbbox(
                (0, 0),
                candidate,
                font=font,
                stroke_width=stroke_width,
            )
            if current and bounds[2] - bounds[0] > max_width:
                lines.append(current)
                current = token
            else:
                current = candidate
        lines.append(current or " ")
    return "\n".join(lines)


def _rgba(value: str) -> tuple[int, int, int, int]:
    raw = value.removeprefix("#")
    if len(raw) == 6:
        raw += "FF"
    return tuple(int(raw[index : index + 2], 16) for index in range(0, 8, 2))  # type: ignore[return-value]


__all__ = ["CaptionOverlay", "CaptionRasterError", "rasterize_clean_caption"]
