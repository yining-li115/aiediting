"""Exact source and output time primitives for ProjectDoc."""

from __future__ import annotations

from math import gcd
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .base import ContractModel


class Rational(ContractModel):
    """A positive reduced rational such as 30000/1001 or 2/1."""

    num: int = Field(gt=0)
    den: int = Field(gt=0)

    @model_validator(mode="after")
    def reduced(self) -> Rational:
        if gcd(self.num, self.den) != 1:
            raise ValueError("rational values must be reduced")
        return self


class SourceRange(ContractModel):
    """Half-open source interval [start, end) in asset-local integer ticks."""

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    timescale: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> SourceRange:
        if self.end <= self.start:
            raise ValueError("source range end must be greater than start")
        return self

    @property
    def duration_ticks(self) -> int:
        return self.end - self.start


class FrameRange(ContractModel):
    """Half-open output interval expressed in project frames."""

    start_frame: int = Field(ge=0)
    duration_frames: int = Field(gt=0)

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.duration_frames


class TimeMapPoint(ContractModel):
    """One relative mapping point inside a variable-speed clip."""

    output_frame: int = Field(ge=0)
    source_tick: int = Field(ge=0)


class ConstantPlayback(ContractModel):
    kind: Literal["constant"] = "constant"
    rate: Rational = Rational(num=1, den=1)


class VariablePlayback(ContractModel):
    kind: Literal["variable"] = "variable"
    points: tuple[TimeMapPoint, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def monotonic(self) -> VariablePlayback:
        if self.points[0] != TimeMapPoint(output_frame=0, source_tick=0):
            raise ValueError("variable playback must start at output 0 and source 0")
        for previous, current in zip(self.points, self.points[1:]):
            if current.output_frame <= previous.output_frame:
                raise ValueError("variable playback output frames must increase")
            if current.source_tick <= previous.source_tick:
                raise ValueError("variable playback source ticks must increase")
        return self


Playback = Annotated[
    ConstantPlayback | VariablePlayback,
    Field(discriminator="kind"),
]
