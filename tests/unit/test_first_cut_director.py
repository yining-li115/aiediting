from __future__ import annotations

from datetime import datetime, timezone

from aive.agent.first_cut import build_first_cut_patch
from aive.contracts.captions import default_caption_style
from aive.contracts.director import (
    CaptionEditDecision,
    EditBlueprint,
    EditSectionDecision,
    FirstCutDecision,
    MusicEditDecision,
    SoundEffectEditDecision,
    VisualEditDecision,
)
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
from aive.contracts.time import FrameRange, SourceRange
from aive.project.hashing import canonical_model_hash
from aive.project.service import ProjectService
from tests.v2_fixtures import FIXTURE_PROVENANCE, source_understanding


NOW = datetime(2026, 9, 26, 23, 45, tzinfo=timezone.utc)


class AcceptCapabilities:
    def rejection_reason(self, capability):
        return None


def _document() -> ProjectDoc:
    return ProjectDoc(
        project_id="project_first_cut",
        revision_id="revision_assets",
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=90,
            prompt="Make an energetic short video",
        ),
        assets=(
            AssetRef(asset_id="asset_video", kind="video"),
            AssetRef(asset_id="asset_photo", kind="photo"),
            AssetRef(asset_id="asset_music", kind="audio", roles=("music",)),
            AssetRef(asset_id="asset_sfx", kind="audio", roles=("sfx",)),
        ),
        timeline=Timeline(
            duration_frames=90,
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


def _moment(
    moment_id: str,
    asset_id: str,
    start: int,
    end: int,
    *,
    ordinal: int = 0,
    usable: bool = True,
) -> MomentEvidence:
    return MomentEvidence(
        moment_id=moment_id,
        project_id="project_first_cut",
        asset_id=asset_id,
        ordinal=ordinal,
        shot_ids=(f"{moment_id}_shot",),
        source=MediaSourceSpan(start=start, end=end, timescale=1_000),
        analysis_window_count=1,
        keyframe_count=1,
        understanding=source_understanding(
            shot_ids=(f"{moment_id}_shot",),
            source=MediaSourceSpan(start=start, end=end, timescale=1_000),
            summary="A useful scene",
            narrative_function="detail",
            worth_using=usable,
        ),
    )


def _decision(
    *,
    visuals: tuple[VisualEditDecision, ...],
    duration_frames: int,
    captions: tuple[CaptionEditDecision, ...] = (),
    music: tuple[MusicEditDecision, ...] = (),
    sound_effects: tuple[SoundEffectEditDecision, ...] = (),
    summary: str = "AI first cut",
) -> FirstCutDecision:
    return FirstCutDecision(
        summary=summary,
        explanation="Created the requested first cut.",
        edit_blueprint=EditBlueprint(
            objective="Fulfil the test request",
            narrative_summary=summary,
            output_duration_frames=duration_frames,
            sections=(
                EditSectionDecision(
                    section_key="section_1",
                    output=FrameRange(
                        start_frame=0,
                        duration_frames=duration_frames,
                    ),
                    role="story",
                    goal="Create the sequence",
                ),
            ),
            visuals=visuals,
            captions=captions,
            caption_style=(
                default_caption_style("caption_style_test") if captions else None
            ),
            music=music,
            sound_effects=sound_effects,
            confidence=0.9,
            provenance=FIXTURE_PROVENANCE,
        ),
    )


def test_builder_creates_one_valid_gapless_patch_with_source_audio() -> None:
    document = _document()
    moments = (
        _moment("moment_video", "asset_video", 125, 2_125),
        _moment("moment_photo", "asset_photo", 0, 1_000),
    )
    decision = _decision(
        duration_frames=90,
        visuals=(
            VisualEditDecision(
                decision_key="video_1",
                moment_id="moment_video",
                source=MediaSourceSpan(start=125, end=2_125),
                output=FrameRange(start_frame=0, duration_frames=60),
                purpose="Open on motion",
            ),
            VisualEditDecision(
                decision_key="photo_1",
                moment_id="moment_photo",
                source=None,
                output=FrameRange(start_frame=60, duration_frames=30),
                purpose="Land on the detail",
                preserve_source_audio=False,
            ),
        ),
    )

    patch = build_first_cut_patch(
        document=document,
        moments=moments,
        decision=decision,
        run_id="run_first_cut",
        request_id="request_first_cut",
        asset_has_audio={"asset_video": True, "asset_photo": False},
    )
    base = ProjectRevision(
        commit_id="commit_assets",
        created_at=NOW,
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(kind="initial", origin="system"),
        document=document,
    )
    result = ProjectService(AcceptCapabilities()).create_patch_candidate(
        base_revision=base,
        patch=patch,
        commit_id="commit_first_cut",
        revision_id="revision_first_cut",
        created_at=NOW,
    )

    timeline = result.revision.document.timeline
    video = next(track for track in timeline.tracks if track.kind == "video")
    audio = next(track for track in timeline.tracks if track.kind == "audio")
    assert timeline.duration_frames == 90
    assert [clip.output.start_frame for clip in video.clips] == [0, 60]
    assert [clip.output.duration_frames for clip in video.clips] == [60, 30]
    assert video.clips[0].source.start / video.clips[0].source.timescale == 0.125
    assert len(audio.clips) == 1
    assert audio.clips[0].output == video.clips[0].output
    assert patch.origin == "agent"
    assert patch.scope.kind == "global"


def test_render_autofix_candidate_uses_fresh_stable_patch_identity() -> None:
    document = _document()
    moment = _moment("moment_video", "asset_video", 0, 2_000)
    decision = _decision(
        duration_frames=60,
        visuals=(
            VisualEditDecision(
                decision_key="video_1",
                moment_id=moment.moment_id,
                source=moment.source,
                output=FrameRange(start_frame=0, duration_frames=60),
                purpose="Use the opening",
            ),
        ),
    )
    common = {
        "document": document,
        "moments": (moment,),
        "decision": decision,
        "run_id": "run_first_cut",
        "request_id": "request_first_cut",
        "asset_has_audio": {"asset_video": True},
    }

    initial = build_first_cut_patch(**common)
    replacement = build_first_cut_patch(**common, candidate_attempt=1)

    assert replacement.base_revision_id == initial.base_revision_id
    assert replacement.patch_id != initial.patch_id
    assert replacement.idempotency_key != initial.idempotency_key
    assert replacement.operations != initial.operations


def test_builder_does_not_replace_model_editorial_judgment_with_a_rule() -> None:
    document = _document()
    moment = _moment(
        "moment_rejected",
        "asset_video",
        0,
        1_000,
        usable=False,
    )
    decision = _decision(
        duration_frames=30,
        summary="Deliberate exception",
        visuals=(
            VisualEditDecision(
                decision_key="video_exception",
                moment_id=moment.moment_id,
                source=moment.source,
                output=FrameRange(start_frame=0, duration_frames=30),
                purpose="A deliberate contextual exception",
            ),
        ),
    )

    patch = build_first_cut_patch(
        document=document,
        moments=(moment,),
        decision=decision,
        run_id="run_first_cut",
        request_id="request_first_cut",
        asset_has_audio={"asset_video": True},
    )
    assert patch.operations


def test_builder_materializes_asr_captions_and_authorized_music() -> None:
    document = _document()
    moment = _moment("moment_video", "asset_video", 0, 3_000)
    moment = moment.model_copy(
        update={"has_speech": True, "asr_text": "A real line from ASR."}
    )
    decision = _decision(
        duration_frames=90,
        summary="Captioned music cut",
        visuals=(
            VisualEditDecision(
                decision_key="video_1",
                moment_id=moment.moment_id,
                source=moment.source,
                output=FrameRange(start_frame=0, duration_frames=90),
                purpose="Carry the story",
            ),
        ),
        captions=(
            CaptionEditDecision(
                decision_key="caption_1",
                output=FrameRange(start_frame=0, duration_frames=90),
                text="A real line from ASR.",
                source="asr",
                evidence_moment_ids=(moment.moment_id,),
            ),
        ),
        music=(
            MusicEditDecision(
                decision_key="music_1",
                asset_id="asset_music",
                source=SourceRange(start=0, end=2, timescale=1),
                output=FrameRange(start_frame=0, duration_frames=60),
                gain_db=-18,
                fade_in_frames=15,
                duck_under_speech=True,
                purpose="Build under the opening",
            ),
            MusicEditDecision(
                decision_key="music_2",
                asset_id="asset_music",
                source=SourceRange(start=0, end=1, timescale=1),
                output=FrameRange(start_frame=60, duration_frames=30),
                gain_db=-18,
                fade_out_frames=15,
                duck_under_speech=True,
                purpose="Resolve under the ending",
            ),
        ),
    )

    patch = build_first_cut_patch(
        document=document,
        moments=(moment,),
        decision=decision,
        run_id="run_caption_music",
        request_id="request_caption_music",
        asset_has_audio={"asset_video": True, "asset_music": True},
        asset_duration_seconds={"asset_video": 3.0, "asset_music": 2.0},
    )
    base = ProjectRevision(
        commit_id="commit_assets",
        created_at=NOW,
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(kind="initial", origin="system"),
        document=document,
    )
    result = ProjectService(AcceptCapabilities()).create_patch_candidate(
        base_revision=base,
        patch=patch,
        commit_id="commit_caption_music",
        revision_id="revision_caption_music",
        created_at=NOW,
    )

    tracks = result.revision.document.timeline.tracks
    captions = next(track for track in tracks if track.kind == "caption")
    music = next(
        track for track in tracks if track.kind == "audio" and track.role == "music"
    )
    assert [cue.text for cue in captions.cues] == ["A real line from ASR."]
    assert sum(clip.output.duration_frames for clip in music.clips) == 90
    assert len(music.clips) == 2
    assert all(clip.gain_db == -18 for clip in music.clips)
    assert all(clip.ducking_group == "speech" for clip in music.clips)
    assert music.clips[0].fade_in_frames == 15
    assert music.clips[-1].fade_out_frames == 15


def test_builder_materializes_model_selected_sound_effects() -> None:
    document = _document()
    moment = _moment("moment_video", "asset_video", 0, 2_000)
    decision = _decision(
        duration_frames=60,
        visuals=(
            VisualEditDecision(
                decision_key="video_1",
                moment_id=moment.moment_id,
                source=moment.source,
                output=FrameRange(start_frame=0, duration_frames=60),
                purpose="Show the action",
            ),
        ),
        sound_effects=(
            SoundEffectEditDecision(
                decision_key="sfx_impact",
                asset_id="asset_sfx",
                source=SourceRange(start=0, end=2, timescale=30),
                output=FrameRange(start_frame=29, duration_frames=2),
                gain_db=-8,
                evidence_moment_ids=(moment.moment_id,),
                purpose="Reinforce the visible action",
            ),
        ),
    )

    patch = build_first_cut_patch(
        document=document,
        moments=(moment,),
        decision=decision,
        run_id="run_sfx",
        request_id="request_sfx",
        asset_has_audio={"asset_video": True, "asset_sfx": True},
        asset_duration_seconds={"asset_video": 2.0, "asset_sfx": 1.0},
    )
    base = ProjectRevision(
        commit_id="commit_assets",
        created_at=NOW,
        document_hash=canonical_model_hash(document),
        provenance=RevisionProvenance(kind="initial", origin="system"),
        document=document,
    )
    result = ProjectService(AcceptCapabilities()).create_patch_candidate(
        base_revision=base,
        patch=patch,
        commit_id="commit_sfx",
        revision_id="revision_sfx",
        created_at=NOW,
    )

    sfx = next(
        track
        for track in result.revision.document.timeline.tracks
        if track.kind == "audio" and track.role == "sfx"
    )
    assert len(sfx.clips) == 1
    assert sfx.clips[0].output == FrameRange(start_frame=29, duration_frames=2)
    assert sfx.clips[0].gain_db == -8
