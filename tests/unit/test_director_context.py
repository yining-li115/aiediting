from __future__ import annotations

from datetime import datetime, timezone

from aive.agent.context import AgentRunContextBuilder
from aive.agent.tools import DirectorMomentTools, MomentSearchQuery
from aive.contracts.moments import MediaSourceSpan, MomentEvidence
from aive.contracts.project_doc import (
    AssetRef,
    DependencyRef,
    DependencySet,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    Timeline,
)
from aive.contracts.revision import ProjectRevision, RevisionProvenance
from aive.media.repository import AssetProcessingRecord
from aive.project.hashing import canonical_model_hash
from aive.style_reference import build_style_reference_blueprint
from tests.v2_fixtures import source_understanding, style_blueprint_draft
from tests.provider_fakes import FixtureRerankProvider


NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _revision() -> ProjectRevision:
    document = ProjectDoc(
        project_id="project_context",
        revision_id="revision_assets",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=120,
            prompt="Create an energetic food story with a strong opening",
            explicit_requirements=("Keep the chef's explanation",),
            style_tags=("warm", "fast"),
        ),
        assets=(
            AssetRef(asset_id="asset_b", kind="video", label="plating.mp4"),
            AssetRef(asset_id="asset_a", kind="video", label="chef.mp4"),
        ),
        timeline=Timeline(
            duration_frames=120,
            output=OutputSettings(
                width=1080,
                height=1920,
                fps=Rational(num=30, den=1),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1")
        ),
    )
    return ProjectRevision(
        commit_id="commit_assets",
        created_at=NOW,
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(kind="initial", origin="system"),
        document=document,
    )


def _status(asset_id: str, has_audio: bool) -> AssetProcessingRecord:
    return AssetProcessingRecord(
        asset_id=asset_id,
        project_id="project_context",
        storage_key=f"project_context/original/{asset_id}.mp4",
        kind="video",
        original_name=f"{asset_id}.mp4",
        duration_seconds=4,
        width=1080,
        height=1920,
        fps=30,
        has_audio=has_audio,
        readiness="ready",
        analysis_readiness="ready",
    )


def _moment(
    moment_id: str,
    asset_id: str,
    ordinal: int,
    semantic: str,
    *,
    speech: bool = False,
) -> MomentEvidence:
    source = MediaSourceSpan(
        start=ordinal * 1_000,
        end=(ordinal + 1) * 1_000,
    )
    shot_id = f"shot_{moment_id}"
    return MomentEvidence(
        moment_id=moment_id,
        project_id="project_context",
        asset_id=asset_id,
        ordinal=ordinal,
        shot_ids=(shot_id,),
        source=source,
        analysis_window_count=1,
        keyframe_count=1,
        understanding=source_understanding(
            shot_ids=(shot_id,),
            source=source,
            summary=semantic,
            narrative_function=(
                "Open the sequence" if ordinal == 0 else "Develop the sequence"
            ),
        ),
        has_speech=speech,
        asr_text="The chef explains the dish" if speech else None,
    )


def test_context_builder_freezes_revision_media_and_moment_evidence() -> None:
    revision = _revision()
    moments = (
        _moment("moment_a", "asset_a", 1, "Chef explains the dish", speech=True),
        _moment("moment_b", "asset_b", 0, "Energetic plating close-up"),
    )

    context = AgentRunContextBuilder().build(
        revision=revision,
        asset_statuses=(_status("asset_a", True), _status("asset_b", False)),
        moments=moments,
    )

    assert context.base_revision_id == "revision_assets"
    assert context.document_hash == revision.document_hash
    assert [asset.asset_id for asset in context.assets] == ["asset_b", "asset_a"]
    assert context.assets[1].has_audio is True
    assert [moment.moment_id for moment in context.moments] == [
        "moment_b",
        "moment_a",
    ]


async def test_moment_tools_search_and_inspect_only_frozen_context() -> None:
    context = AgentRunContextBuilder().build(
        revision=_revision(),
        asset_statuses=(_status("asset_a", True), _status("asset_b", False)),
        moments=(
            _moment("moment_a", "asset_a", 1, "Chef explains the dish", speech=True),
            _moment("moment_b", "asset_b", 0, "Energetic plating close-up"),
        ),
    )
    tools = DirectorMomentTools(
        context,
        search_provider=FixtureRerankProvider(),
    )

    hits = await tools.search_moments(
        MomentSearchQuery(text="chef explanation", require_speech=True, limit=5)
    )

    assert [hit.moment_id for hit in hits] == ["moment_a"]
    assert tools.inspect_media(("moment_a",))[0].asr_text == (
        "The chef explains the dish"
    )


def test_style_reference_evidence_is_frozen_but_never_selectable() -> None:
    revision = _revision()
    style_asset = AssetRef(
        asset_id="asset_style",
        kind="video",
        label="style-reference.mp4",
        roles=("reference", "style_reference"),
    )
    document = revision.document.model_copy(
        update={"assets": (*revision.document.assets, style_asset)}
    )
    revision = revision.model_copy(
        update={
            "document": document,
            "document_hash": canonical_model_hash(document),
        }
    )
    style_status = AssetProcessingRecord(
        **{
            **_status("asset_a", False).__dict__,
            "asset_id": "asset_style",
            "original_name": "style-reference.mp4",
        }
    )
    source_moment = _moment(
        "moment_source",
        "asset_a",
        0,
        "Source footage",
    )
    style_moment = _moment(
        "moment_style",
        "asset_style",
        0,
        "Reference-only footage",
    )
    blueprint = build_style_reference_blueprint(
        style_status,
        (style_moment,),
        style_blueprint_draft(moment_ids=(style_moment.moment_id,)),
    )

    context = AgentRunContextBuilder().build(
        revision=revision,
        asset_statuses=(
            _status("asset_a", False),
            _status("asset_b", False),
            style_status,
        ),
        moments=(source_moment, style_moment),
        style_reference_blueprints=(blueprint,),
    )

    assert [asset.asset_id for asset in context.assets] == ["asset_b", "asset_a"]
    assert [moment.moment_id for moment in context.moments] == ["moment_source"]
    assert context.style_reference_blueprints == (blueprint,)
