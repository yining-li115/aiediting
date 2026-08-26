"""Interaction layer: EditOperation, EditScope, ProjectVersion.

These are first-class domain models, not UI helpers.

The rule that makes the whole interaction design work: **the editor UI must
not modify a TimelineSpec directly.** It constructs an EditOperation and goes
through the same executor the agent uses. Otherwise chat edits and manual
edits drift into two incompatible states and undo, versioning, locking and
preference capture all break at once.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import Spec, VersionedSpec, new_id

# --- operations --------------------------------------------------------------

OperationType = Literal[
    # timeline level - local, precise, deterministic
    "replace_clip",
    "trim_clip",
    "extend_clip",
    "delete_clip",
    "reorder_clips",
    "set_speed",
    "set_crop",
    "edit_caption",
    "move_caption",
    "remove_subtitles",
    "replace_music",
    "set_audio_mix",
    "lock_clip",
    "unlock_clip",
    # plan level - global, semantic
    "change_rhythm",
    "change_story_structure",
    "change_style",
    "change_audio_mood",
    "change_reference_strength",
    "change_subtitle_density",
    # compound - needs re-planning
    "regenerate_section",
    "regenerate_all",
    "apply_reference_layer",
    # read-only / meta
    "inspect_project",
    "search_material",
    "list_alternatives",
    "render_preview",
    "compare_versions",
    "undo",
    "redo",
    "revert_to",
    "commit_version",
]

Route = Literal["fast", "deep"]

# Routing is decided statically by operation type, never by the model at call
# time. "make the subtitles smaller" must not be able to trigger the expensive
# video model just because the model felt uncertain.
DEEP_OPERATIONS: frozenset[str] = frozenset(
    {
        "change_rhythm",
        "change_story_structure",
        "change_style",
        "change_audio_mood",
        "change_reference_strength",
        "regenerate_section",
        "regenerate_all",
        "apply_reference_layer",
    }
)

# Operations that require the matcher to re-bind material.
REMATCH_OPERATIONS: frozenset[str] = frozenset(
    {
        "replace_clip",
        "delete_clip",
        "change_rhythm",
        "change_story_structure",
        "change_style",
        "change_audio_mood",
        "change_reference_strength",
        "regenerate_section",
        "regenerate_all",
        "apply_reference_layer",
    }
)


def route_for(operation: str) -> Route:
    return "deep" if operation in DEEP_OPERATIONS else "fast"


# --- scope -------------------------------------------------------------------

ScopeType = Literal["time_range", "section", "clip_set", "global"]


class EditScope(VersionedSpec):
    """Which part of the project an operation is allowed to touch.

    ``global`` must be explicitly supported by the user's instruction
    ("everything", "recut it"). An agent must never default to global just
    because resolving a narrower scope was hard.
    """

    type: ScopeType = "clip_set"
    start: float | None = None
    end: float | None = None
    section_ids: list[str] = Field(default_factory=list)
    clip_ids: list[str] = Field(default_factory=list)
    # Things the user asked to keep untouched, e.g. "but leave the opening".
    preserve: list[str] = Field(default_factory=list)
    locked_elements: list[str] = Field(default_factory=list)


Origin = Literal["chat", "editor", "agent_plan", "system"]


class EditOperation(VersionedSpec):
    op_id: str = Field(default_factory=lambda: new_id("op"))
    turn_id: str | None = None
    operation: OperationType
    params: dict[str, Any] = Field(default_factory=dict)
    scope: EditScope = Field(default_factory=EditScope)
    origin: Origin = "chat"

    @property
    def route(self) -> Route:
        return route_for(self.operation)

    @property
    def needs_rematch(self) -> bool:
        return self.operation in REMATCH_OPERATIONS


class EditCommandPlan(Spec):
    """What the agent produced for one user instruction."""

    turn_id: str = Field(default_factory=lambda: new_id("t"))
    instruction: str
    operations: list[EditOperation] = Field(default_factory=list)
    resolved_scope: EditScope = Field(default_factory=EditScope)
    explanation: str | None = None


# --- versions ----------------------------------------------------------------


class ProjectVersion(VersionedSpec):
    """One node in the project's version DAG.

    Full snapshots, not deltas. The specs are tens of kilobytes, so storing
    complete copies makes "go back to v3" an O(1) lookup that cannot be
    corrupted by a bug in delta application. The operation list is kept for
    explainability and as preference data, not for reconstruction.
    """

    version_id: str = Field(default_factory=lambda: new_id("v"))
    project_id: str
    parent_version_id: str | None = None
    turn_id: str | None = None
    user_instruction: str | None = None
    operations: list[EditOperation] = Field(default_factory=list)
    plan_snapshot_id: str | None = None
    timeline_snapshot_id: str | None = None
    preview_render_id: str | None = None
    accepted: bool | None = None


class Lock(Spec):
    """A user-visible guarantee. Enforced in the executor, never in a prompt."""

    element_type: Literal["clip", "section", "music", "subtitle_style"]
    element_id: str
