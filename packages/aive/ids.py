"""Opaque runtime identifier generation."""

from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    """Return a readable collision-safe identifier."""

    return f"{prefix}_{uuid.uuid4().hex[:8]}"


__all__ = ["new_id"]
