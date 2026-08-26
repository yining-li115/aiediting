"""Each invariant must be caught by a purpose-built broken timeline.

The point of these tests is not coverage for its own sake: the validator is
the cheapest defence against shipping a broken video, and a validator that
silently stops catching a case is worse than none.
"""

from __future__ import annotations

from aive.specs import SubtitleClip, TimelineSpec, validate_timeline
from aive.specs.validate import hard_violations


def codes(timeline, plan=None, durations=None) -> set[str]:
    return {v.code for v in validate_timeline(timeline, plan, durations)}


def test_clean_timeline_has_no_violations(timeline: TimelineSpec) -> None:
    assert validate_timeline(timeline) == []


def test_1_out_of_order_clips(timeline: TimelineSpec) -> None:
    timeline.video_track.clips.reverse()
    assert "NOT_MONOTONIC" in codes(timeline)


def test_1b_gap_between_hard_cuts(timeline: TimelineSpec) -> None:
    timeline.clips[1].timeline_start += 0.4
    timeline.clips[1].source_out += 0.4 * timeline.clips[1].speed
    assert "GAP_OR_OVERLAP" in codes(timeline)


def test_2_must_start_at_zero(timeline: TimelineSpec) -> None:
    timeline.clips[0].timeline_start = 0.5
    assert "NOT_STARTING_AT_ZERO" in codes(timeline)


def test_2b_duration_must_match_last_clip(timeline: TimelineSpec) -> None:
    timeline.duration = 7.5
    assert "DURATION_MISMATCH" in codes(timeline)


def test_3_speed_inconsistency(timeline: TimelineSpec) -> None:
    timeline.clips[0].source_out += 1.0
    assert "SPEED_INCONSISTENT" in codes(timeline)


def test_4_bad_source_range(timeline: TimelineSpec) -> None:
    timeline.clips[0].source_out = timeline.clips[0].source_in
    assert "BAD_SOURCE_RANGE" in codes(timeline)


def test_4b_source_beyond_asset_duration(timeline: TimelineSpec) -> None:
    durations = {f"asset_{i:03d}": 1.2 for i in (1, 2, 3)}
    assert "SOURCE_OUT_OF_BOUNDS" in codes(timeline, None, durations)


def test_4c_unknown_source(timeline: TimelineSpec) -> None:
    assert "UNKNOWN_SOURCE" in codes(timeline, None, {"other": 10.0})


def test_5_clip_below_minimum_length(timeline: TimelineSpec) -> None:
    clip = timeline.clips[0]
    clip.timeline_end = 0.1
    clip.source_out = clip.source_in + 0.1
    timeline.clips[1].timeline_start = 0.1
    assert "CLIP_TOO_SHORT" in codes(timeline)


def test_6_speed_out_of_range(timeline: TimelineSpec) -> None:
    clip = timeline.clips[0]
    clip.speed = 8.0
    clip.source_out = clip.source_in + clip.timeline_duration * 8.0
    assert "SPEED_OUT_OF_RANGE" in codes(timeline)


def test_7_off_target_duration_is_soft(timeline: TimelineSpec, plan) -> None:
    plan.intent.target_duration = 45.0
    found = validate_timeline(timeline, plan)
    off = [v for v in found if v.code == "OFF_TARGET_DURATION"]
    assert off and off[0].severity == "soft"
    assert hard_violations(found) == []


def test_8_unimplemented_transition_is_soft(timeline: TimelineSpec) -> None:
    timeline.clips[0].transition_out = "whip_pan"
    found = validate_timeline(timeline)
    assert {v.code for v in found} >= {"TRANSITION_NOT_IMPLEMENTED"}
    assert all(v.severity == "soft" for v in found)


def test_8b_dissolve_duration_bounds(timeline: TimelineSpec) -> None:
    timeline.clips[0].transition_out = "dissolve"
    timeline.clips[0].transition_duration = 3.0
    assert "BAD_TRANSITION_DURATION" in codes(timeline)


def test_9_subtitles_out_of_range_and_overlapping(timeline: TimelineSpec) -> None:
    timeline.subtitle_track.clips.extend(
        [
            SubtitleClip(clip_id="s1", text="a", timeline_start=0.0, timeline_end=2.0),
            SubtitleClip(clip_id="s2", text="b", timeline_start=1.0, timeline_end=3.0),
            SubtitleClip(clip_id="s3", text="c", timeline_start=5.0, timeline_end=9.0),
        ]
    )
    found = codes(timeline)
    assert "SUBTITLE_OVERLAP" in found
    assert "SUBTITLE_OUT_OF_RANGE" in found


def test_10_slot_traceability(timeline: TimelineSpec, plan) -> None:
    timeline.clips[0].slot_id = "slot_99"
    timeline.clips[1].slot_id = None
    found = codes(timeline, plan)
    assert "UNKNOWN_SLOT" in found
    assert "CLIP_WITHOUT_SLOT" in found


def test_10b_skipped_slot_must_not_be_rendered(timeline: TimelineSpec, plan) -> None:
    from aive.specs import AdaptationRecord

    timeline.adaptation_log.append(
        AdaptationRecord(slot_id="slot_01", action="skip", detail="no candidate")
    )
    assert "SKIPPED_SLOT_RENDERED" in codes(timeline, plan)


def test_empty_timeline_is_hard(timeline: TimelineSpec) -> None:
    timeline.video_track.clips.clear()
    found = validate_timeline(timeline)
    assert [v.code for v in found] == ["EMPTY_TIMELINE"]
    assert found[0].severity == "hard"
