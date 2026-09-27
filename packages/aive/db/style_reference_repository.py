"""SQL adapter for immutable Style Reference Blueprints."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..contracts.style_reference import StyleReferenceBlueprint
from ..media.repository import ProjectAssetNotFound
from ..project.repository import ProjectNotFound
from ..style_reference import StyleReferenceBlueprintRepository
from .models import Asset, Project, StyleReferenceBlueprintRow, utcnow


class StyleReferenceBlueprintConflict(RuntimeError):
    pass


class SqlAlchemyStyleReferenceBlueprintRepository(
    StyleReferenceBlueprintRepository
):
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.session_factory = sessions

    def save(
        self,
        blueprint: StyleReferenceBlueprint,
    ) -> StyleReferenceBlueprint:
        with self.session_factory.begin() as session:
            if session.get(Project, blueprint.project_id) is None:
                raise ProjectNotFound(
                    f"project {blueprint.project_id!r} does not exist"
                )
            asset = session.get(Asset, blueprint.asset_id)
            if asset is None or asset.project_id != blueprint.project_id:
                raise ProjectAssetNotFound(
                    f"asset {blueprint.asset_id!r} is not registered to "
                    f"project {blueprint.project_id!r}"
                )
            existing = session.get(StyleReferenceBlueprintRow, blueprint.blueprint_id)
            if existing is not None:
                stored = StyleReferenceBlueprint.model_validate(existing.blueprint_json)
                if stored != blueprint:
                    raise StyleReferenceBlueprintConflict(
                        f"Blueprint id {blueprint.blueprint_id!r} already has "
                        "different immutable content"
                    )
                return stored
            same_evidence = session.scalar(
                select(StyleReferenceBlueprintRow).where(
                    StyleReferenceBlueprintRow.project_id == blueprint.project_id,
                    StyleReferenceBlueprintRow.asset_id == blueprint.asset_id,
                    StyleReferenceBlueprintRow.evidence_hash == blueprint.evidence_hash,
                )
            )
            if same_evidence is not None:
                stored = StyleReferenceBlueprint.model_validate(
                    same_evidence.blueprint_json
                )
                if stored != blueprint:
                    raise StyleReferenceBlueprintConflict(
                        "Style evidence hash already has different immutable content"
                    )
                return stored
            session.add(
                StyleReferenceBlueprintRow(
                    id=blueprint.blueprint_id,
                    project_id=blueprint.project_id,
                    asset_id=blueprint.asset_id,
                    blueprint_version=blueprint.blueprint_version,
                    evidence_hash=blueprint.evidence_hash,
                    blueprint_json=blueprint.model_dump(mode="json"),
                    created_at=utcnow(),
                )
            )
            session.flush()
            return blueprint

    def get(
        self,
        project_id: str,
        blueprint_id: str,
    ) -> StyleReferenceBlueprint | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(StyleReferenceBlueprintRow).where(
                    StyleReferenceBlueprintRow.id == blueprint_id,
                    StyleReferenceBlueprintRow.project_id == project_id,
                )
            )
            if row is None:
                return None
            return StyleReferenceBlueprint.model_validate(row.blueprint_json)
