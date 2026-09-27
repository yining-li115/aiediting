"""Storage layout.

Keys are structured as ``{project_id}/{kind}/{name}`` so that everything one
project owns is a single prefix - which is what makes "delete this project" or
"how much is this user storing" one operation rather than a survey.

Adding a kind is adding a constant here. The storage backends do not enumerate
kinds, so nothing else has to change.
"""

from __future__ import annotations

from pathlib import Path

# Known kinds. Extend freely - these are conventions, not an enum the
# backends validate against.
ORIGINAL = "original"  # exactly what the user uploaded
PROXY = "proxy"  # normalised 540p copy used for analysis
THUMBNAIL = "thumbnail"  # compact still used by the workspace
KEYFRAME = "keyframe"  # stills sent to the material model
ANALYSIS_AUDIO = "analysis-audio"  # short-lived ASR windows, never authority
MUSIC = "music"  # tracks uploaded for one project
RENDER = "render"  # finished and preview videos
REFERENCE = "reference"  # reference videos for remake mode
EXPORT = "export"  # NLE project files, later

# Shared across projects rather than owned by one.
SHARED = "_shared"


def project_key(project_id: str, kind: str, name: str) -> str:
    return f"{project_id}/{kind}/{name}"


def shared_key(kind: str, name: str) -> str:
    """For content that is not owned by a single project, such as the library
    music every project can choose from."""
    return f"{SHARED}/{kind}/{name}"


def project_prefix(project_id: str) -> str:
    return f"{project_id}/"


def safe_name(original: str, fallback: str) -> str:
    """A filename safe to use as a key component.

    User filenames arrive with spaces, emoji, non-ASCII and occasionally path
    separators. Only the extension is worth keeping; the identity of the file
    is the asset id, which the caller supplies.
    """
    suffix = Path(original).suffix.lower()
    if len(suffix) > 12 or not suffix.replace(".", "").isalnum():
        suffix = ""
    return f"{fallback}{suffix}"
