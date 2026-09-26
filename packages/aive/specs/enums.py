"""Controlled vocabularies shared by every layer.

Two rules that hold everywhere in this package:

* Every time value is **seconds as a float**. Never frame numbers, never
  timecode strings. Passing frame numbers across module boundaries is the
  single easiest way to introduce off-by-one drift in this project.
* Every 0-1 score is a closed interval [0.0, 1.0].
"""

from typing import Literal

# --- semantics ---------------------------------------------------------------

# Shared by EditPlanSpec slots and MaterialSegment so the matcher can compare
# the two fields directly without a translation table.
SemanticRole = Literal[
    "opening_environment",  # establishing shot
    "environment",
    "transit",  # travelling / moving between places
    "person_activity",
    "action",
    "interaction",
    "detail",
    "food",
    "reaction",
    "text_card",
    "ending_environment",
    "ending",
    "unknown",
]

ShotType = Literal[
    "extreme_wide",
    "wide",
    "medium",
    "close_up",
    "extreme_close_up",
    "pov",
    "unknown",
]

CameraMotion = Literal[
    "static",
    "handheld",
    "slow_pan",
    "fast_pan",
    "tilt",
    "zoom_in",
    "zoom_out",
    "tracking",
    "drone",
    "unknown",
]

# --- editing grammar ---------------------------------------------------------

Transition = Literal[
    "hard_cut",
    "dissolve",
    "fade_to_black",
    "whip_pan",  # declared but not implemented in the MVP
    "zoom_blur",  # declared but not implemented in the MVP
]

# The renderer rejects anything outside this set. Keeping the enum wider than
# the implementation lets a plan express intent we cannot execute yet, which
# the validator then reports instead of silently dropping.
IMPLEMENTED_TRANSITIONS: frozenset[str] = frozenset(
    {"hard_cut", "dissolve", "fade_to_black"}
)

Pace = Literal["slow", "medium", "medium_fast", "fast"]
BeatAlignment = Literal["none", "soft", "strict"]
SubtitleDensity = Literal["none", "low", "medium", "high"]
SubtitleSource = Literal["none", "asr", "generated"]
EditMode = Literal["auto", "reference_remake"]

# --- adaptation --------------------------------------------------------------

AdaptationAction = Literal[
    "trim",
    "speed",
    "crop",
    "reframe",
    "skip",
    "merge",
    "replace_role",
    "extend",
    "shorten",
]

# --- assets ------------------------------------------------------------------

# Photos are a first-class input, not an afterthought: a phone camera roll
# mixes them with video, and asking the user to separate the two is asking for
# exactly the work this product exists to remove.
AssetKind = Literal["video", "photo"]

# A photo has no inherent duration. It gets a nominal one so it can occupy a
# segment like anything else, but the matcher is free to give it whatever
# length a slot wants - which makes photos unusually easy to place.
PHOTO_NOMINAL_DURATION = 3.0

VIDEO_SUFFIXES: frozenset[str] = frozenset(
    {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg"}
)
PHOTO_SUFFIXES: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".tiff"}
)
AUDIO_SUFFIXES: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
)

# --- user intent -------------------------------------------------------------

# The canonical constraint vocabulary.
#
# This is a contract, not a lookup table: the parser (LLM or rules) may only
# emit these values, and the planner must handle every one of them. Free text
# would be silently ignored by the planner, which is worse than not extracting
# it at all - the user would see their instruction accepted and disregarded.
UserConstraint = Literal[
    "few_transitions",  # "不要太多转场"
    "no_transitions",
    "no_subtitles",  # "不要字幕"
    "few_subtitles",
    "slow_pace",  # "慢一点"
    "fast_pace",  # "快一点"
    "keep_original_sound",  # "保留原声"
    "no_music",
    "keep_opening",  # "开头别动" - honoured by the agent layer
]

# Style tags the planner reacts to. Unknown tags are kept rather than dropped:
# they still feed emotion matching, and an unrecognised style is a hint about
# vocabulary to add, not noise.
KNOWN_STYLES: frozenset[str] = frozenset(
    {
        "travel",
        "daily",
        "relaxed",
        "energetic",
        "cinematic",
        "minimal",
        "warm",
        "food",
    }
)

# --- limits ------------------------------------------------------------------

# A shot below this length reads as a flash rather than as content, so we treat
# it as a technical defect rather than a stylistic choice.
MIN_CLIP_DURATION = 0.30

MIN_SPEED = 0.25
MAX_SPEED = 4.0

# Float comparison tolerance for timeline arithmetic, in seconds.
TIME_EPSILON = 1e-3

MIN_DISSOLVE_DURATION = 0.1
MAX_DISSOLVE_DURATION = 1.0
