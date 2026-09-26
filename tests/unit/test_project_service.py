from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aive.contracts.patch import PATCH_V1_OPERATION_NAMES, Patch
from aive.contracts.project import CreateProjectRequest
from aive.contracts.project_doc import DependencyRef, DependencySet, ProjectDoc
from aive.contracts.revision import ProjectRevision, RevisionProvenance
from aive.project.patching import OPERATION_HANDLERS
from aive.project.service import (
    CapabilityUse,
    ProjectService,
    ProjectServiceError,
)


class AcceptCapabilities:
    def rejection_reason(self, capability: CapabilityUse) -> str | None:
        return None


class RejectMotion:
    def rejection_reason(self, capability: CapabilityUse) -> str | None:
        if capability.kind == "motion":
            return "motion definition is not installed"
        return None


def _document(*, locks: list[dict[str, object]] | None = None) -> ProjectDoc:
    return ProjectDoc.model_validate(
        {
            "project_id": "project_demo",
            "revision_id": "revision_001",
            "intent": {
                "task_mode": "autonomous",
                "target_duration_frames": 180,
                "prompt": "Make a concise launch film",
            },
            "assets": [
                {"asset_id": "asset_video_a", "kind": "video"},
                {"asset_id": "asset_video_b", "kind": "video"},
                {"asset_id": "asset_photo", "kind": "photo"},
                {"asset_id": "asset_audio_a", "kind": "audio"},
                {"asset_id": "asset_audio_b", "kind": "audio"},
                {
                    "asset_id": "asset_reference",
                    "kind": "video",
                    "roles": ["reference"],
                },
            ],
            "creative_plan": {
                "summary": "A complete demo plan",
                "sections": [
                    {
                        "section_id": "section_all",
                        "output": {"start_frame": 0, "duration_frames": 180},
                        "role": "full_story",
                    }
                ],
            },
            "timeline": {
                "duration_frames": 180,
                "output": {
                    "width": 1080,
                    "height": 1920,
                    "fps": {"num": 30, "den": 1},
                },
                "tracks": [
                    {
                        "kind": "video",
                        "track_id": "track_video",
                        "role": "primary",
                        "clips": [
                            {
                                "kind": "video",
                                "clip_id": "clip_video",
                                "asset_id": "asset_video_a",
                                "source": {"start": 0, "end": 4000, "timescale": 1000},
                                "output": {"start_frame": 0, "duration_frames": 120},
                            }
                        ],
                    },
                    {
                        "kind": "audio",
                        "track_id": "track_audio",
                        "role": "music",
                        "clips": [
                            {
                                "clip_id": "clip_audio",
                                "asset_id": "asset_audio_a",
                                "source": {"start": 0, "end": 4000, "timescale": 1000},
                                "output": {"start_frame": 0, "duration_frames": 120},
                            }
                        ],
                    },
                    {
                        "kind": "caption",
                        "track_id": "track_caption",
                        "style": {
                            "style_id": "style_caption",
                            "component_name": "caption.basic",
                            "component_version": "1.0.0",
                        },
                        "cues": [
                            {
                                "cue_id": "caption_01",
                                "output": {"start_frame": 0, "duration_frames": 30},
                                "text": "Original",
                                "source": "generated",
                            }
                        ],
                    },
                    {
                        "kind": "graphic",
                        "track_id": "track_graphic",
                        "graphics": [
                            {
                                "graphic_id": "graphic_01",
                                "output": {"start_frame": 30, "duration_frames": 30},
                                "component_name": "title.basic",
                                "component_version": "1.0.0",
                                "props": {"title": "Original"},
                            }
                        ],
                    },
                ],
            },
            "locks": locks or [],
            "dependencies": {
                "renderer": {"name": "ffmpeg", "version": "dev"},
                "components": [
                    {"name": "caption.basic", "version": "1.0.0"},
                    {"name": "title.basic", "version": "1.0.0"},
                ],
            },
        }
    )


def _revision(*, document: ProjectDoc | None = None) -> ProjectRevision:
    return ProjectRevision(
        commit_id="commit_initial",
        created_at=datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
        document_hash="a" * 64,
        provenance=RevisionProvenance(kind="initial", origin="user"),
        document=document or _document(),
    )


