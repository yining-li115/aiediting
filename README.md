# AI Video Editor — Lite MVP

Conversational, stateful, taste-aware video editing agent.
The authoritative plan is [docs/TECHNICAL_PLAN.md](docs/TECHNICAL_PLAN.md); model
selection lives in [docs/MODEL_SELECTION.md](docs/MODEL_SELECTION.md).

Current state: **build block A (skeleton) complete** — see docs/TECHNICAL_PLAN.md §13.

## Setup

```bash
uv sync --extra dev          # Python 3.11+, pinned by pyproject
uv run aive db init          # create the schema
```

`ffmpeg` and `ffprobe` must be on PATH.

## Verify block A end to end

```bash
uv run python scripts/make_test_media.py --count 3 --seconds 8
uv run aive validate examples/timeline_demo.json --media-root data/media
uv run aive render   examples/timeline_demo.json --out out/demo.mp4 --quality hq
uv run pytest -q
```

`examples/timeline_demo.json` is a hand-written TimelineSpec. Passing
validation and rendering to a playable MP4 is block A's completion criterion,
which currently holds: 6.02s at 1080x1920, 44 tests green, all offline.

## Commands

| command | status |
|---|---|
| `aive db init` | working |
| `aive validate` | working — the 10 timeline invariants |
| `aive render` | working — proxy / hq / partial range |
| `aive inspect` | working — plan or timeline summary |
| `aive ingest` / `analyze` | block B |
| `aive plan` / `match` / `run` | block C |

## Layout

```
packages/aive/specs/      domain models + validation   <- the contract
packages/aive/providers/  one protocol per model role, mocks included
packages/aive/renderer/   FFmpeg backend + pure command compiler
packages/aive/db/         19 tables
packages/aive/jobs.py     in-process job runner
apps/api/                 FastAPI surface
```

Two boundaries that must not blur:

* the renderer reads `TimelineSpec` and never `EditPlanSpec`;
* `matcher/` makes editing decisions, `renderer/compiler/` only translates.

## Configuration

Copy `.env.example` to `.env`. Every model role is configured separately —
there is deliberately no global `AIVE_MODEL`. All roles default to `mock`, so
the whole skeleton runs offline with no API keys and no cost.
