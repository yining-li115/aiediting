"""Versioned parameter contracts for executable caption components."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from .base import ContractModel
from .timeline import CaptionStyle


CLEAN_CAPTION_COMPONENT = "caption.clean"
CLEAN_CAPTION_VERSION = "1.0.0"
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


class CleanCaptionParameters(ContractModel):
    """Safe, renderer-neutral parameters for the first caption component."""

    font_family: str = Field(default="Arial", min_length=1, max_length=128)
    font_size: int = Field(default=56, ge=12, le=240)
    font_weight: str = Field(default="bold", pattern=r"^(regular|bold)$")
    text_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: int = Field(default=3, ge=0, le=16)
    background_color: str = "#00000099"
    bottom_margin: int = Field(default=96, ge=0, le=2_000)
    horizontal_padding: int = Field(default=28, ge=0, le=300)
    vertical_padding: int = Field(default=16, ge=0, le=200)
    line_spacing: int = Field(default=8, ge=0, le=100)
    corner_radius: int = Field(default=18, ge=0, le=200)
    max_width_ratio: float = Field(default=0.84, ge=0.25, le=1.0)
    text_align: str = Field(default="center", pattern=r"^(left|center)$")

    @field_validator("text_color", "stroke_color", "background_color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if not _COLOR.fullmatch(value):
            raise ValueError("caption colors must be #RRGGBB or #RRGGBBAA")
        return value.upper()


def clean_caption_parameters(style: CaptionStyle) -> CleanCaptionParameters:
    if (
        style.component_name != CLEAN_CAPTION_COMPONENT
        or style.component_version != CLEAN_CAPTION_VERSION
    ):
        raise ValueError(
            "caption component "
            f"{style.component_name!r} at version {style.component_version!r} "
            "is not installed"
        )
    return CleanCaptionParameters.model_validate(style.parameters)


def default_caption_style(style_id: str) -> CaptionStyle:
    return CaptionStyle(
        style_id=style_id,
        component_name=CLEAN_CAPTION_COMPONENT,
        component_version=CLEAN_CAPTION_VERSION,
        parameters=CleanCaptionParameters().model_dump(mode="json"),
    )


__all__ = [
    "CLEAN_CAPTION_COMPONENT",
    "CLEAN_CAPTION_VERSION",
    "CleanCaptionParameters",
    "clean_caption_parameters",
    "default_caption_style",
]
