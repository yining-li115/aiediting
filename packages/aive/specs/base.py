"""Common base model and identifier helpers."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SPEC_VERSION = "1.0"


def new_id(prefix: str) -> str:
    """Short, readable, collision-safe identifier: ``clip_9f2a41b0``."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Spec(BaseModel):
    """Base for every domain model.

    ``extra="forbid"`` is deliberate: a typo in a field name should fail loudly
    at parse time rather than silently produce a spec that is missing the field
    the rest of the pipeline reads.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class VersionedSpec(Spec):
    """A spec that is persisted and therefore must carry its format version.

    Every stored spec is checked on read. Without this, a format change makes
    old projects fail somewhere deep in the pipeline instead of at the point
    where they are loaded.
    """

    spec_version: str = SPEC_VERSION


class Provenance(Spec):
    """Where a spec came from. Needed for debugging and for ablations."""

    reference_id: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    candidate_index: int = 0
    seed: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
