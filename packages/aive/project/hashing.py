from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel


def canonical_model_hash(model: BaseModel) -> str:
    """Return a stable SHA-256 for one closed Pydantic contract value."""

    payload = json.dumps(
        model.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
