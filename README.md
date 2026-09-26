# AI Video Editor — Investor Demo Build

Conversational, stateful, taste-aware video editing Agent. The target is the
complete investor-demo workflow, delivered as coherent vertical slices rather
than a reduced feature demo.

The current product and architecture source is
[AI Video Agent Technical Implementation v0.5](docs/AI_Video_Agent_Technical_Implementation_v0.5.pdf).
Repository implementation rules and delivery order are defined in:

- [Architecture Guardrails](docs/ARCHITECTURE_GUARDRAILS.md)
- [Agentic Workspace Architecture](docs/AGENTIC_WORKSPACE_ARCHITECTURE.md)
- [Investor Demo Roadmap](docs/INVESTOR_DEMO_ROADMAP.md)
- [Investor Demo Completion Checklist](docs/INVESTOR_DEMO_CHECKLIST.md)
- [AI Coding Rules](docs/AI_CODING_RULES.md)
- [ProjectDoc v1 Contract](docs/PROJECT_DOC_V1.md)
- [Patch v1 Contract](docs/PATCH_V1.md)
- [Revision and Commit State Machine](docs/REVISION_COMMIT_STATE_MACHINE.md)
- [Remotion Component Registry and Sandbox](docs/REMOTION_COMPONENTS_V1.md)

Current state: the Project kernel, immutable Revision history, browser project
creation, media upload/understanding, autonomous first cut, revision-bound
preview/export, post-render Reviewer/AutoFix, and the first bounded continuous
editing conversation are connected. ProjectDoc remains the only authoritative
edit state and every accepted UI or Agent edit uses the typed Patch path. The
remaining editor, presentation, Reference Remake, live-model qualification,
and demo-reliability work is tracked in the completion checklist above.

## Setup

```bash
uv sync --extra dev          # Python 3.11+, pinned by pyproject
uv run alembic upgrade head  # create or upgrade the schema
```

`ffmpeg`, `ffprobe`, and fontconfig's `fc-match` must be on PATH. Fontconfig is
used to resolve the exact installed font file for `caption.clean@1.0.0`.

The generated Remotion component validator has a separate pinned Node runtime:

```bash
cd apps/remotion
npm install
npm run install-browser
```

This installs Remotion's managed Headless Shell under ignored `node_modules`;
it does not use the developer's Chrome profile. Candidate source and build
artifacts are stored under the configured object-storage root, not in Git.

## Verify block A end to end

```bash
uv run python scripts/make_test_media.py --count 3 --seconds 8
uv run aive validate examples/timeline_demo.json --media-root data/media
uv run aive render   examples/timeline_demo.json --out out/demo.mp4 --quality hq
uv run pytest -q
```

`examples/timeline_demo.json` is a hand-written TimelineSpec. Passing
validation and rendering to a playable MP4 is block A's completion criterion,
which is covered by the current offline test suite.

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
packages/aive/contracts/  ProjectDoc, Patch, Revision, and upload contracts
packages/aive/project/    authoritative edit and commit services
packages/aive/media/      upload and derived-media processing services
packages/aive/providers/  one protocol per model role, mocks included
packages/aive/renderer/   FFmpeg backend + pure command compiler
packages/aive/db/         SQLAlchemy repository adapters
packages/aive/jobs.py     in-process job runner
apps/api/                 FastAPI surface
```

Three boundaries that must not blur:

* ProjectDoc Revision is the only authoritative edit state;
* uploaded bytes and technical metadata do not become a second timeline;
* every UI or Agent edit commits through the same typed Patch path.

## Configuration

Copy `.env.example` to `.env`. Every model role is configured separately —
there is deliberately no global `AIVE_MODEL`. The Director must resolve to a
real model and has no offline or keyword fallback. Missing credentials,
invalid model output, and rejected tool decisions fail the Agent Job visibly
without changing the current Revision. Other roles still have explicit local
fixtures while their production paths are being qualified.

For owned/synthetic investor-demo footage, the Continuous Editing Director can
use pinned Gemini models through the existing provider port:

```bash
AIVE_ROLE_DIRECTOR_MODEL=gemini-3.6-flash
AIVE_ROLE_DIRECTOR_FALLBACK_MODELS=gemini-3.5-flash,gemini-3.1-flash-lite
```

Fallback is limited to quota, timeout, and temporary capacity failures. Invalid
model output and rejected Patches never trigger model failover; a rejected
Patch may instead be corrected by the same bounded tool loop after a no-write
ProjectService/renderer dry-run. Free-tier Gemini traffic may be used by Google
to improve its products, so this configuration is not for customer footage.

Run the live synthetic Continuous Editing qualification without touching the
developer database:

```bash
./.venv/bin/python scripts/check_continuous_edit_golden.py
```

It validates Trim, Move, Delete, Moment-grounded Replace, Crop/Center Reframe,
Constant Speed, Gain/Fade, clarification, immutable Revision history,
source-audio pairing, and real FFmpeg preview files. The command calls the
configured Director models and therefore consumes external quota.
