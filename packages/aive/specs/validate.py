"""Timeline invariants.

Called twice: after the matcher produces a TimelineSpec, and again before the
renderer consumes one. It is the cheapest defence this project has against
shipping a broken video, and it costs nothing at runtime.

Severity split:

* ``hard``  - refuse to render. The timeline is internally inconsistent.
* ``soft``  - render anyway, but record it. The timeline is executable but
  violates intent; the structural critic subtracts points for these.
"""

from __future__ import annotations

from typing import Literal

from .base import Spec
from .edit_plan import EditPlanSpec
from .enums import (
    IMPLEMENTED_TRANSITIONS,
    MAX_DISSOLVE_DURATION,
    MAX_SPEED,
    MIN_CLIP_DURATION,
    MIN_DISSOLVE_DURATION,
    MIN_SPEED,
    TIME_EPSILON,
)
from .timeline import TimelineSpec

Severity = Literal["hard", "soft"]


class Violation(Spec):
    code: str
    severity: Severity
    message: str
    location: str | None = None  # clip_id / slot_id / track


def _close(a: float, b: float, eps: float = TIME_EPSILON) -> bool:
    return abs(a - b) <= eps


def validate_timeline(
    timeline: TimelineSpec,
    plan: EditPlanSpec | None = None,
    asset_durations: dict[str, float] | None = None,
) -> list[Violation]:
    """Return every invariant violation, worst-first is not guaranteed.

    ``plan`` and ``asset_durations`` are optional: without them the checks that
    need them are skipped rather than guessed at.
    """
    out: list[Violation] = []
    clips = timeline.clips

    if not clips:
        out.append(
            Violation(
                code="EMPTY_TIMELINE",
                severity="hard",
                message="timeline has no video clips",
            )
        )
        return out

    ordered = sorted(clips, key=lambda c: c.timeline_start)

    # --- 1. monotonic and contiguous -----------------------------------------
    if [c.clip_id for c in ordered] != [c.clip_id for c in clips]:
        out.append(
            Violation(
                code="NOT_MONOTONIC",
                severity="hard",
                message="clips are not stored in ascending timeline_start order",
            )
        )
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.transition_out == "hard_cut" and not _close(
            prev.timeline_end, nxt.timeline_start
        ):
            out.append(
                Violation(
                    code="GAP_OR_OVERLAP",
                    severity="hard",
                    message=(
                        f"hard cut leaves a gap/overlap: {prev.timeline_end:.3f} "
                        f"-> {nxt.timeline_start:.3f}"
                    ),
                    location=prev.clip_id,
                )
            )

    # --- 2. covers [0, duration] ---------------------------------------------
    if not _close(ordered[0].timeline_start, 0.0):
        out.append(
            Violation(
                code="NOT_STARTING_AT_ZERO",
                severity="hard",
                message=f"first clip starts at {ordered[0].timeline_start:.3f}",
                location=ordered[0].clip_id,
            )
        )
    if not _close(ordered[-1].timeline_end, timeline.duration):
        out.append(
            Violation(
                code="DURATION_MISMATCH",
                severity="hard",
                message=(
                    f"last clip ends at {ordered[-1].timeline_end:.3f} but "
                    f"duration is {timeline.duration:.3f}"
                ),
                location=ordered[-1].clip_id,
            )
        )

    for clip in clips:
        # --- 3. speed consistency --------------------------------------------
        expected = clip.timeline_duration * clip.speed
        if not _close(clip.source_duration, expected):
            out.append(
                Violation(
                    code="SPEED_INCONSISTENT",
                    severity="hard",
                    message=(
                        f"source span {clip.source_duration:.3f}s != "
                        f"timeline span {clip.timeline_duration:.3f}s x "
                        f"speed {clip.speed}"
                    ),
                    location=clip.clip_id,
                )
            )

        # --- 4. source range legal -------------------------------------------
        if clip.source_in < 0 or clip.source_out <= clip.source_in:
            out.append(
                Violation(
                    code="BAD_SOURCE_RANGE",
                    severity="hard",
                    message=f"source_in={clip.source_in} source_out={clip.source_out}",
                    location=clip.clip_id,
                )
            )
        elif asset_durations is not None:
            limit = asset_durations.get(clip.source_id)
            if limit is None:
                out.append(
                    Violation(
                        code="UNKNOWN_SOURCE",
                        severity="hard",
                        message=f"source_id {clip.source_id} is not a known asset",
                        location=clip.clip_id,
                    )
                )
            elif clip.source_out > limit + TIME_EPSILON:
                out.append(
                    Violation(
                        code="SOURCE_OUT_OF_BOUNDS",
                        severity="hard",
                        message=(
                            f"source_out {clip.source_out:.3f} exceeds asset "
                            f"duration {limit:.3f}"
                        ),
                        location=clip.clip_id,
                    )
                )

        # --- 5. minimum shot length ------------------------------------------
        if clip.timeline_duration < MIN_CLIP_DURATION - TIME_EPSILON:
            out.append(
                Violation(
                    code="CLIP_TOO_SHORT",
                    severity="hard",
                    message=(
                        f"{clip.timeline_duration:.3f}s is below the "
                        f"{MIN_CLIP_DURATION}s floor"
                    ),
                    location=clip.clip_id,
                )
            )

        # --- 6. speed range ---------------------------------------------------
        if not (MIN_SPEED - 1e-9 <= clip.speed <= MAX_SPEED + 1e-9):
            out.append(
                Violation(
                    code="SPEED_OUT_OF_RANGE",
                    severity="hard",
                    message=f"speed {clip.speed} outside [{MIN_SPEED}, {MAX_SPEED}]",
                    location=clip.clip_id,
                )
            )

        # --- 8. transition legality (soft) ------------------------------------
        if clip.transition_out not in IMPLEMENTED_TRANSITIONS:
            out.append(
                Violation(
                    code="TRANSITION_NOT_IMPLEMENTED",
                    severity="soft",
                    message=f"transition {clip.transition_out!r} is not implemented",
                    location=clip.clip_id,
                )
            )
        elif clip.transition_out == "dissolve":
            if not (
                MIN_DISSOLVE_DURATION
                <= clip.transition_duration
                <= MAX_DISSOLVE_DURATION
            ):
                out.append(
                    Violation(
                        code="BAD_TRANSITION_DURATION",
                        severity="soft",
                        message=(
                            f"dissolve of {clip.transition_duration:.3f}s outside "
                            f"[{MIN_DISSOLVE_DURATION}, {MAX_DISSOLVE_DURATION}]"
                        ),
                        location=clip.clip_id,
                    )
                )
            elif clip.timeline_duration < clip.transition_duration:
                out.append(
                    Violation(
                        code="TRANSITION_TOO_LONG",
                        severity="soft",
                        message="dissolve is longer than the clip it starts from",
                        location=clip.clip_id,
                    )
                )

    # --- 7. duration matches intent (soft) -----------------------------------
    if plan is not None:
        target = plan.intent.target_duration
        allowed = target * plan.intent.duration_tolerance
        if abs(timeline.duration - target) > allowed:
            out.append(
                Violation(
                    code="OFF_TARGET_DURATION",
                    severity="soft",
                    message=(
                        f"{timeline.duration:.2f}s vs target {target:.2f}s "
                        f"(tolerance +/-{allowed:.2f}s)"
                    ),
                )
            )

    # --- 9. subtitles in range and non-overlapping (soft) --------------------
    subs = sorted(timeline.subtitle_track.clips, key=lambda s: s.timeline_start)
    for sub in subs:
        if sub.timeline_start < -TIME_EPSILON or (
            sub.timeline_end > timeline.duration + TIME_EPSILON
        ):
            out.append(
                Violation(
                    code="SUBTITLE_OUT_OF_RANGE",
                    severity="soft",
                    message=(
                        f"subtitle spans {sub.timeline_start:.3f}-"
                        f"{sub.timeline_end:.3f} outside [0, {timeline.duration:.3f}]"
                    ),
                    location=sub.clip_id,
                )
            )
        if sub.timeline_end <= sub.timeline_start:
            out.append(
                Violation(
                    code="SUBTITLE_EMPTY_SPAN",
                    severity="soft",
                    message="subtitle ends before it starts",
                    location=sub.clip_id,
                )
            )
    for prev, nxt in zip(subs, subs[1:]):
        if nxt.timeline_start < prev.timeline_end - TIME_EPSILON:
            out.append(
                Violation(
                    code="SUBTITLE_OVERLAP",
                    severity="soft",
                    message="two subtitles are visible at the same time",
                    location=nxt.clip_id,
                )
            )

    # --- 10. slots are traceable (soft) --------------------------------------
    if plan is not None:
        known = {s.slot_id for s in plan.slots}
        skipped = timeline.skipped_slot_ids()
        for clip in clips:
            if clip.slot_id is None:
                out.append(
                    Violation(
                        code="CLIP_WITHOUT_SLOT",
                        severity="soft",
                        message="clip is not traceable to a plan slot",
                        location=clip.clip_id,
                    )
                )
            elif clip.slot_id not in known:
                out.append(
                    Violation(
                        code="UNKNOWN_SLOT",
                        severity="soft",
                        message=f"slot_id {clip.slot_id} is not in plan {plan.plan_id}",
                        location=clip.clip_id,
                    )
                )
            elif clip.slot_id in skipped:
                out.append(
                    Violation(
                        code="SKIPPED_SLOT_RENDERED",
                        severity="soft",
                        message=(
                            f"slot {clip.slot_id} is marked skipped but has a clip"
                        ),
                        location=clip.clip_id,
                    )
                )

    return out


def hard_violations(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity == "hard"]


def assert_renderable(
    timeline: TimelineSpec,
    plan: EditPlanSpec | None = None,
    asset_durations: dict[str, float] | None = None,
) -> list[Violation]:
    """Raise on hard violations, return the soft ones for the caller to log."""
    violations = validate_timeline(timeline, plan, asset_durations)
    hard = hard_violations(violations)
    if hard:
        detail = "; ".join(f"[{v.code}] {v.message}" for v in hard)
        raise ValueError(f"timeline {timeline.timeline_id} is not renderable: {detail}")
    return [v for v in violations if v.severity == "soft"]