def _patch(
    operations: list[dict[str, object]],
    *,
    origin: str = "agent",
    project_id: str = "project_demo",
    base_revision_id: str = "revision_001",
    scope: dict[str, object] | None = None,
) -> Patch:
    return Patch.model_validate(
        {
            "patch_id": "patch_001",
            "project_id": project_id,
            "base_revision_id": base_revision_id,
            "idempotency_key": "agent-turn-001",
            "origin": origin,
            "request_id": "turn_001",
            "summary": "Refine the opening",
            "scope": scope or {"kind": "global"},
            "operations": operations,
        }
    )


def _build(
    patch: Patch,
    *,
    base: ProjectRevision | None = None,
    capabilities: object | None = None,
    require_review: bool = False,
):
    return ProjectService(capabilities or AcceptCapabilities()).create_patch_candidate(
        base_revision=base or _revision(),
        patch=patch,
        commit_id="commit_candidate",
        revision_id="revision_002",
        created_at=datetime(2026, 9, 26, 10, 5, tzinfo=timezone.utc),
        actor_id="agent_editor",
        require_review=require_review,
    )


def test_every_patch_v1_operation_has_an_explicit_handler() -> None:
    assert tuple(OPERATION_HANDLERS) == PATCH_V1_OPERATION_NAMES


def test_initial_candidate_is_a_minimal_authoritative_project_doc() -> None:
    request = CreateProjectRequest(
        request_id="request_create",
        project_id="project_new",
        idempotency_key="create-project-new",
        origin="user",
        intent={
            "task_mode": "autonomous",
            "target_duration_frames": 900,
            "prompt": "Create a warm travel film",
            "style_tags": ["warm"],
        },
        output={
            "width": 1080,
            "height": 1920,
            "fps": {"num": 30, "den": 1},
        },
    )

    result = ProjectService(AcceptCapabilities()).create_initial_candidate(
        request=request,
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="dev")
        ),
        commit_id="commit_initial_new",
        revision_id="revision_initial_new",
        created_at=datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc),
    )

    assert result.attempt.state == "validated"
    assert result.attempt.request_kind == "initialize"
    assert result.revision.provenance.kind == "initial"
    assert result.revision.document.project_id == "project_new"
    assert result.revision.document.parent_revision_id is None
    assert result.revision.document.timeline.duration_frames == 900
    assert result.revision.document.timeline.tracks == ()
    assert result.revision.document.assets == ()
    assert result.revision.document.dependencies.renderer.name == "ffmpeg"


def test_candidate_applies_operations_in_order_without_mutating_base() -> None:
    base = _revision()
    patch = _patch(
        [
            {
                "op": "update_caption_text",
                "operation_id": "op_caption",
                "cue_id": "caption_01",
                "text": "Investor-ready",
            },
            {
                "op": "set_audio_gain",
                "operation_id": "op_audio",
                "clip_id": "clip_audio",
                "gain_db": -4.0,
            },
            {
                "op": "update_graphic_props",
                "operation_id": "op_graphic",
                "graphic_id": "graphic_01",
                "props": {"title": "Demo"},
            },
            {
                "op": "set_visual_motion",
                "operation_id": "op_motion",
                "clip_id": "clip_video",
                "motion": {
                    "definition_id": "motion.push_in",
                    "definition_version": "1.0.0",
                    "parameters": {"strength": 0.2},
                },
            },
        ]
    )

    result = _build(patch, base=base, require_review=True)

    assert result.attempt.state == "awaiting_review"
    assert result.revision.document.revision_id == "revision_002"
    assert result.revision.document.parent_revision_id == "revision_001"
    assert result.revision.provenance.patch_id == "patch_001"
    assert result.revision.document_hash != base.document_hash
    assert base.document.revision_id == "revision_001"
    assert base.document.timeline.tracks[2].cues[0].text == "Original"
    candidate = result.revision.document
    assert candidate.timeline.tracks[1].clips[0].gain_db == -4.0
    assert candidate.timeline.tracks[2].cues[0].text == "Investor-ready"
    assert candidate.timeline.tracks[3].graphics[0].props == {"title": "Demo"}
    assert candidate.timeline.tracks[0].clips[0].motion.definition_id == "motion.push_in"


