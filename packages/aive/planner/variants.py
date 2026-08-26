"""Candidate variation strategy.

Candidates must differ *by construction*. Raising the sampling temperature and
hoping for variety produces candidates that differ in wording but agree in
structure - which makes best-of-N measure noise rather than choice.

Each variant changes one editing decision in a direction a human editor would
recognise, so when the critic prefers one we learn something about taste
rather than about the sampler.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    name: str
    # Multiplies the base shot length: <1 means more, shorter shots.
    pace_factor: float = 1.0
    transition_density: float = 0.10
    music_sync_strength: float = 0.5
    # Multiplies the opening shot's length - a longer opening reads calmer.
    opening_factor: float = 1.0
    # Rotate the middle sections to change the narrative order.
    section_rotation: int = 0
    # Alternate wide/close instead of following the pool's natural mix.
    alternate_scale: bool = False
    description: str = ""


VARIANTS: tuple[Variant, ...] = (
    Variant(
        name="baseline",
        description="pool-driven pacing, restrained transitions",
    ),
    Variant(
        name="faster",
        pace_factor=0.72,
        transition_density=0.12,
        music_sync_strength=0.75,
        description="shorter shots, tighter to the beat",
    ),
    Variant(
        name="slower",
        pace_factor=1.4,
        transition_density=0.06,
        music_sync_strength=0.35,
        opening_factor=1.3,
        description="longer holds, fewer cuts",
    ),
    Variant(
        name="restructured",
        section_rotation=1,
        description="same material, different narrative order",
    ),
    Variant(
        name="rhythmic",
        pace_factor=0.85,
        transition_density=0.22,
        music_sync_strength=0.85,
        alternate_scale=True,
        description="alternating shot scale, stronger beat alignment",
    ),
    Variant(
        name="long_opening",
        pace_factor=1.1,
        transition_density=0.05,
        opening_factor=1.8,
        description="establishing hold, then settles",
    ),
)


def pick(count: int) -> list[Variant]:
    """First ``count`` variants, cycling if more are requested than defined."""
    if count <= len(VARIANTS):
        return list(VARIANTS[:count])
    out = list(VARIANTS)
    while len(out) < count:
        out.append(VARIANTS[len(out) % len(VARIANTS)])
    return out
