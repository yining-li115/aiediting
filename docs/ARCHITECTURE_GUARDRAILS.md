# Architecture Guardrails

Status: authoritative engineering constraints for the investor-demo build.

This document translates the architecture proposal in
`AI_Video_Agent_Technical_Implementation_v0.5.pdf` into repository rules. The
goal is not to reduce the final product scope. The goal is to build the full
experience through coherent vertical slices without accumulating parallel
state, duplicated business rules, or framework-driven structure.

## 1. System invariant

`ProjectDoc` is the single authoritative description of the edited work.

- Every saved state is an immutable `ProjectRevision` containing a complete
  `ProjectDoc` snapshot.
- `projects.current_revision_id` points to the current committed revision.
- A preview, export, review result, reference alignment, or job always names
  the revision it was produced from.
- An `EditPlan` may explain creative intent, but it is not a second editable
  timeline.
- Search indexes, proxies, embeddings, checkpoints, and cached previews are
  derived state. They may be rebuilt and must never overwrite the project.

## 2. One edit path

Manual editing and agent editing use the same protocol:

```text
current revision
  -> typed Patch with base_revision_id and idempotency_key
  -> ProjectService validation
  -> immutable candidate revision
  -> optional preview and review
  -> atomic commit if the base revision is still current
```

The service, not the model or UI, enforces:

- project and asset authorization;
- stable IDs and valid time ranges;
- effect capability support;
- content, duration, position, and audio locks;
- ripple effects and transition/audio handles;
- idempotency and optimistic concurrency;
- current-revision comparison before commit.

No UI component, API router, Agent tool, or Worker may update the project
document directly.

## 3. Time model

Avoid floating-point seconds in the authoritative document.

- Source ranges use integer ticks plus an explicit `timescale`, or preserved
  source PTS where required.
- Output ranges use integer frames.
- Project frame rate is a rational `{num, den}`.
- Intervals are left-closed and right-open: `[start, end)`.
- Speed is represented as an explicit rational mapping from source duration to
  output duration.
- Adapters convert to seconds only at boundaries such as FFmpeg commands.

## 4. Dependency direction

Dependencies point inward:

```text
Web -> HTTP API -> application services -> domain contracts
Workers ---------> application services -> domain contracts
Adapters --------> provider/repository ports -> domain contracts
```

The domain layer must not import:

- FastAPI or HTTP request objects;
- SQLAlchemy models or sessions;
- cloud/vendor SDKs;
- FFmpeg subprocess code;
- React/TypeScript frontend code.

API routes may parse requests, invoke one application service, and map typed
errors to HTTP responses. They must not contain SQL queries, model prompts,
editing rules, or media commands.

## 5. Target repository layout

This is the target ownership model. Create a directory only when the first
real implementation needs it; do not scaffold empty packages.

```text
apps/
  web/                         React/TypeScript workspace
  api/
    main.py                    composition root
    routes/                    thin HTTP adapters

workers/
  agent.py                     Director process entry point
  media.py                     media task entry point
  render.py                    rendering task entry point

packages/aive/
  contracts/                   ProjectDoc, Patch, Job, Reference contracts
  project/                     ProjectService, patching, revisions, locks
  agent/                       Director, context, tools
  media/                       probe, proxy, analysis, moments
  reference/                   blueprint, policy, alignment, review
  rendering/                   renderer port, FFmpeg, Remotion adapter
  providers/                   model/provider adapters
  storage/                     object storage adapters

skills/                        approved, versioned creative methods
migrations/                    append-only schema history
tests/
  unit/
  contract/
  integration/
  e2e/
  fixtures/
```

## 6. Process boundaries

Logical boundaries are required before deployment boundaries.

- API: authorization, Project Service entry points, upload sessions, jobs,
  events, commits, and exports.
- Director: reads context and evidence, then proposes tool calls and Patches.
- Media Worker: probe, normalize, proxy, analyze, transcribe, and index.
- Render Worker: render a named revision and produce an Artifact.

Model-driven Director work runs inside the `VideoAgentHarness` runtime
boundary. The Harness owns model context, provider invocation, workflow-local
review, and bounded recovery; it never owns ProjectDoc persistence, Patch
commit, rendering, SQL, or HTTP orchestration.

For the local demo these may run in one machine and may initially use an
in-process dispatcher. Their contracts must already carry project, revision,
request, and job identity so they can later move to durable workers without
changing domain behavior.

## 7. FFmpeg and Remotion ownership

FFmpeg is the media foundation:

- probe and validate input;
- create proxies, thumbnails, waveforms, and normalized audio;
- trim, crop, concatenate, mix, encode, and package exports;
- precompute supported effects.

Remotion is the React composition and preview layer:

- browser Player integration;
- captions, title cards, branding, and parameterized motion graphics;
- frame-driven compositions that read the same ProjectDoc contract.

Generated Remotion implementations follow the candidate -> sandbox validation
-> explicit approval -> immutable registry lifecycle in
`REMOTION_COMPONENTS_V1.md`. Candidate source and bundles are stored as data,
outside the product source tree. Registry publication alone does not authorize
a capability in ProjectDoc; the selected renderer must also support it.

Both sit behind rendering ports. Neither owns project state. Adding Remotion
must not introduce a Remotion-only project format or duplicate timing rules.

## 8. Data ownership

The intended durable entities are:

- `projects` and immutable `project_revisions`;
- `edit_operations` with idempotency identity;
- `assets`, `moments`, and transcript segments;
- immutable `reference_blueprints` and revision-bound reference context;
- `jobs`, `artifacts`, `agent_runs`, `usage_events`, and `outbox_events`;
- versioned Skills/style resources and explicit project bindings.

PostgreSQL is the production target. SQLite is allowed only as a local demo
adapter while the domain and repository contracts remain database-neutral.
Committed migrations are append-only.

## 9. Error and failure rules

- Unsupported effects fail explicitly or return a typed confirmation request.
- Model output is untrusted data and must pass Pydantic and domain validation.
- Expired jobs may save historical artifacts but cannot move the current
  revision pointer.
- A failed analysis or render never clears a previously valid project.
- Cancellation is recorded state, not only an in-memory task cancellation.
- Catch broad exceptions only at process boundaries where they are converted
  into a failed Job with a diagnostic identifier.

## 10. Testing boundaries

- Unit tests: pure domain invariants, Patch application, locks, and time math.
- Contract tests: Pydantic/JSON Schema compatibility and generated TS types.
- Integration tests: database transaction, storage, FFmpeg, jobs, and provider
  adapters.
- End-to-end tests: upload through preview, edit, revision, undo, and export.
- Media regression fixtures: real short clips with audio, variable frame rate,
  rotation, photos, and reference footage.

The critical continuous scenario is:

```text
generate first draft
  -> edit only the middle
  -> lock the opening
  -> manual edit
  -> agent edits the ending
  -> undo
  -> reference-guided local adjustment
  -> export the committed revision
```
