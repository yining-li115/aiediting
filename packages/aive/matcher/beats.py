"""Aligning cuts to the music.

Based on the detected beat list, not on ``k * 60 / bpm``. Real beat grids are
not evenly spaced - the tracks in this project measure a 10ms standard
deviation between intervals and a first beat offset of half a second - so
arithmetic from the tempo drifts audibly over a 45-second cut with twenty cuts
in it. ``beat_times`` comes from onset detection and follows the music.

Three properties the algorithm has to have:

* **Non-accumulating.** Moving one cut changes the length of the shot before
  it and the start of the shot after it. Each boundary therefore aims at its
  position in the *original* plan, and the shot's length absorbs whatever the
  previous boundary did. Aiming at "cursor + planned length" instead lets a
  run of same-direction snaps compound, and the whole cut ends up seconds
  short of what the user asked for.
* **Constrained.** A snap that pushes a shot outside its slot's allowed
  duration is refused. Missing a beat is inaudible; a shot that is visibly the
  wrong length is not, so the constraint wins.
* **Optional.** ``music_sync_strength`` of zero means no snapping at all. Not
  every edit should sit on the grid - cutting exactly on every beat reads as
  metronomic rather than musical.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..specs.edit_plan import EditPlanSpec
from ..specs.enums import MIN_CLIP_DURATION
from ..specs.timeline import VideoClip

# TODO(review): a bar is assumed to be four beats. True for most pop and
# electronic music, wrong for waltz-time or anything in 6/8. Detecting the
# real meter needs downbeat tracking, which librosa's beat_track does not do.
BEATS_PER_BAR = 4

# TODO(review): how far a cut may move, as a fraction of one beat interval and
# of the shot's own length, at full sync strength. Half a beat is the largest
# move that still lands on "the nearest" beat rather than a different one; a
# quarter of the shot keeps a short shot from being dragged proportionally
# further than a long one. Neither has been checked against a viewer.
MAX_SHIFT_BEATS = 0.5
MAX_SHIFT_FRACTION = 0.25

# TODO(review): cut this many seconds before the beat so the incoming shot
# lands on it rather than starting from it. A real editing convention, but
# editors disagree about it, so the default is off until preference data can
# settle it (block F).
LEAD_SECONDS = 0.0


@dataclass
class BeatGrid:
    """Beat positions expressed in timeline time, not music-file time."""

    times: list[float]
    bar_times: list[float]

    @property
    def interval(self) -> float:
        if len(self.times) < 2:
            return 0.5
        gaps = [b - a for a, b in zip(self.times, self.times[1:])]
        gaps.sort()
        return gaps[len(gaps) // 2]

    def nearest(self, target: float, low: float, high: float,
                prefer_bars: bool = False) -> float | None:
        """The best beat inside [low, high], or None if there is none."""
        pool = self.bar_times if prefer_bars else self.times
        candidates = [t for t in pool if low <= t <= high]
        if not candidates and prefer_bars:
            candidates = [t for t in self.times if low <= t <= high]
        if not candidates:
            return None
        return min(candidates, key=lambda t: abs(t - target))


def build_grid(beat_times: list[float], music_in: float, duration: float) -> BeatGrid:
    """Shift the track's beats into timeline coordinates and clip to length."""
    times = [t - music_in for t in beat_times if music_in <= t <= music_in + duration]
    bars = [t for index, t in enumerate(times) if index % BEATS_PER_BAR == 0]
    return BeatGrid(times=times, bar_times=bars)


def align(
    clips: list[VideoClip],
    plan: EditPlanSpec,
    grid: BeatGrid,
    strength: float | None = None,
) -> int:
    """Move cut points onto beats where the plan allows it.

    Mutates ``clips`` in place and returns how many cuts were moved. Clips stay
    contiguous, and ``source_out`` is recomputed so the speed invariant holds.
    """
    strength = plan.rhythm.music_sync_strength if strength is None else strength
    if strength <= 0 or not grid.times or len(clips) < 2:
        return 0

    section_starts = _section_start_slots(plan)
    interval = grid.interval
    moved = 0

    # The boundaries the plan asked for. Snapping aims at these, never at a
    # position derived from where the previous snap happened to land.
    planned: list[float] = []
    running = 0.0
    for clip in clips:
        running += clip.timeline_duration
        planned.append(running)

    cursor = 0.0
    for index, clip in enumerate(clips):
        planned_end = planned[index]

        if index == len(clips) - 1:
            # The last boundary is the total length the plan asked for, so it
            # stays put and this clip absorbs every earlier adjustment.
            end = max(cursor + MIN_CLIP_DURATION, planned_end)
            _rewrite(clip, cursor, end)
            break

        slot = plan.slot_by_id(clip.slot_id) if clip.slot_id else None
        low, high = _allowed_range(slot, clip.timeline_duration)
        tolerance = strength * min(
            MAX_SHIFT_BEATS * interval, MAX_SHIFT_FRACTION * clip.timeline_duration
        )

        # A candidate has to satisfy the musical window, the slot's own
        # duration range, and the minimum shot length. The intersection may be
        # empty, and that is a perfectly good outcome.
        window_low = max(
            cursor + max(low, MIN_CLIP_DURATION), planned_end - tolerance
        )
        window_high = min(cursor + high, planned_end + tolerance)

        end = planned_end
        if window_low <= window_high:
            target = grid.nearest(
                planned_end + LEAD_SECONDS,
                window_low,
                window_high,
                prefer_bars=(clips[index + 1].slot_id in section_starts),
            )
            if target is not None and abs(target - planned_end) > 1e-3:
                end = target - LEAD_SECONDS
                moved += 1

        # Even without a snap, the boundary must respect the previous clip's
        # drift, or this clip could end up below the minimum length.
        end = max(end, cursor + MIN_CLIP_DURATION)

        _rewrite(clip, cursor, end)
        cursor = clip.timeline_end

    return moved


def _allowed_range(slot, current: float) -> tuple[float, float]:
    if slot is None or slot.duration_range is None:
        # TODO(review): +/-15% when the slot has no declared range. Wide enough
        # to reach a neighbouring beat at typical tempos, narrow enough not to
        # change the felt pacing.
        return current * 0.85, current * 1.15
    low, high = slot.duration_range
    return max(MIN_CLIP_DURATION, low), high


def _rewrite(clip: VideoClip, start: float, end: float) -> None:
    """Reposition a clip, keeping source_out consistent with speed."""
    span = round(end - start, 3)
    clip.timeline_start = round(start, 3)
    clip.timeline_end = round(start + span, 3)
    clip.source_out = round(clip.source_in + span * clip.speed, 3)


def _section_start_slots(plan: EditPlanSpec) -> set[str]:
    """Slots that open a narrative section.

    Landing a section change on a bar line is what makes an edit feel
    structured rather than merely on-beat.
    """
    return {
        section.slot_ids[0]
        for section in plan.story.sections
        if section.slot_ids
    }


def offsets_to_beats(clips: list[VideoClip], grid: BeatGrid) -> list[float]:
    """How far each cut sits from the nearest beat, in seconds.

    The same statistic reference analysis records as ``cut_to_beat_offset_ms``,
    so our own output can be measured the way a reference video is.
    """
    if not grid.times:
        return []
    out = []
    for clip in clips[:-1]:
        nearest = min(grid.times, key=lambda t: abs(t - clip.timeline_end))
        out.append(clip.timeline_end - nearest)
    return out