@pytest.mark.parametrize(
    ("project_id", "base_revision_id", "state", "code"),
    [
        ("another_project", "revision_001", "rejected", "project_mismatch"),
        ("project_demo", "stale_revision", "conflict", "stale_base_revision"),
    ],
)
def test_identity_and_stale_base_fail_before_candidate_creation(
    project_id: str,
    base_revision_id: str,
    state: str,
    code: str,
) -> None:
    patch = _patch(
        [{"op": "set_timeline_duration", "operation_id": "op_1", "duration_frames": 200}],
        project_id=project_id,
        base_revision_id=base_revision_id,
    )

    with pytest.raises(ProjectServiceError) as caught:
        _build(patch)

    assert caught.value.attempt.state == state
    assert caught.value.attempt.candidate_revision_id is None
    assert caught.value.attempt.issue.code == code


def test_complete_candidate_validation_rejects_an_incoherent_intermediate_result() -> None:
    patch = _patch(
        [
            {
                "op": "set_visual_output",
                "operation_id": "op_output",
                "clip_id": "clip_video",
                "output": {"start_frame": 0, "duration_frames": 30},
            }
        ]
    )

    with pytest.raises(ProjectServiceError) as caught:
        _build(patch)

    assert caught.value.attempt.state == "rejected"
    assert caught.value.attempt.candidate_revision_id == "revision_002"
    assert caught.value.attempt.issue.code == "candidate_invalid"


def test_final_validation_allows_coordinated_operations_that_are_only_valid_together() -> None:
    patch = _patch(
        [
            {
                "op": "set_visual_output",
                "operation_id": "op_output",
                "clip_id": "clip_video",
                "output": {"start_frame": 0, "duration_frames": 60},
            },
            {
                "op": "set_video_playback",
                "operation_id": "op_playback",
                "clip_id": "clip_video",
                "playback": {"kind": "constant", "rate": {"num": 2, "den": 1}},
            },
        ]
    )

    result = _build(patch)

    clip = result.revision.document.timeline.tracks[0].clips[0]
    assert result.attempt.state == "validated"
    assert clip.output.duration_frames == 60
    assert clip.playback.rate.num == 2


def test_bounded_scope_and_preserve_ids_are_enforced() -> None:
    outside_scope = _patch(
        [
            {
                "op": "update_caption_text",
                "operation_id": "op_caption",
                "cue_id": "caption_01",
                "text": "Outside",
            }
        ],
        scope={"kind": "bounded", "target_ids": ["clip_video"]},
    )
    with pytest.raises(ProjectServiceError) as outside:
        _build(outside_scope)
    assert outside.value.attempt.issue.code == "scope_violation"

    preserved = _patch(
        [
            {
                "op": "set_visual_motion",
                "operation_id": "op_motion",
                "clip_id": "clip_video",
                "motion": None,
            }
        ],
        scope={
            "kind": "bounded",
            "target_ids": ["track_video"],
            "preserve_ids": ["clip_video"],
        },
    )
    with pytest.raises(ProjectServiceError) as protected:
        _build(preserved)
    assert protected.value.attempt.issue.code == "preserved_target"


def test_lock_blocks_matching_property_and_reports_the_lock() -> None:
    base = _revision(
        document=_document(
            locks=[
                {
                    "lock_id": "lock_style",
                    "target_type": "clip",
                    "target_id": "clip_video",
                    "protect": ["style"],
                }
            ]
        )
    )
    patch = _patch(
        [
            {
                "op": "set_visual_motion",
                "operation_id": "op_motion",
                "clip_id": "clip_video",
                "motion": None,
            }
        ]
    )

    with pytest.raises(ProjectServiceError) as caught:
        _build(patch, base=base)

    assert caught.value.attempt.issue.code == "lock_violation"
    assert caught.value.attempt.issue.details["lock_id"] == "lock_style"


def test_user_can_unlock_then_edit_but_agent_cannot_remove_locks() -> None:
    base = _revision(
        document=_document(
            locks=[
                {
                    "lock_id": "lock_style",
                    "target_type": "clip",
                    "target_id": "clip_video",
                    "protect": ["style"],
                }
            ]
        )
    )
    operations = [
        {"op": "remove_lock", "operation_id": "op_unlock", "lock_id": "lock_style"},
        {
            "op": "set_visual_motion",
            "operation_id": "op_motion",
            "clip_id": "clip_video",
            "motion": None,
        },
    ]

    result = _build(_patch(operations, origin="user"), base=base)
    assert result.attempt.state == "validated"
    assert result.revision.document.locks == ()

    with pytest.raises(ProjectServiceError) as caught:
        _build(_patch(operations, origin="agent"), base=base)
    assert caught.value.attempt.issue.code == "agent_cannot_remove_lock"


