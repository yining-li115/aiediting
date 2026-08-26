"""Fitting a chosen segment into its slot.

The reference video and the user's footage never line up exactly, and neither
do a plan and a real pool. The product must not say "you don't have that shot,
so this cannot be cut" - it must keep the rhythm and the narrative using the
most reasonable substitute available.

Steps are tried in order, cheapest and least visible first:

    1. Trim       pick the best sub-range inside the segment
    2. Extend/Shorten   move the slot length within its allowed range
    3. Speed      change tempo to hit the length (0.5x-2.0x only)
    4. Crop       centre-crop when the aspect ratio does not match
    5. Replace role / 6. Skip / 7. Merge   handled during selection

Every non-trivial step is written to ``adaptation_log``, so "why did this shot
end up like that?" is always answerable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..specs.edit_plan import Slot
from ..specs.segment import MaterialSegment
from ..specs.timeline import AdaptationRecord

# Narrower than the renderer's hard limit: beyond this, speed reads as an
# effect rather than as pacing, and that is an editorial choice a plan should
# make explicitly rather than something the matcher does to fit a gap.
MIN_SPEED = 0.5
MAX_SPEED = 2.0
MIN_CLIP = 0.35


@dataclass
class Fitted:
    source_in: float
    source_out: float
    timeline_duration: float
    speed: float
    records: list[AdaptationRecord] = field(default_factory=list)


def _best_subrange(segment: MaterialSegment, wanted: float) -> tuple[float, float]:
    """Pick where inside the segment to cut from.

    Centring is deliberate: the first and last moments of a detected shot are
    the most likely to contain the transition into or out of it, and those
    frames are exactly the ones that look like a mistake in a finished cut.
    """
    available = segment.duration
    if wanted >= available:
        return segment.start, segment.end
    margin = (available - wanted) / 2.0
    return segment.start + margin, segment.start + margin + wanted


def fit(
    slot: Slot,
    segment: MaterialSegment,
    target_duration: float | None = None,
) -> Fitted:
    wanted = target_duration or slot.target_duration
    low, high = slot.duration_range or (wanted, wanted)
    records: list[AdaptationRecord] = []

    if segment.kind == "photo":
        # Nothing to trim and nothing to speed up: the still simply holds for
        # the requested length. Recorded so the log still explains the clip.
        records.append(
            AdaptationRecord(
                slot_id=slot.slot_id,
                action="extend",
                detail=f"still held for {wanted:.2f}s",
            )
        )
        return Fitted(0.0, wanted, wanted, 1.0, records)

    # --- 1. Trim ---------------------------------------------------------
    if segment.duration >= wanted:
        source_in, source_out = _best_subrange(segment, wanted)
        if segment.duration - wanted > 0.05:
            records.append(
                AdaptationRecord(
                    slot_id=slot.slot_id,
                    action="trim",
                    detail=f"{segment.duration:.2f}s -> {wanted:.2f}s, centred",
                )
            )
        return Fitted(source_in, source_out, wanted, 1.0, records)

    # The segment is shorter than the slot wants.
    # --- 2. Shorten the slot, within its allowed range --------------------
    if segment.duration >= low:
        shortened = segment.duration
        records.append(
            AdaptationRecord(
                slot_id=slot.slot_id,
                action="shorten",
                detail=f"slot {wanted:.2f}s -> {shortened:.2f}s (range low {low:.2f}s)",
            )
        )
        return Fitted(segment.start, segment.end, shortened, 1.0, records)

    # --- 3. Speed --------------------------------------------------------
    needed_speed = segment.duration / max(low, 1e-6)
    if MIN_SPEED <= needed_speed <= MAX_SPEED and low >= MIN_CLIP:
        records.append(
            AdaptationRecord(
                slot_id=slot.slot_id,
                action="speed",
                detail=(
                    f"1.00 -> {needed_speed:.2f} to stretch "
                    f"{segment.duration:.2f}s into {low:.2f}s"
                ),
            )
        )
        return Fitted(segment.start, segment.end, low, needed_speed, records)

    # Nothing fits cleanly: use the segment as-is and let the timeline be
    # shorter. Reporting it is better than silently freezing a frame.
    duration = max(segment.duration, MIN_CLIP)
    records.append(
        AdaptationRecord(
            slot_id=slot.slot_id,
            action="shorten",
            detail=(
                f"segment only {segment.duration:.2f}s, below slot minimum "
                f"{low:.2f}s; used as-is"
            ),
        )
    )
    return Fitted(segment.start, segment.start + duration, duration, 1.0, records)


def redistribute(
    slots: list[Slot], skipped: set[str], target_total: float
) -> dict[str, float]:
    """Spread a skipped slot's time over the slots that survive.

    Without this, every skip shortens the video and the result drifts away
    from the requested length.
    """
    kept = [s for s in slots if s.slot_id not in skipped]
    if not kept:
        return {}
    base = sum(s.target_duration for s in kept)
    if base <= 0:
        return {}
    factor = target_total / base
    out: dict[str, float] = {}
    for slot in kept:
        low, high = slot.duration_range or (slot.target_duration, slot.target_duration)
        out[slot.slot_id] = max(low, min(high, slot.target_duration * factor))
    return out
