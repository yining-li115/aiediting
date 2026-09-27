from __future__ import annotations

from datetime import datetime, timezone

from aive.agent.review import FirstCutReviewer
from aive.contracts.audio import (
    AudioAssetAnalysis,
    AudioEnergySegment,
    AudioSignalAnalysis,
    AudioVisualSyncDecision,
)
from aive.contracts.director import (
    AgentAssetContext,
    AgentRunContext,
    EditBlueprint,
    EditSectionDecision,
    FirstCutDecision,
    MusicEditDecision,
    SoundEffectEditDecision,
    VisualEditDecision,
)
from aive.contracts.moments import AudioUnderstanding, MediaSourceSpan, MomentEvidence
from aive.contracts.project_doc import OutputSettings, ProjectIntent, Rational
from aive.contracts.time import FrameRange, SourceRange
from tests.v2_fixtures import FIXTURE_PROVENANCE, source_understanding


def _analysis(asset_id: str, *, sfx: bool = False) -> AudioAssetAnalysis:
    return AudioAssetAnalysis(
        project_id="project_sync",
        asset_id=asset_id,
        content_hash="a" * 64,
        basis_hash=("c" if sfx else "b") * 64,
        signal=AudioSignalAnalysis(
            duration_ms=2_000,
            sample_rate=16_000,
            integrated_rms_dbfs=-18,
            peak_dbfs=-1,
            bpm=None if sfx else 120,
            beat_confidence=0 if sfx else 0.8,
            beat_times_ms=() if sfx else (0, 500, 1_000, 1_500),
            accent_times_ms=() if sfx else (0, 1_000),
            transient_times_ms=(500,) if sfx else (0, 500, 1_000, 1_500),
            energy_curve=(
                AudioEnergySegment(
                    source=MediaSourceSpan(start=0, end=2_000),
                    rms_dbfs=-18,
                    peak_dbfs=-1,
                    relative_energy=0.5,
                ),
            ),
            detector="fixture-signal-v2",
        ),
        understandings=(
            AudioUnderstanding(
                evidence_id=f"evidence_{asset_id}",
                source=MediaSourceSpan(start=0, end=2_000),
                summary="Measured fixture audio",
                sound_events=("impact",) if sfx else (),
                music_description=None if sfx else "steady instrumental pulse",
                editorial_value="Supports the visual event",
                confidence=0.9,
                provenance=FIXTURE_PROVENANCE,
            ),
        ),
        analyzed_at=datetime.now(timezone.utc),
    )


def _context() -> AgentRunContext:
    moment = MomentEvidence(
        moment_id="moment_action",
        project_id="project_sync",
        asset_id="asset_video",
        ordinal=0,
        shot_ids=("shot_action",),
        source=MediaSourceSpan(start=0, end=2_000),
        analysis_window_count=1,
        keyframe_count=1,
        understanding=source_understanding(
            shot_ids=("shot_action",),
            source=MediaSourceSpan(start=0, end=2_000),
            summary="A visible action lands at the cut",
        ),
    )
    return AgentRunContext(
        project_id="project_sync",
        base_revision_id="revision_sync",
        document_hash="d" * 64,
        intent=ProjectIntent(task_mode="autonomous", target_duration_frames=60),
        output=OutputSettings(
            width=1080,
            height=1920,
            fps=Rational(num=30, den=1),
        ),
        assets=(
            AgentAssetContext(
                asset_id="asset_video",
                kind="video",
                roles=("source",),
                duration_seconds=2,
                width=1080,
                height=1920,
                fps=30,
                has_audio=True,
                analysis_readiness="ready",
            ),
            AgentAssetContext(
                asset_id="asset_music",
                kind="audio",
                roles=("music",),
                duration_seconds=2,
                width=1,
                height=1,
                fps=0,
                has_audio=True,
                analysis_readiness="ready",
                audio_analysis=_analysis("asset_music"),
            ),
            AgentAssetContext(
                asset_id="asset_sfx",
                kind="audio",
                roles=("sfx",),
                duration_seconds=2,
                width=1,
                height=1,
                fps=0,
                has_audio=True,
                analysis_readiness="ready",
                audio_analysis=_analysis("asset_sfx", sfx=True),
            ),
        ),
        moments=(moment,),
    )


def _decision(*, music_event_ms: int = 1_000) -> FirstCutDecision:
    return FirstCutDecision(
        summary="Beat-grounded cut",
        explanation="The cut and impact follow measured audio events.",
        edit_blueprint=EditBlueprint(
            objective="Synchronize picture and sound deliberately",
            narrative_summary="Two visual phrases meet on one musical accent.",
            output_duration_frames=60,
            sections=(
                EditSectionDecision(
                    section_key="section_story",
                    output=FrameRange(start_frame=0, duration_frames=60),
                    role="story",
                    goal="Build and land",
                ),
            ),
            visuals=(
                VisualEditDecision(
                    decision_key="visual_a",
                    moment_id="moment_action",
                    source=MediaSourceSpan(start=0, end=1_000),
                    output=FrameRange(start_frame=0, duration_frames=30),
                    purpose="Build",
                ),
                VisualEditDecision(
                    decision_key="visual_b",
                    moment_id="moment_action",
                    source=MediaSourceSpan(start=1_000, end=2_000),
                    output=FrameRange(start_frame=30, duration_frames=30),
                    purpose="Land",
                ),
            ),
            music=(
                MusicEditDecision(
                    decision_key="music_bed",
                    asset_id="asset_music",
                    source=SourceRange(start=0, end=2_000, timescale=1_000),
                    output=FrameRange(start_frame=0, duration_frames=60),
                    gain_db=-18,
                    purpose="Support the visual build",
                ),
            ),
            sound_effects=(
                SoundEffectEditDecision(
                    decision_key="impact_sfx",
                    asset_id="asset_sfx",
                    source=SourceRange(start=0, end=1_000, timescale=1_000),
                    output=FrameRange(start_frame=15, duration_frames=30),
                    gain_db=-8,
                    evidence_moment_ids=("moment_action",),
                    purpose="Reinforce the visible action",
                ),
            ),
            audio_visual_sync=(
                AudioVisualSyncDecision(
                    sync_key="sync_music_cut",
                    audio_decision_key="music_bed",
                    signal_event="accent",
                    source_time_ms=music_event_ms,
                    output_frame=30,
                    target="visual_cut",
                    purpose="Land the second phrase on the accent",
                ),
                AudioVisualSyncDecision(
                    sync_key="sync_impact_cut",
                    audio_decision_key="impact_sfx",
                    signal_event="transient",
                    source_time_ms=500,
                    output_frame=30,
                    target="visual_cut",
                    purpose="Align the impact transient with the action",
                ),
            ),
            confidence=0.9,
            provenance=FIXTURE_PROVENANCE,
        ),
    )


def test_reviewer_accepts_model_choices_grounded_in_measured_audio_events() -> None:
    review = FirstCutReviewer().review(_context(), _decision())

    assert review.accepted is True
    assert review.issues == ()


def test_reviewer_rejects_invented_audio_event() -> None:
    review = FirstCutReviewer().review(_context(), _decision(music_event_ms=750))

    assert review.accepted is False
    assert {issue.code for issue in review.issues} == {"audio_sync_event_unmeasured"}