def test_capability_validator_rejects_unavailable_versioned_definitions() -> None:
    patch = _patch(
        [
            {
                "op": "set_visual_motion",
                "operation_id": "op_motion",
                "clip_id": "clip_video",
                "motion": {
                    "definition_id": "motion.unknown",
                    "definition_version": "9.9.9",
                },
            }
        ]
    )

    with pytest.raises(ProjectServiceError) as caught:
        _build(patch, capabilities=RejectMotion())

    assert caught.value.attempt.issue.code == "unsupported_capability"
    assert caught.value.attempt.issue.details["name"] == "motion.unknown"


def test_all_project_level_patch_operations_apply_as_one_ordered_unit() -> None:
    patch = _patch(
        [
            {
                "op": "set_intent",
                "operation_id": "op_01",
                "intent": {
                    "task_mode": "reference_remake",
                    "target_duration_frames": 240,
                    "prompt": "Remake the reference structure",
                },
            },
            {
                "op": "add_asset",
                "operation_id": "op_02",
                "asset": {"asset_id": "asset_temp", "kind": "photo"},
            },
            {
                "op": "update_asset",
                "operation_id": "op_03",
                "asset": {
                    "asset_id": "asset_temp",
                    "kind": "photo",
                    "label": "Updated",
                },
            },
            {"op": "remove_asset", "operation_id": "op_04", "asset_id": "asset_temp"},
            {
                "op": "set_creative_plan",
                "operation_id": "op_05",
                "creative_plan": {
                    "summary": "Reference-shaped plan",
                    "sections": [
                        {
                            "section_id": "section_all",
                            "output": {"start_frame": 0, "duration_frames": 240},
                            "role": "full_story",
                        }
                    ],
                },
            },
            {"op": "set_timeline_duration", "operation_id": "op_06", "duration_frames": 240},
            {
                "op": "set_output_settings",
                "operation_id": "op_07",
                "output": {
                    "width": 1080,
                    "height": 1920,
                    "fps": {"num": 30, "den": 1},
                    "audio_sample_rate": 48000,
                    "color_space": "display-p3",
                },
            },
            {
                "op": "add_track",
                "operation_id": "op_08",
                "track": {"kind": "graphic", "track_id": "track_temp", "graphics": []},
                "before_track_id": "track_graphic",
            },
            {
                "op": "move_track",
                "operation_id": "op_09",
                "track_id": "track_temp",
                "before_track_id": "track_video",
            },
            {
                "op": "set_track_enabled",
                "operation_id": "op_10",
                "track_id": "track_temp",
                "enabled": False,
            },
            {
                "op": "add_lock",
                "operation_id": "op_11",
                "lock": {
                    "lock_id": "lock_temp",
                    "target_type": "project",
                    "target_id": "project_demo",
                    "protect": ["content"],
                },
            },
            {"op": "remove_lock", "operation_id": "op_12", "lock_id": "lock_temp"},
            {
                "op": "set_reference_context",
                "operation_id": "op_13",
                "reference_context": {
                    "reference_asset_id": "asset_reference",
                    "blueprint_id": "blueprint_01",
                    "blueprint_version": "1.0.0",
                    "policy": {"strictness": "editing"},
                },
            },
            {
                "op": "set_reference_policy",
                "operation_id": "op_14",
                "policy": {
                    "strictness": "shot_aligned",
                    "preserve": ["section_order"],
                },
            },
            {
                "op": "upsert_reference_alignment",
                "operation_id": "op_15",
                "alignment": {
                    "alignment_id": "alignment_01",
                    "reference_section_id": "reference_section_01",
                    "reference_range": {"start": 0, "end": 4000, "timescale": 1000},
                    "output_range": {"start_frame": 0, "duration_frames": 120},
                    "output_element_ids": ["clip_video"],
                    "relation": "opening rhythm",
                },
            },
            {
                "op": "remove_reference_alignment",
                "operation_id": "op_16",
                "alignment_id": "alignment_01",
            },
            {
                "op": "upsert_adaptation_decision",
                "operation_id": "op_17",
                "decision": {
                    "decision_id": "decision_01",
                    "reference_section_id": "reference_section_01",
                    "reason": "Use available footage",
                    "action": "adapt shot role",
                    "status": "accepted",
                },
            },
            {
                "op": "remove_adaptation_decision",
                "operation_id": "op_18",
                "decision_id": "decision_01",
            },
            {
                "op": "set_dependencies",
                "operation_id": "op_19",
                "dependencies": {
                    "renderer": {"name": "ffmpeg", "version": "7.0"},
                    "components": [
                        {"name": "caption.basic", "version": "1.0.0"},
                        {"name": "title.basic", "version": "1.0.0"},
                    ],
                },
            },
            {"op": "remove_track", "operation_id": "op_20", "track_id": "track_temp"},
        ],
        origin="user",
    )

    result = _build(patch)
    document = result.revision.document

    assert result.attempt.state == "validated"
    assert document.intent.task_mode == "reference_remake"
    assert document.timeline.duration_frames == 240
    assert document.timeline.output.color_space == "display-p3"
    assert "track_temp" not in {track.track_id for track in document.timeline.tracks}
    assert "asset_temp" not in {asset.asset_id for asset in document.assets}
    assert document.locks == ()
    assert document.reference_context.policy.strictness == "shot_aligned"
    assert document.reference_context.alignments == ()
    assert document.reference_context.adaptations == ()
    assert document.dependencies.renderer.version == "7.0"


