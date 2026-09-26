"""Common base model and identifier helpers."""

from __future__ import annotations

import uuid
from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def _accepts_none(field: Any) -> bool:
    """Whether None is a legal value for this field - and therefore meaningful.

    On an optional field None is an answer ("the user set no duration") and
    must survive. On a list field it is a model habit and must not.
    """
    annotation = getattr(field, "annotation", None)
    if annotation is None:
        return True
    return type(None) in get_args(annotation) or annotation is type(None)


def _max_len(field: Any) -> int | None:
    """The ``max_length`` a field declares, if any."""
    for constraint in getattr(field, "metadata", ()):
        limit = getattr(constraint, "max_length", None)
        if limit is not None:
            return int(limit)
    return None


class ModelAnswer(BaseModel):
    """Base for schemas a **model** fills in, as opposed to our own code.

    Two differences from ``Spec``, both because of who is writing the object.

    ``extra="ignore"``. Spec forbids extras so that a typo in our code fails
    loudly. Here the author is a language model, and forbidding extras means a
    model that adds one helpful field it was not asked for has its entire
    answer thrown away - a real planner response was discarded over
    ``implementation_notes``, after 47 seconds and 2,234 output tokens.

    Over-long strings and lists are truncated rather than rejected, for the
    same reason: a 220-character sentence where 200 were asked for is a long
    answer, not a failed one. Bounds still exist - they stop a model from
    returning a novel - they just stop costing a whole call to enforce.

    Type and range constraints are NOT relaxed. A shot type outside the
    vocabulary or a score on the wrong scale is genuinely unusable, and
    coercing it would put a wrong value into the pool silently.
    """

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _trim_to_bounds(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for name, field in cls.model_fields.items():
            if data.get(name, ...) is None and not _accepts_none(field):
                # A model writing `"style": null` means the same thing as
                # leaving the key out, but pydantic reads it as "the wrong
                # type" and discards the whole answer. One null cost a
                # 25-second, 1,058-token intent call.
                del data[name]
                continue
            limit = _max_len(field)
            if limit is None or name not in data:
                continue
            value = data[name]
            if isinstance(value, (str, list)) and len(value) > limit:
                data[name] = value[:limit]
        return data
