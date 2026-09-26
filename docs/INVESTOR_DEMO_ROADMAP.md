# Investor Demo Roadmap

Status: product-complete target, implemented through sequential vertical
slices.

The demo is not a reduced product concept. It must show the complete product
story convincingly while the implementation is built in an order that keeps
every stage testable and reusable.

## 1. Investor-facing story

The final demonstration should show one continuous project:

1. Choose Autonomous Edit or Reference Remake.
2. Upload original videos/photos and optionally one reference video.
3. Watch meaningful processing progress: ingest, understanding, planning,
   matching, preview, and review.
4. Receive a coherent first cut with real video and audio.
5. Ask for a localized change in natural language.
6. Lock an accepted section and make one manual edit.
7. Ask the Agent to continue editing without damaging locked or manual work.
8. Compare versions and undo or restore.
9. In reference mode, inspect how reference sections map to the new result and
   where the system adapted because footage was missing.
10. Export a final playable video from the committed revision.

This is the product narrative. Implementation milestones below are delivery
order, not permission to create disposable mini-products.

## 2. Demo completion criteria

The investor demo is complete only when it has:

- a polished Web workspace, not a CLI-only path;
- real upload, analysis, generation, preview, iterative editing, versioning,
  and export;
- both autonomous and reference-remake modes;
- a visible explanation of progress and meaningful limitations;
- at least one real model-backed media observation path;
- deterministic fixtures and a rehearsed golden project for reliability;
- failure recovery that preserves the last usable revision;
- no decorative control presented as working functionality.

The golden project is a reliability fixture, not a prerecorded fake. Its
inputs, intermediate artifacts, and expected revision history are retained so
the same real pipeline can be rerun and diagnosed.

## 3. Implementation sequence

### Stage 0 - Engineering baseline

Deliver:

- checkpoint the current worktree before structural migration;
- authoritative architecture and AI coding rules;
- target module ownership and dependency checks;
- current test/build commands documented and reproducible.

Exit condition: a coding agent can identify where a new behavior belongs and
which existing implementation it replaces without inventing another layer.

### Stage 1 - Project kernel

Deliver:

- `ProjectDoc`, integer time contracts, Patch, locks, and immutable revisions;
- `ProjectService` candidate creation, validation, commit, conflict, undo, and
  restore;
- database schema with `projects.current_revision_id`;
- generated frontend contract types.

Exit condition: without any model, tests can create a project, apply local
edits, reject stale/locked edits, commit revisions, and restore history.

### Stage 2 - Media and render vertical slice

Status: the model-free vertical slice is implemented: project-scoped upload,
deterministic probe, AssetRef Revision commit, durable proxy/thumbnail Jobs,
physical-Shot analysis, timed ASR evidence, validated cross-Shot semantic
Moment grouping, revision-bound FFmpeg preview/range preview/HQ export
Artifacts, HTTP media delivery, and browser playback. See `RENDERING_V1.md`
for the current executable subset and explicit unsupported capabilities.

Deliver:

- upload into project-scoped storage;
- probe, proxy, thumbnails, audio extraction, and moments;
- adapt the existing FFmpeg renderer to consume ProjectDoc revisions;
- Job progress and revision-bound preview/export artifacts;
- Web upload, progress, preview, and export for a hand-authored revision.

Exit condition met for a hand-authored executable ProjectDoc revision: browser
upload to playable preview/export works without a model and survives a page
reload. Automatically authoring that first executable timeline belongs to
Stage 3.

### Stage 3 - Autonomous first cut

Status: the model-capable vertical slice is implemented behind an explicit
VideoAgentHarness runtime boundary. The Harness freezes an AgentRunContext,
invokes an offline or Volcengine Director, and owns deterministic review plus
at most one bounded AutoFix. The application layer then creates a typed
first-cut Patch through ProjectService, stores one immutable candidate Agent
Revision, automatically renders it, runs deterministic plus optional
Volcengine post-render review, and atomically accepts it only after approval.
The browser exposes progress, explanation, and the Reviewer summary. Failed
providers, exhausted repairs, render failures, and review rejection preserve
the previous revision. A live Volcengine golden-run, rendered captions/music,
Reviewer-driven correction Patches, and iterative chat edits remain.

Deliver:

- one production-configured media observer plus offline fake;
- `search_moments` and `inspect_media` tool contracts;
- Video Agent Harness, Director context, and tool loop;
- first-cut Patch generation and preview review;
- user-facing draft explanation based on actual operations.

Exit condition: a real footage set produces a coherent editable draft through
the browser, and a failed model call leaves the project intact.

### Stage 4 - Continuous editing

Deliver:

- natural-language scope resolution;
- local Patch operations for trim, replace, speed, caption, music, and crop;
- selection-aware editing, locks, manual edits, undo, and version comparison;
- affected-range preview with transition/audio handles.

Exit condition: the full generate/edit/lock/manual-edit/agent-edit/undo/export
scenario passes as an end-to-end test.

### Stage 5 - Reference remake

Deliver:

- immutable Reference Blueprint with evidence;
- Remake Policy and user-selected preserve/adapt/exclude dimensions;
- moment matching and multi-shot Alignment;
- adaptation decisions and reference-aware review;
- reference/output comparison in the Web workspace.

Exit condition: the same footage can be edited autonomously or against a
reference, missing footage is explained, and localized reference feedback
updates only the mapped output range.

### Stage 6 - Remotion and presentation quality

Deliver:

- Remotion Player reading the shared ProjectDoc timing model;
- captions, title cards, brand components, and approved motion graphics;
- preview/export consistency regression fixtures;
- polished progress, comparison, history, and recovery UX.

Exit condition: visual presentation is investor-ready and every visible
control is connected to real state.

### Stage 7 - Demo reliability and observability

Deliver:

- golden demo project and reset script;
- warm-cache and cold-cache rehearsal;
- structured run/job/revision logs and model usage accounting;
- timeout, retry, cancellation, stale-job, and worker-restart tests;
- documented fallback procedure that never misrepresents prerecorded output as
  a new live result.

Exit condition: the demo can be rehearsed repeatedly, failures are diagnosable,
and the last usable result remains available.

## 4. What starts now

The first coding milestone is Stage 1, Project kernel. Do not begin by adding
more models, more planner variants, Remotion, pgvector, or Celery. Those depend
on stable project and job identities and otherwise deepen the current split
between plan, timeline, UI state, and generated output.

Before Stage 1 code changes, produce and review:

- the exact `ProjectDoc` v1 schema;
- the supported Patch v1 operation list;
- revision and commit state transitions;
- the initial database migration strategy from the current development data;
- tests for stale revisions, idempotency, locks, ripple effects, and undo.

## 5. Scope control

Features are never silently discarded. Work not in the current stage remains
on this roadmap and must reuse the same contracts. A stage may add only the
minimum interfaces required for its exit condition; future features do not
justify unused abstractions in present code.
