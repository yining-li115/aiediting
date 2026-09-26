from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from aive.contracts.patch import (
    PATCH_V1_OPERATION_NAMES,
    PATCH_VERSION,
    Patch,
    PatchOperation,
    PatchScope,
)


EXPECTED_OPERATION_NAMES = (
    # Project document
    "set_intent",
    "add_asset",
    "update_asset",
    "remove_asset",
    "set_creative_plan",
    "set_timeline_duration",
    "set_output_settings",
    "add_track",
    "remove_track",
    "move_track",
    "set_track_enabled",
    "add_lock",
    "remove_lock",
    "set_reference_context",
    "set_reference_policy",
    "upsert_reference_alignment",
    "remove_reference_alignment",
    "upsert_adaptation_decision",
    "remove_adaptation_decision",
    "set_dependencies",
    # Visual timeline
    "insert_visual_clip",
    "delete_visual_clip",
    "replace_visual_source",
    "set_visual_output",
    "set_video_playback",
    "set_visual_crop",
    "set_visual_reframe",
    "set_visual_motion",
    "set_visual_transition",
    "upsert_visual_effect",
    "remove_visual_effect",
    # Audio timeline
    "insert_audio_clip",
    "delete_audio_clip",
    "replace_audio_source",
    "set_audio_output",
    "set_audio_playback",
    "set_audio_gain",
    "set_audio_fades",
    "set_audio_ducking",
    # Captions
    "add_caption",
    "update_caption_text",
    "set_caption_output",
    "delete_caption",
    "set_caption_style",
    # Graphics
    "add_graphic",
    "set_graphic_output",
    "update_graphic_props",
    "delete_graphic",
)


def _patch_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "patch_version": "1.0",
        "patch_id": "patch_demo_01",
        "project_id": "project_demo",
        "base_revision_id": "revision_07",
        "idempotency_key": "edit-request-2026-09-25-01",
        "origin": "agent",
        "request_id": "request_01",
        "summary": "Replace the ending shot without changing the opening",
        "scope": {
            "kind": "bounded",
            "target_ids": ["clip_ending"],
            "preserve_ids": ["clip_opening"],
            "allow_ripple": False,
        },
        "operations": [
            {
                "op": "replace_visual_source",
                "operation_id": "operation_01",
                "clip_id": "clip_ending",
                "source": {
                    "kind": "video",
                    "asset_id": "asset_replacement",
                    "source": {"start": 3000, "end": 7000, "timescale": 1000},
                },
            }
        ],
    }
    values.update(overrides)
    return values


def test_patch_v1_operation_whitelist_is_explicit_and_stable() -> None:
    assert PATCH_VERSION == "1.0"
    assert PATCH_V1_OPERATION_NAMES == EXPECTED_OPERATION_NAMES
    assert len(PATCH_V1_OPERATION_NAMES) == len(set(PATCH_V1_OPERATION_NAMES))


def test_patch_round_trips_with_a_discriminated_operation() -> None:
    patch = Patch.model_validate(_patch_payload())

    restored = Patch.model_validate_json(patch.model_dump_json())

    assert restored == patch
    assert restored.operations[0].op == "replace_visual_source"
    assert restored.operations[0].source.kind == "video"


def test_checked_in_patch_example_stays_valid() -> None:
    example = Path(__file__).parents[2] / "examples" / "patch_v1.json"

    patch = Patch.model_validate_json(example.read_text(encoding="utf-8"))

    assert patch.project_id == "project_demo"
    assert [operation.op for operation in patch.operations] == [
        "add_lock",
        "set_video_playback",
        "set_visual_motion",
        "set_visual_transition",
        "replace_visual_source",
    ]


@pytest.mark.parametrize(
    "operation",
    [
        {
            "op": "set_reference_policy",
            "operation_id": "reference_01",
            "policy": {
                "strictness": "editing",
                "preserve": ["pacing"],
                "adapt": ["shot_roles"],
                "exclude": ["brand"],
            },
        },
        {
            "op": "set_visual_motion",
            "operation_id": "motion_01",
            "clip_id": "clip_photo",
            "motion": {
                "definition_id": "motion.parallax_pan",
                "definition_version": "1.0.0",
                "parameters": {"depth": 0.7, "direction": "left"},
            },
        },
        {
            "op": "set_audio_gain",
            "operation_id": "audio_01",
            "clip_id": "clip_music",
            "gain_db": -8.5,
        },
        {
            "op": "update_caption_text",
            "operation_id": "caption_01",
            "cue_id": "cue_01",
            "text": "A precise, editable story.",
        },
        {
            "op": "update_graphic_props",
            "operation_id": "graphic_01",
            "graphic_id": "graphic_title",
            "props": {"title": "AIVE", "accent": "#FF5C35"},
        },
    ],
)
def test_operations_from_each_domain_validate(operation: dict[str, object]) -> None:
    parsed = TypeAdapter(PatchOperation).validate_python(operation)

    assert parsed.op == operation["op"]


def test_arbitrary_json_patch_is_rejected() -> None:
    payload = _patch_payload(
        operations=[
            {
                "op": "replace",
                "operation_id": "operation_01",
                "path": "/timeline/tracks/0/clips/2/asset_id",
                "value": "asset_other",
            }
        ]
    )

    with pytest.raises(ValidationError, match="union_tag_invalid"):
        Patch.model_validate(payload)


def test_visual_motion_and_transition_can_be_cleared() -> None:
    adapter = TypeAdapter(PatchOperation)

    motion = adapter.validate_python(
        {
            "op": "set_visual_motion",
            "operation_id": "motion_clear",
            "clip_id": "clip_photo",
            "motion": None,
        }
    )
    transition = adapter.validate_python(
        {
            "op": "set_visual_transition",
            "operation_id": "transition_clear",
            "clip_id": "clip_photo",
            "transition": None,
        }
    )

    assert motion.motion is None
    assert transition.transition is None


def test_patch_requires_at_least_one_operation() -> None:
    with pytest.raises(ValidationError):
        Patch.model_validate(_patch_payload(operations=[]))


def test_operation_ids_must_be_unique_within_a_patch() -> None:
    operation = _patch_payload()["operations"][0]

    with pytest.raises(ValidationError, match="duplicate operation id"):
        Patch.model_validate(_patch_payload(operations=[operation, operation]))


def test_bounded_scope_requires_a_target_or_output_range() -> None:
    with pytest.raises(ValidationError, match="bounded scope"):
        PatchScope(kind="bounded")


def test_scope_ids_are_unique_and_target_preserve_sets_do_not_overlap() -> None:
    with pytest.raises(ValidationError, match="duplicate target id"):
        PatchScope(kind="bounded", target_ids=("clip_1", "clip_1"))

    with pytest.raises(ValidationError, match="both target_ids and preserve_ids"):
        PatchScope(
            kind="bounded",
            target_ids=("clip_1",),
            preserve_ids=("clip_1",),
        )


def test_operation_payloads_are_closed_and_models_are_frozen() -> None:
    payload = _patch_payload()
    payload["operations"][0]["path"] = "/not/allowed"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        Patch.model_validate(payload)

    patch = Patch.model_validate(_patch_payload())
    with pytest.raises(ValidationError, match="frozen_instance"):
        patch.summary = "mutated"


def test_patch_emits_a_closed_schema_with_every_operation_discriminator() -> None:
    schema = Patch.model_json_schema()
    encoded = json.dumps(schema)

    assert schema["additionalProperties"] is False
    for operation_name in EXPECTED_OPERATION_NAMES:
        assert f'"const": "{operation_name}"' in encoded
