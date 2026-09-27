"""Tolerant boundary model for untrusted structured model answers."""

from __future__ import annotations

from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, model_validator


def _accepts_none(field: Any) -> bool:
    annotation = getattr(field, "annotation", None)
    if annotation is None:
        return True
    return type(None) in get_args(annotation) or annotation is type(None)


def _max_len(field: Any) -> int | None:
    for constraint in getattr(field, "metadata", ()):
        limit = getattr(constraint, "max_length", None)
        if limit is not None:
            return int(limit)
    return None


class ModelAnswer(BaseModel):
    """Ignore helpful extras and trim only declared string/list bounds."""

    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _trim_to_bounds(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, field in cls.model_fields.items():
            if data.get(name, ...) is None and not _accepts_none(field):
                del data[name]
                continue
            limit = _max_len(field)
            if limit is None or name not in data:
                continue
            value = data[name]
            if isinstance(value, (str, list)) and len(value) > limit:
                data[name] = value[:limit]
        return data


__all__ = ["ModelAnswer"]
