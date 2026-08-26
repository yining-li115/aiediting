"""Per-call model logging.

Every request to a model is appended to ``logs/ai-calls-YYYY-MM-DD.jsonl``.
Model behaviour is the least predictable part of this system and the hardest
to reason about after the fact: when a segment gets a strange label, the only
way to tell a bad prompt from a bad model from a bad frame is to read what was
actually sent and what actually came back.

Two deliberate choices:

* base64 image payloads are replaced by a short placeholder. A single frame is
  hundreds of kilobytes and would make the log unreadable and unsearchable;
  the frame path is recorded instead, so the image is still one command away.
* failures are logged with the same shape as successes. A log that only
  contains what worked cannot explain what did not.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

# Roughly a short sentence: enough to see the shape of a response, short
# enough that a hundred calls stay skimmable.
_PREVIEW_CHARS = 4000


def log_dir() -> Path:
    return Path(os.environ.get("AIVE_LOG_DIR", "logs"))


def enabled() -> bool:
    return os.environ.get("AIVE_LOG_CALLS", "1") not in ("0", "false", "no")


def _redact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip base64 payloads, keep everything that explains the request."""
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            out.append({"role": message.get("role"), "content": content})
            continue
        parts: list[Any] = []
        for part in content or []:
            kind = part.get("type")
            if kind == "text":
                parts.append({"type": "text", "text": part.get("text", "")})
            elif kind in ("image_url", "video_url"):
                url = (part.get(kind) or {}).get("url", "")
                head = url.split(",", 1)[0] if url.startswith("data:") else url
                parts.append(
                    {
                        "type": kind,
                        "inline": head,
                        "bytes": max(0, len(url) - len(head)),
                    }
                )
            else:
                parts.append({"type": kind})
        out.append({"role": message.get("role"), "content": parts})
    return out


def record(
    *,
    role: str,
    model: str,
    prompt_version: str | None = None,
    mode: str | None = None,
    subject: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    response: str | None = None,
    parsed: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    status: str = "ok",
    error: str | None = None,
    attempt: int = 0,
) -> None:
    if not enabled():
        return

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "role": role,
        "model": model,
        "prompt_version": prompt_version,
        "mode": mode,
        "subject": subject,  # which segment / project this call was about
        "status": status,
        "attempt": attempt,
        "latency_ms": latency_ms,
        "usage": usage or {},
        "messages": _redact(messages or []),
        "response": (response or "")[:_PREVIEW_CHARS],
        "parsed": parsed,
        "error": error,
    }

    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"ai-calls-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    line = json.dumps(entry, ensure_ascii=False)
    # Appends from concurrent coroutines run in one thread, but the renderer
    # and job worker may not; a lock keeps lines from interleaving.
    with _LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
