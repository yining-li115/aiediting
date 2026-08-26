"""Deterministic statistics over a reference's shots.

Everything here is arithmetic on the shot list. It is separated from the
analysis pipeline because B1 will aggregate across several references using
the same functions, and because none of it should ever be asked of a model.
"""

from __future__ import annotations

import numpy as np

from ..specs.reference import DurationStats, ReferenceShot


def duration_stats(values: list[float]) -> DurationStats:
    if not values:
        return DurationStats()
    array = np.asarray(values, dtype=np.float64)
    return DurationStats(
        mean=round(float(array.mean()), 3),
        median=round(float(np.median(array)), 3),
        p10=round(float(np.percentile(array, 10)), 3),
        p90=round(float(np.percentile(array, 90)), 3),
        std=round(float(array.std()), 3),
    )


def pace_of(median_duration: float) -> str:
    """The same ladder the planner uses, so a reference's pace and a plan's
    pace mean the same thing."""
    if median_duration <= 0.9:
        return "fast"
    if median_duration <= 1.3:
        return "medium_fast"
    if median_duration <= 2.0:
        return "medium"
    return "slow"


def transition_density(shots: list[ReferenceShot]) -> float:
    """Fraction of cuts that are something other than a hard cut."""
    if len(shots) < 2:
        return 0.0
    effects = sum(1 for s in shots[:-1] if s.transition_out != "hard_cut")
    return round(effects / (len(shots) - 1), 3)


def bigrams(sequence: list[str]) -> dict[str, float]:
    """Transition probabilities over a sequence.

    This is what makes a style aggregatable. Sequences from different videos
    cannot be averaged - they have different lengths and different content -
    but "a wide shot tends to be followed by a medium" survives aggregation,
    and that tendency is what editing grammar actually is.
    """
    if len(sequence) < 2:
        return {}
    counts: dict[str, int] = {}
    for a, b in zip(sequence, sequence[1:]):
        counts[f"{a}->{b}"] = counts.get(f"{a}->{b}", 0) + 1
    total = sum(counts.values())
    return {k: round(v / total, 4) for k, v in sorted(counts.items())}


def distribution(values: list[str]) -> dict[str, float]:
    if not values:
        return {}
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    total = len(values)
    return {k: round(v / total, 4) for k, v in sorted(counts.items())}


def style_tags(shots: list[ReferenceShot], median_duration: float,
               density: float) -> list[str]:
    """A few coarse descriptors, derived rather than guessed.

    Deliberately conservative: these feed the planner's style handling, and an
    invented tag would change the edit for no reason.
    """
    tags: list[str] = []
    if median_duration <= 1.0:
        tags.append("energetic")
    elif median_duration >= 2.5:
        tags.append("cinematic")
    if density <= 0.05:
        tags.append("minimal")
    roles = {s.semantic_role for s in shots}
    if "food" in roles:
        tags.append("food")
    if roles & {"opening_environment", "environment", "transit"}:
        tags.append("travel")
    return sorted(set(tags))
