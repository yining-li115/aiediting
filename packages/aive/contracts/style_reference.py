"""Closed contract for model-authored transferable Style grammar."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, StableId
from .media import DatabaseId
from .moments import MediaSourceSpan, ModelProvenance


class StyleEvidence(ContractModel):
    """A style observation tied to supplied reference evidence, never content."""

    evidence_id: str = Field(min_length=1, max_length=512)
    description: str = Field(min_length=1, max_length=1_000)
    source: MediaSourceSpan | None = None
    moment_ids: tuple[StableId, ...] = ()


class StylePattern(ContractModel):
    """One transferable model-authored rule in a free creative vocabulary."""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    application: str = Field(min_length=1, max_length=2_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class TemporalStylePhase(ContractModel):
    """One relative phase in the reference's editing progression."""

    order: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1_000)
    rhythm_description: str = Field(min_length=1, max_length=1_000)
    progression: str = Field(min_length=1, max_length=1_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class ObjectiveStyleEvidence(ContractModel):
    """Facts a style model observed; none is an editorial conclusion."""

    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    duration_seconds: float = Field(gt=0)
    moment_count: int = Field(ge=1)


class StyleBlueprintDraft(ContractModel):
    """Provider answer before project identity and evidence hash are attached."""

    summary: str = Field(min_length=1, max_length=4_000)
    temporal_structure: tuple[TemporalStylePhase, ...] = Field(min_length=1)
    narrative_grammar: tuple[StylePattern, ...] = ()
    shot_grammar: tuple[StylePattern, ...] = ()
    transition_grammar: tuple[StylePattern, ...] = ()
    motion_grammar: tuple[StylePattern, ...] = ()
    color_and_composition: tuple[StylePattern, ...] = ()
    audio_visual_relationship: tuple[StylePattern, ...] = ()
    caption_and_graphic_language: tuple[StylePattern, ...] = ()
    transferable_principles: tuple[str, ...] = Field(min_length=1, max_length=32)
    avoid_copying: tuple[str, ...] = Field(min_length=1, max_length=32)
    evidence: tuple[StyleEvidence, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=16)
    provenance: ModelProvenance


class StyleReferenceBlueprint(ContractModel):
    """Immutable provider-authored grammar extracted from one Style Reference."""

    blueprint_version: Literal["2.0"] = "2.0"
    blueprint_id: StableId
    project_id: DatabaseId
    asset_id: DatabaseId
    evidence_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[A-Fa-f0-9]+$",
    )
    summary: str = Field(min_length=1, max_length=4_000)
    objective_evidence: ObjectiveStyleEvidence
    temporal_structure: tuple[TemporalStylePhase, ...] = Field(min_length=1)
    narrative_grammar: tuple[StylePattern, ...] = ()
    shot_grammar: tuple[StylePattern, ...] = ()
    transition_grammar: tuple[StylePattern, ...] = ()
    motion_grammar: tuple[StylePattern, ...] = ()
    color_and_composition: tuple[StylePattern, ...] = ()
    audio_visual_relationship: tuple[StylePattern, ...] = ()
    caption_and_graphic_language: tuple[StylePattern, ...] = ()
    transferable_principles: tuple[str, ...] = Field(min_length=1, max_length=32)
    avoid_copying: tuple[str, ...] = Field(min_length=1, max_length=32)
    evidence: tuple[StyleEvidence, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    uncertainty: tuple[str, ...] = Field(default=(), max_length=16)
    provenance: ModelProvenance

    @model_validator(mode="after")
    def references_are_closed(self) -> StyleReferenceBlueprint:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Style Blueprint evidence IDs must be unique")
        known = set(evidence_ids)
        referenced: set[str] = set()
        pattern_groups = (
            self.narrative_grammar,
            self.shot_grammar,
            self.transition_grammar,
            self.motion_grammar,
            self.color_and_composition,
            self.audio_visual_relationship,
            self.caption_and_graphic_language,
        )
        for pattern in (
            item for group in pattern_groups for item in group
        ):
            referenced.update(pattern.evidence_ids)
        for phase in self.temporal_structure:
            referenced.update(phase.evidence_ids)
        missing = referenced - known
        if missing:
            raise ValueError(
                f"Style Blueprint references unknown evidence {sorted(missing)!r}"
            )
        orders = tuple(phase.order for phase in self.temporal_structure)
        if orders != tuple(range(len(orders))):
            raise ValueError("Style temporal phases must be ordered from zero")
        moment_ids = {
            moment_id for item in self.evidence for moment_id in item.moment_ids
        }
        if not moment_ids:
            raise ValueError("Style Blueprint must cite analyzed reference Moments")
        return self


__all__ = [
    "ObjectiveStyleEvidence",
    "StyleEvidence",
    "StyleBlueprintDraft",
    "StylePattern",
    "StyleReferenceBlueprint",
    "TemporalStylePhase",
]
