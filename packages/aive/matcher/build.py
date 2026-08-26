"""EditPlanSpec + candidate pool -> TimelineSpec.

This is where a plan stops being abstract. Slots get real footage, real in/out
points and real positions on a timeline, and every compromise made along the
way is recorded.
"""

from __future__ import annotations

import numpy as np

from ..specs.base import new_id
from ..specs.edit_plan import EditPlanSpec
from ..specs.enums import Transition
from ..specs.segment import MaterialSegment
from ..specs.timeline import (
    AdaptationRecord,
    AudioMix,
    RenderConstraints,
    SubtitleTrack,
    TimelineSpec,
    VideoClip,
    VideoTrack,
)
from ..specs.validate import validate_timeline
from .adapt import fit, redistribute
from .assign import Assignment, assign
from .beats import align, build_grid
from .score import DEFAULT_WEIGHTS, Weights

ASPECT_SIZES = {"9:16": "1080x1920", "16:9": "1920x1080", "1:1": "1080x1080"}


def _transition_for(index: int, total: int, density: float) -> tuple[Transition, float]:
    """Spread the requested transition density evenly over the cuts.

    Evenly rather than randomly: clustered dissolves read as a mistake, and a
    deterministic placement keeps candidate comparison meaningful.
    """
    if index >= total - 1 or density <= 0:
        return "hard_cut", 0.0
    every = max(2, round(1 / max(density, 1e-6)))
    if (index + 1) % every == 0:
        return "dissolve", 0.4
    return "hard_cut", 0.0


def build_timeline(
    plan: EditPlanSpec,
    segments: list[MaterialSegment],
    vectors: dict[str, np.ndarray] | None = None,
    music: dict | None = None,
    weights: Weights = DEFAULT_WEIGHTS,
) -> TimelineSpec:
    assignments: list[Assignment] = assign(plan, segments, vectors, weights)

    # --- step 6: skip slots nothing can fill --------------------------------
    records: list[AdaptationRecord] = []
    skipped: set[str] = set()
    for item in assignments:
        if item.filled:
            continue
        skipped.add(item.slot.slot_id)
        records.append(
            AdaptationRecord(
                slot_id=item.slot.slot_id,
                action="skip",
                detail=(
                    f"no viable candidate for role {item.slot.semantic_role!r}"
                    f" / {item.slot.shot_type!r}"
                ),
            )
        )

    filled = [a for a in assignments if a.filled]
    if not filled:
        raise ValueError(
            f"plan {plan.plan_id} could not be filled: no segment matched any slot"
        )

    # Time freed by skipped slots is spread over the survivors so the result
    # still lands on the requested length.
    durations = redistribute(
        [a.slot for a in assignments], skipped, plan.intent.target_duration
    )

    clips: list[VideoClip] = []
    cursor = 0.0
    for index, item in enumerate(filled):
        assert item.segment is not None
        fitted = fit(item.slot, item.segment, durations.get(item.slot.slot_id))
        records.extend(fitted.records)

        transition, transition_duration = _transition_for(
            index, len(filled), plan.rhythm.transition_density
        )
        clips.append(
            VideoClip(
                clip_id=f"c_{index + 1:03d}",
                slot_id=item.slot.slot_id,
                source_id=item.segment.source_id,
                source_in=round(fitted.source_in, 3),
                source_out=round(fitted.source_out, 3),
                timeline_start=round(cursor, 3),
                timeline_end=round(cursor + fitted.timeline_duration, 3),
                speed=round(fitted.speed, 4),
                transition_out=transition,
                transition_duration=transition_duration,
                source_audio_gain=1.0 if plan.audio.keep_original_sound else 0.0,
            )
        )
        cursor += fitted.timeline_duration

    # Rounding each clip independently lets a few milliseconds accumulate, so
    # the last clip absorbs the drift and the timeline stays exactly contiguous.
    _snap(clips)

    # Then move cut points onto the beat, where the plan allows it. This runs
    # after fitting rather than during it: adaptation decides how long a shot
    # should be, and alignment only nudges where the boundary falls.
    beats_moved = 0
    if music and music.get("beat_times"):
        grid = build_grid(
            music["beat_times"],
            music.get("music_in", 0.0),
            clips[-1].timeline_end,
        )
        beats_moved = align(clips, plan, grid)

    duration = clips[-1].timeline_end

    timeline = TimelineSpec(
        timeline_id=new_id("tl"),
        plan_id=plan.plan_id,
        project_id=plan.project_id,
        duration=round(duration, 3),
        aspect_ratio=plan.intent.aspect_ratio,
        tracks=[VideoTrack(clips=clips), SubtitleTrack()],
        audio=AudioMix(
            music_id=(music or {}).get("music_id") or plan.audio.music_id,
            music_key=(music or {}).get("storage_key"),
            volume=0.8,
            ducking=plan.audio.duck_music_under_speech,
        ),
        render_constraints=RenderConstraints(
            resolution=ASPECT_SIZES.get(plan.intent.aspect_ratio, "1080x1920")
        ),
        adaptation_log=records,
    )
    if beats_moved:
        records.append(
            AdaptationRecord(
                slot_id="-",
                action="trim",
                detail=f"{beats_moved} cut points moved onto the beat",
            )
        )
    timeline.provenance.candidate_index = plan.provenance.candidate_index
    timeline.provenance.model = plan.provenance.model
    timeline.provenance.extra = dict(plan.provenance.extra)
    return timeline


def _snap(clips: list[VideoClip]) -> None:
    """Make clip boundaries exactly contiguous after rounding."""
    cursor = 0.0
    for clip in clips:
        span = round(clip.timeline_end - clip.timeline_start, 3)
        clip.timeline_start = round(cursor, 3)
        clip.timeline_end = round(cursor + span, 3)
        # source_out follows from the timeline span and speed, or the speed
        # invariant breaks.
        clip.source_out = round(clip.source_in + span * clip.speed, 3)
        cursor = clip.timeline_end


def build_candidates(
    plans: list[EditPlanSpec],
    segments: list[MaterialSegment],
    vectors: dict[str, np.ndarray] | None = None,
    music: dict | None = None,
) -> list[tuple[TimelineSpec, list]]:
    """Build every candidate, keeping the ones that fail feasible-but-broken.

    A plan that cannot be filled is dropped with its reason rather than
    silently omitted, so a run that produces three candidates instead of six
    is explainable.
    """
    out: list[tuple[TimelineSpec, list]] = []
    for plan in plans:
        try:
            timeline = build_timeline(plan, segments, vectors, music)
        except ValueError:
            continue
        out.append((timeline, validate_timeline(timeline, plan)))
    return out
