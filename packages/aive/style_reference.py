"""Immutable Style Reference identity, validation, and persistence ports."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from .contracts.moments import MomentEvidence
from .contracts.style_reference import (
    ObjectiveStyleEvidence,
    StyleBlueprintDraft,
    StyleReferenceBlueprint,
)
from .media.repository import AssetProcessingRecord


class StyleReferenceUnavailable(ValueError):
    pass


class StyleReferenceBlueprintRepository(Protocol):
    """Insert-only persistence for content-addressed derived evidence."""

    def save(
        self,
        blueprint: StyleReferenceBlueprint,
    ) -> StyleReferenceBlueprint: ...

    def get(
        self,
        project_id: str,
        blueprint_id: str,
    ) -> StyleReferenceBlueprint | None: ...


def style_reference_inputs(
    asset: AssetProcessingRecord,
    moments: tuple[MomentEvidence, ...],
) -> tuple[ObjectiveStyleEvidence, tuple[MomentEvidence, ...]]:
    """Close the objective evidence shown to a Style model."""

    if asset.kind != "video":
        raise StyleReferenceUnavailable("Style Reference must be a video Asset")
    if asset.readiness != "ready" or asset.analysis_readiness != "ready":
        raise StyleReferenceUnavailable(
            f"Style Reference Asset {asset.asset_id!r} is not fully understood"
        )
    ordered = tuple(
        sorted(
            (
                moment
                for moment in moments
                if moment.asset_id == asset.asset_id
                and moment.project_id == asset.project_id
            ),
            key=lambda moment: (
                moment.ordinal,
                moment.source.start,
                moment.source.end,
                moment.moment_id,
            ),
        )
    )
    if not ordered:
        raise StyleReferenceUnavailable(
            f"Style Reference Asset {asset.asset_id!r} has no analyzed Moments"
        )
    objective = ObjectiveStyleEvidence(
        source_width=asset.width,
        source_height=asset.height,
        duration_seconds=asset.duration_seconds,
        moment_count=len(ordered),
    )
    return objective, ordered


def style_reference_evidence_hash(
    asset: AssetProcessingRecord,
    moments: tuple[MomentEvidence, ...],
) -> str:
    objective, ordered = style_reference_inputs(asset, moments)
    payload = {
        "blueprint_version": "2.0",
        "project_id": asset.project_id,
        "asset_id": asset.asset_id,
        "objective": objective.model_dump(mode="json"),
        "moments": [moment.model_dump(mode="json") for moment in ordered],
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def style_reference_blueprint_id(evidence_hash: str) -> str:
    return f"style_blueprint_{evidence_hash[:32]}"


def build_style_reference_blueprint(
    asset: AssetProcessingRecord,
    moments: tuple[MomentEvidence, ...],
    draft: StyleBlueprintDraft,
) -> StyleReferenceBlueprint:
    """Attach trusted identity to a provider answer without inferring style."""

    objective, ordered = style_reference_inputs(asset, moments)
    _validate_draft(draft, ordered)
    evidence_hash = style_reference_evidence_hash(asset, moments)
    return StyleReferenceBlueprint(
        blueprint_id=style_reference_blueprint_id(evidence_hash),
        project_id=asset.project_id,
        asset_id=asset.asset_id,
        evidence_hash=evidence_hash,
        objective_evidence=objective,
        **draft.model_dump(),
    )


def _validate_draft(
    draft: StyleBlueprintDraft,
    moments: tuple[MomentEvidence, ...],
) -> None:
    by_id = {moment.moment_id: moment for moment in moments}
    for evidence in draft.evidence:
        if not evidence.moment_ids:
            raise StyleReferenceUnavailable(
                "Style model evidence must cite at least one supplied Moment"
            )
        unknown = set(evidence.moment_ids) - set(by_id)
        if unknown:
            raise StyleReferenceUnavailable(
                f"Style model cited unknown Moments {sorted(unknown)!r}"
            )
        if evidence.source is None:
            continue
        cited = tuple(by_id[moment_id] for moment_id in evidence.moment_ids)
        timescales = {moment.source.timescale for moment in cited}
        if timescales != {evidence.source.timescale}:
            raise StyleReferenceUnavailable(
                "Style model evidence uses a different source timescale"
            )
        if evidence.source.start < min(item.source.start for item in cited) or (
            evidence.source.end > max(item.source.end for item in cited)
        ):
            raise StyleReferenceUnavailable(
                "Style model evidence range is outside its cited Moments"
            )


__all__ = [
    "StyleReferenceBlueprintRepository",
    "StyleReferenceUnavailable",
    "build_style_reference_blueprint",
    "style_reference_blueprint_id",
    "style_reference_evidence_hash",
    "style_reference_inputs",
]
