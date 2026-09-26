"""Deterministic statistics over a reference's shots.

Everything here is arithmetic on the shot list. It is separated from the
analysis pipeline because B1 will aggregate across several references using
the same functions, and because none of it should ever be asked of a model.
"""

from __future__ import annotations

import numpy as np

from ..specs.reference import (
    DurationStats,
    ReferenceAnalysis,
    ReferenceShot,
    StylePrior,
)


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


def _scalar_agreement(values: list[float]) -> float:
    """1.0 when the references agree on a number, 0.0 when they scatter.

    Coefficient of variation rather than raw spread, because the features have
    different units: half a second of disagreement is a lot for shot length and
    nothing for a beat count.
    """
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        # One reference cannot disagree with itself. That is not the same as
        # agreement, but the alternative - reporting 0 - would make every
        # single-reference prior unusable, which is wrong.
        return 1.0
    array = np.asarray(clean, dtype=np.float64)
    mean = float(array.mean())
    if abs(mean) < 1e-6:
        return 1.0 if float(array.std()) < 1e-6 else 0.0
    cv = float(array.std()) / abs(mean)
    return round(max(0.0, 1.0 - cv), 4)


def _distribution_agreement(dists: list[dict[str, float]]) -> float:
    """Mean pairwise overlap between distributions.

    Total-variation distance: two identical distributions overlap 1.0, two
    with no shared keys overlap 0.0. Averaged over every pair so one outlier
    reference cannot hide behind three similar ones.
    """
    present = [d for d in dists if d]
    if len(present) < 2:
        return 1.0
    scores: list[float] = []
    for i in range(len(present)):
        for j in range(i + 1, len(present)):
            a, b = present[i], present[j]
            keys = set(a) | set(b)
            tv = 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)
            scores.append(1.0 - tv)
    return round(float(np.mean(scores)), 4)


def merge_distributions(dists: list[dict[str, float]]) -> dict[str, float]:
    """Average several distributions into one, renormalised.

    Unweighted: a 90-shot reference and a 12-shot one contribute equally,
    because the question is "what does this creator tend to do", not "what
    happens across all these frames". A long video would otherwise drown out
    the others purely by being long.
    """
    present = [d for d in dists if d]
    if not present:
        return {}
    keys: set[str] = set()
    for d in present:
        keys |= set(d)
    merged = {k: sum(d.get(k, 0.0) for d in present) / len(present) for k in keys}
    total = sum(merged.values())
    if total <= 0:
        return {}
    return {k: round(v / total, 4) for k, v in sorted(merged.items()) if v > 0}


def build_prior(
    analyses: list[ReferenceAnalysis],
    project_id: str | None = None,
) -> StylePrior:
    """N x ReferenceAnalysis -> StylePrior.

    Pure arithmetic, no model. Everything a model could add here - "why is it
    cut this way" - has already been added upstream, one reference at a time;
    aggregation is only ever averaging, and asking a model to average is both
    more expensive and less correct.
    """
    if not analyses:
        return StylePrior(project_id=project_id)

    # Shot lengths are pooled across references rather than averaged per
    # reference: the distribution of shot lengths IS the pace, and pooling
    # keeps its shape where averaging the means would flatten it.
    all_durations = [d for a in analyses for d in a.durations()]
    duration = duration_stats(all_durations)

    scale_dists = [distribution(a.shot_scale_sequence) for a in analyses]
    role_dists = [distribution(a.semantic_structure) for a in analyses]
    scale_bigrams = [bigrams(a.shot_scale_sequence) for a in analyses]
    role_bigram_list = [bigrams(a.semantic_structure) for a in analyses]

    densities = [a.transition_density for a in analyses]
    sync = [a.music.beat_sync_strength for a in analyses]
    # TODO(review): section_count stays empty until the reference pipeline
    # detects structure rather than just shots. Shot count was standing in for
    # it, which on a real reference meant telling the planner "this style
    # typically has 24 sections" - a number it would have obeyed. A field left
    # unmeasured is better than a field measured with the wrong instrument:
    # the first is skipped, the second is followed.
    sections: list[float] = []

    subtitle = _dominant([a.text.subtitle_density for a in analyses])

    tags: list[str] = []
    for a in analyses:
        tags.extend(a.style_tags)

    consistency = round(
        float(
            np.mean(
                [
                    _scalar_agreement([a.shot_duration.median for a in analyses]),
                    _scalar_agreement(densities),
                    _distribution_agreement(scale_dists),
                    _distribution_agreement(role_dists),
                ]
            )
        ),
        4,
    )

    return StylePrior(
        project_id=project_id,
        source_reference_ids=[a.reference_id for a in analyses],
        shot_duration=duration,
        total_duration=duration_stats([a.duration for a in analyses]),
        pace=pace_of(duration.median),
        shot_scale_distribution=merge_distributions(scale_dists),
        shot_scale_bigrams=merge_distributions(scale_bigrams),
        role_distribution=merge_distributions(role_dists),
        role_bigrams=merge_distributions(role_bigram_list),
        transition_density=duration_stats(densities),
        subtitle_density=subtitle,
        beat_sync_strength=duration_stats(sync),
        section_count=duration_stats(sections),
        # Kept if it appears anywhere: a tag on one of three references is a
        # real property of the set, and dropping it would lose exactly the
        # distinguishing traits the user picked those references for.
        style_tags=sorted(set(tags)),
        consistency=consistency,
    )


def _dominant(values: list[str]) -> str:
    """The most common non-unknown value, or "unknown" when nobody looked."""
    known = [v for v in values if v and v != "unknown"]
    if not known:
        return "unknown"
    counts: dict[str, int] = {}
    for v in known:
        counts[v] = counts.get(v, 0) + 1
    return max(sorted(counts), key=lambda k: counts[k])