def test_all_timeline_patch_operations_apply_as_one_ordered_unit() -> None:
    patch = _patch(
        [
            {
                "op": "insert_visual_clip",
                "operation_id": "op_21",
                "track_id": "track_video",
                "clip": {
                    "kind": "image",
                    "clip_id": "clip_visual_temp",
                    "asset_id": "asset_photo",
                    "output": {"start_frame": 120, "duration_frames": 30},
                },
            },
            {
                "op": "replace_visual_source",
                "operation_id": "op_22",
                "clip_id": "clip_video",
                "source": {
                    "kind": "video",
                    "asset_id": "asset_video_b",
                    "source": {"start": 0, "end": 4000, "timescale": 1000},
                },
            },
            {
                "op": "set_visual_output",
                "operation_id": "op_23",
                "clip_id": "clip_video",
                "output": {"start_frame": 0, "duration_frames": 60},
            },
            {
                "op": "set_video_playback",
                "operation_id": "op_24",
                "clip_id": "clip_video",
                "playback": {"kind": "constant", "rate": {"num": 2, "den": 1}},
            },
            {
                "op": "set_visual_crop",
                "operation_id": "op_25",
                "clip_id": "clip_video",
                "crop": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
            },
            {
                "op": "set_visual_reframe",
                "operation_id": "op_26",
                "clip_id": "clip_video",
                "reframe": {"mode": "manual", "focus_x": 0.4, "focus_y": 0.5},
            },
            {
                "op": "set_visual_motion",
                "operation_id": "op_27",
                "clip_id": "clip_video",
                "motion": {
                    "definition_id": "motion.push_in",
                    "definition_version": "1.0.0",
                },
            },
            {
                "op": "set_visual_transition",
                "operation_id": "op_28",
                "clip_id": "clip_video",
                "transition": {
                    "definition_id": "transition.fade",
                    "definition_version": "1.0.0",
                    "duration_frames": 10,
                },
            },
            {
                "op": "upsert_visual_effect",
                "operation_id": "op_29",
                "clip_id": "clip_video",
                "effect": {
                    "effect_id": "effect_temp",
                    "effect_type": "effect.color",
                    "implementation_version": "1.0.0",
                    "parameters": {"contrast": 0.1},
                },
            },
            {
                "op": "remove_visual_effect",
                "operation_id": "op_30",
                "clip_id": "clip_video",
                "effect_id": "effect_temp",
            },
            {
                "op": "delete_visual_clip",
                "operation_id": "op_31",
                "clip_id": "clip_visual_temp",
            },
            {
                "op": "insert_audio_clip",
                "operation_id": "op_32",
                "track_id": "track_audio",
                "clip": {
                    "clip_id": "clip_audio_temp",
                    "asset_id": "asset_audio_a",
                    "source": {"start": 0, "end": 1000, "timescale": 1000},
                    "output": {"start_frame": 150, "duration_frames": 30},
                },
            },
            {
                "op": "replace_audio_source",
                "operation_id": "op_33",
                "clip_id": "clip_audio",
                "asset_id": "asset_audio_b",
                "source": {"start": 0, "end": 2000, "timescale": 1000},
            },
            {
                "op": "set_audio_output",
                "operation_id": "op_34",
                "clip_id": "clip_audio",
                "output": {"start_frame": 0, "duration_frames": 60},
            },
            {
                "op": "set_audio_playback",
                "operation_id": "op_35",
                "clip_id": "clip_audio",
                "playback": {"kind": "constant", "rate": {"num": 1, "den": 1}},
            },
            {
                "op": "set_audio_gain",
                "operation_id": "op_36",
                "clip_id": "clip_audio",
                "gain_db": -3,
            },
            {
                "op": "set_audio_fades",
                "operation_id": "op_37",
                "clip_id": "clip_audio",
                "fade_in_frames": 5,
                "fade_out_frames": 5,
            },
            {
                "op": "set_audio_ducking",
                "operation_id": "op_38",
                "clip_id": "clip_audio",
                "ducking_group": "voice",
            },
            {"op": "delete_audio_clip", "operation_id": "op_39", "clip_id": "clip_audio_temp"},
            {
                "op": "add_caption",
                "operation_id": "op_40",
                "track_id": "track_caption",
                "cue": {
                    "cue_id": "caption_temp",
                    "output": {"start_frame": 100, "duration_frames": 20},
                    "text": "Temporary",
                    "source": "generated",
                },
            },
            {
                "op": "update_caption_text",
                "operation_id": "op_41",
                "cue_id": "caption_01",
                "text": "Updated caption",
            },
            {
                "op": "set_caption_output",
                "operation_id": "op_42",
                "cue_id": "caption_01",
                "output": {"start_frame": 10, "duration_frames": 30},
            },
            {
                "op": "set_caption_style",
                "operation_id": "op_43",
                "track_id": "track_caption",
                "style": {
                    "style_id": "style_caption_v2",
                    "component_name": "caption.basic",
                    "component_version": "1.0.0",
                    "parameters": {"position": "bottom"},
                },
            },
            {"op": "delete_caption", "operation_id": "op_44", "cue_id": "caption_temp"},
            {
                "op": "add_graphic",
                "operation_id": "op_45",
                "track_id": "track_graphic",
                "graphic": {
                    "graphic_id": "graphic_temp",
                    "output": {"start_frame": 90, "duration_frames": 20},
                    "component_name": "title.basic",
                    "component_version": "1.0.0",
                },
            },
            {
                "op": "set_graphic_output",
                "operation_id": "op_46",
                "graphic_id": "graphic_01",
                "output": {"start_frame": 40, "duration_frames": 30},
            },
            {
                "op": "update_graphic_props",
                "operation_id": "op_47",
                "graphic_id": "graphic_01",
                "props": {"title": "Updated"},
            },
            {"op": "delete_graphic", "operation_id": "op_48", "graphic_id": "graphic_temp"},
        ]
    )

    result = _build(patch)
    document = result.revision.document
    video = document.timeline.tracks[0].clips[0]
    audio = document.timeline.tracks[1].clips[0]

    assert result.attempt.state == "validated"
    assert video.asset_id == "asset_video_b"
    assert video.output.duration_frames == 60
    assert video.motion.definition_id == "motion.push_in"
    assert video.transition_out.definition_id == "transition.fade"
    assert video.effects == ()
    assert audio.asset_id == "asset_audio_b"
    assert audio.output.duration_frames == 60
    assert audio.gain_db == -3
    assert audio.fade_in_frames == audio.fade_out_frames == 5
    assert audio.ducking_group == "voice"
    assert document.timeline.tracks[2].cues[0].text == "Updated caption"
    assert document.timeline.tracks[2].style.style_id == "style_caption_v2"
    assert document.timeline.tracks[3].graphics[0].props == {"title": "Updated"}
