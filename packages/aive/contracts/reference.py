"""Reference-remake context stored inside a ProjectDoc revision."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .time import FrameRange, SourceRange


ReferenceDimension = Literal[
    "section_order",
    "duration_pattern",
    "pacing",
    "shot_roles",
    "camera_motion",
    "audio_relationship",
    "text_style",
    "effects",
    "brand",
]


class ReferencePolicy(ContractModel):
    """Which reference dimensions are strict, adaptable, or excluded."""

    strictness: Literal["inspired", "editing", "shot_aligned"] = "editing"
    preserve: tuple[ReferenceDimension, ...] = ()
    adapt: tuple[ReferenceDimension, ...] = ()
    exclude: tuple[ReferenceDimension, ...] = ()
    music_policy: Literal[
        "do_not_copy",
        "user_authorized_track",
        "replace_and_remap",
    ] = "do_not_copy"
    allow_generated_footage: bool = False

    @model_validator(mode="after")
    def dimensions_are_disjoint(self) -> ReferencePolicy:
        groups = {
            "preserve": set(self.preserve),
            "adapt": set(self.adapt),
            "exclude": set(self.exclude),
        }
        duplicates = (groups["preserve"] & groups["adapt"]) | (
            groups["preserve"] & groups["exclude"]
        ) | (groups["adapt"] & groups["exclude"])
        if duplicates:
            joined = ", ".join(sorted(duplicates))
            raise ValueError(f"dimensions appear in more than one policy set: {joined}")
        for name, values in groups.items():
            if len(values) != len(getattr(self, name)):
                raise ValueError(f"duplicate dimensions in {name}")
        return self


class ReferenceAlignment(ContractModel):
    alignment_id: StableId
    reference_section_id: StableId
    reference_range: SourceRange
    output_range: FrameRange
    output_element_ids: tuple[StableId, ...] = Field(min_length=1)
    relation: str = Field(min_length=1, max_length=160)


class AdaptationDecision(ContractModel):
    decision_id: StableId
    reference_section_id: StableId
    reason: str = Field(min_length=1, max_length=500)
    action: str = Field(min_length=1, max_length=160)
    status: Literal["accepted", "needs_confirmation", "rejected"]


class ReferenceContext(ContractModel):
    reference_asset_id: StableId
    blueprint_id: StableId
    blueprint_version: str = Field(min_length=1, max_length=64)
    policy: ReferencePolicy
    alignments: tuple[ReferenceAlignment, ...] = ()
    adaptations: tuple[AdaptationDecision, ...] = ()
