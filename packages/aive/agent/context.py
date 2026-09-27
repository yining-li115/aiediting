"""Build the frozen, model-safe context for one Director run."""

from __future__ import annotations

from ..contracts.audio import AudioAssetAnalysis
from ..contracts.director import AgentAssetContext, AgentRunContext
from ..contracts.moments import MomentEvidence
from ..contracts.revision import ProjectRevision
from ..contracts.style_reference import StyleReferenceBlueprint
from ..media.repository import AssetProcessingRecord


class DirectorContextError(ValueError):
    pass


class AgentRunContextBuilder:
    """Snapshot revision intent and derived evidence without creating edit state."""

    def build(
        self,
        *,
        revision: ProjectRevision,
        asset_statuses: tuple[AssetProcessingRecord, ...],
        moments: tuple[MomentEvidence, ...],
        style_reference_blueprints: tuple[StyleReferenceBlueprint, ...] = (),
        audio_asset_analyses: tuple[AudioAssetAnalysis, ...] = (),
    ) -> AgentRunContext:
        document = revision.document
        statuses = {status.asset_id: status for status in asset_statuses}
        audio_analyses: dict[str, AudioAssetAnalysis] = {}
        for analysis in audio_asset_analyses:
            if analysis.project_id != document.project_id:
                raise DirectorContextError(
                    f"audio analysis for {analysis.asset_id!r} belongs to another project"
                )
            if analysis.asset_id in audio_analyses:
                raise DirectorContextError(
                    f"duplicate audio analysis for Asset {analysis.asset_id!r}"
                )
            audio_analyses[analysis.asset_id] = analysis
        source_order = {
            asset.asset_id: index
            for index, asset in enumerate(document.assets)
            if "source" in asset.roles
        }
        assets: list[AgentAssetContext] = []
        for asset in document.assets:
            if not ({"source", "music", "sfx"} & set(asset.roles)):
                continue
            status = statuses.get(asset.asset_id)
            if status is None:
                raise DirectorContextError(
                    f"source Asset {asset.asset_id!r} has no runtime media record"
                )
            if status.project_id != document.project_id:
                raise DirectorContextError(
                    f"source Asset {asset.asset_id!r} belongs to another project"
                )
            needs_analysis = "source" in asset.roles
            if status.readiness != "ready" or (
                needs_analysis
                and status.analysis_readiness not in {"ready", "partial"}
            ):
                raise DirectorContextError(
                    f"source Asset {asset.asset_id!r} is not ready for Director use"
                )
            assets.append(
                AgentAssetContext(
                    asset_id=asset.asset_id,
                    kind=asset.kind,
                    roles=asset.roles,
                    label=asset.label or status.original_name,
                    duration_seconds=status.duration_seconds,
                    width=status.width,
                    height=status.height,
                    fps=status.fps,
                    has_audio=status.has_audio,
                    analysis_readiness=(
                        status.analysis_readiness if needs_analysis else "ready"
                    ),
                    audio_analysis=(
                        audio_analyses.get(asset.asset_id) or status.audio_analysis
                    ),
                )
            )

        if not assets:
            raise DirectorContextError("Director context requires at least one source Asset")

        scoped: list[MomentEvidence] = []
        for moment in moments:
            if moment.project_id != document.project_id:
                raise DirectorContextError(
                    f"Moment {moment.moment_id!r} belongs to another project"
                )
            if moment.asset_id not in source_order:
                continue
            scoped.append(moment)
        scoped.sort(
            key=lambda moment: (
                source_order[moment.asset_id],
                moment.ordinal,
                moment.source.start,
                moment.moment_id,
            )
        )
        if not scoped:
            raise DirectorContextError("Director context requires analyzed Moments")

        return AgentRunContext(
            project_id=document.project_id,
            base_revision_id=document.revision_id,
            document_hash=revision.document_hash,
            intent=document.intent,
            output=document.timeline.output,
            assets=tuple(assets),
            moments=tuple(scoped),
            style_reference_blueprints=style_reference_blueprints,
            locked_target_ids=tuple(lock.target_id for lock in document.locks),
        )
