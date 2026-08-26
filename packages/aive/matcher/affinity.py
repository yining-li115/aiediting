"""How close two semantic roles or two shot scales are.

These tables are the difference between "no exact match, give up" and "no
exact match, use the most reasonable substitute". The product must never tell
a user their footage cannot be cut because they lack one specific shot.
"""

from __future__ import annotations

from ..specs.enums import CameraMotion, SemanticRole, ShotType

# Roles that can stand in for each other, with how good the substitute is.
# Asymmetry is intentional: a detail shot is a decent stand-in for food, but
# an opening establishing shot is not a stand-in for a close-up of a dish.
ROLE_AFFINITY: dict[tuple[str, str], float] = {
    ("opening_environment", "environment"): 0.75,
    ("environment", "opening_environment"): 0.7,
    ("ending_environment", "environment"): 0.75,
    ("environment", "ending_environment"): 0.7,
    ("ending", "ending_environment"): 0.8,
    ("ending_environment", "ending"): 0.8,
    ("food", "detail"): 0.7,
    ("detail", "food"): 0.6,
    ("person_activity", "action"): 0.8,
    ("action", "person_activity"): 0.8,
    ("person_activity", "interaction"): 0.7,
    ("interaction", "person_activity"): 0.7,
    ("action", "transit"): 0.5,
    ("transit", "environment"): 0.6,
    ("environment", "transit"): 0.55,
    ("reaction", "interaction"): 0.6,
    ("interaction", "reaction"): 0.6,
}

# Shot scales form a ladder; adjacent rungs substitute reasonably, distant
# ones do not - cutting from an extreme wide to an extreme close-up in place
# of a medium changes the grammar, not just the framing.
SCALE_LADDER: tuple[ShotType, ...] = (
    "extreme_wide",
    "wide",
    "medium",
    "close_up",
    "extreme_close_up",
)
_LADDER_INDEX = {scale: i for i, scale in enumerate(SCALE_LADDER)}


def role_affinity(wanted: SemanticRole, have: SemanticRole) -> float:
    if wanted == have:
        return 1.0
    if wanted == "unknown" or have == "unknown":
        return 0.35
    return ROLE_AFFINITY.get((wanted, have), 0.0)


def scale_affinity(wanted: ShotType, have: ShotType) -> float:
    if wanted == have:
        return 1.0
    if wanted == "unknown" or have == "unknown":
        return 0.4
    if wanted == "pov" or have == "pov":
        return 0.4
    a, b = _LADDER_INDEX.get(wanted), _LADDER_INDEX.get(have)
    if a is None or b is None:
        return 0.3
    distance = abs(a - b)
    return {0: 1.0, 1: 0.6, 2: 0.3}.get(distance, 0.1)


def motion_affinity(wanted: CameraMotion | None, have: CameraMotion) -> float:
    if wanted is None or wanted == "unknown" or have == "unknown":
        return 0.5
    if wanted == have:
        return 1.0
    # A static shot sits neutrally next to anything; a wrong-direction pan
    # does not, which is why only static gets the free pass.
    if "static" in (wanted, have):
        return 0.5
    slow = {"slow_pan", "tilt", "tracking"}
    fast = {"fast_pan", "zoom_in", "zoom_out"}
    if (wanted in slow and have in slow) or (wanted in fast and have in fast):
        return 0.7
    return 0.25
