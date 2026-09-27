# Investor Demo Roadmap

Status: product-complete target, implemented through sequential vertical
slices.

The execution checklist is maintained in `INVESTOR_DEMO_CHECKLIST.md`. Update
that checklist only when a user-visible vertical path and its verification are
actually complete.

The demo is not a reduced product concept. It must show the complete product
story convincingly while the implementation is built in an order that keeps
every stage testable and reusable.

## 1. Investor-facing story

The final demonstration should show one continuous project:

1. Start an Autonomous Edit and optionally attach Style References.
2. Upload original videos/photos, optional Style Reference videos, and licensed music.
3. Watch meaningful processing progress: ingest, understanding, planning,
   matching, preview, and review.
4. Receive a coherent first cut with real video and audio.
5. Ask for a localized change in natural language.
6. Lock an accepted section and make one manual edit.
7. Ask the Agent to continue editing without damaging locked or manual work.
8. Compare versions and undo or restore.
9. Inspect which transferable pace and editing grammar the Agent learned from
   Style References without reusing their footage.
10. Export a final playable video from the committed revision.

This is the product narrative. Implementation milestones below are delivery
order, not permission to create disposable mini-products.

## 2. Demo completion criteria

The investor demo is complete only when it has:

- a polished Web workspace, not a CLI-only path;
- real upload, analysis, generation, preview, iterative editing, versioning,
  and export;
- autonomous editing with optional, non-content Style References;
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
invokes an OpenAI-compatible Gemini/Volcengine Director, and owns deterministic review plus
at most one bounded AutoFix. The application layer then creates a typed
first-cut Patch through ProjectService, stores one immutable candidate Agent
Revision, automatically renders it, runs deterministic plus optional
Volcengine post-render review, performs at most one Reviewer-driven replacement
AutoFix for repairable blocking issues, and atomically accepts only an approved
candidate.
The browser exposes progress, explanation, and the Reviewer summary. Failed
providers, exhausted repairs, render failures, and final review rejection preserve
the previous revision. The first cut now turns Moment ASR into a versioned
`caption.clean` track, selects an explicitly authorized music Asset, applies
gain/fades/speech ducking, places authorized SFX from Moment evidence, and may
align real cut/transition boundaries to measured audio events. Uploaded audio
meaning and signal facts are frozen into AgentRunContext before the model
decision; render review consumes the real audiovisual proxy. All layers render
in the revision-bound preview/export path. A production-model golden run for this layer,
animated typography, catalog-backed licensing, and effect-aware repair beyond
the first-cut subset remain.

Deliver:

- one production-configured media observer plus explicit test fake;
- `search_moments` and `inspect_media` tool contracts;
- Video Agent Harness, Director context, and tool loop;
- first-cut Patch generation and preview review;
- user-facing draft explanation based on actual operations.

Exit condition: a real footage set produces a coherent editable draft through
the browser, and a failed model call leaves the project intact.

### Stage 4 - Continuous editing

Status: the first safe vertical slice is implemented. A durable primary
Conversation records messages and Agent turns; each turn freezes ProjectDoc,
Moment evidence, recent conversation history, and workspace context. The
VideoAgentHarness runs a six-step closed Tool Registry containing
`read_project`, `search_moments`, `inspect_media`, and `propose_patch`.
Accepted low-risk proposals use the shared ProjectService commit path, enqueue
a Revision-bound preview, persist an operation-derived explanation, and update
the left-side browser conversation. Preview playback now freezes an absolute
Revision frame into the turn. The formal model schema and shared Patch path
support clip-boundary Trim, whole-clip Move/Delete, Moment-grounded Replace,
Crop/Center Reframe, Constant Speed, and source-audio Gain/Fade. One real
Gemini Golden Run has now completed all of those operations plus clarification
in one Conversation. Every accepted turn passed a no-write
ProjectService/renderer dry-run, committed a child Revision, kept source audio
paired, and produced a revision-bound FFmpeg preview; the ambiguous turn
produced only a question.
This qualifies the temporary Gemini chain and synthetic edit fixture, not the
final production model or investor-demo story footage. See
`CONTINUOUS_EDITING_V1.md` for the exact supported subset and remaining
boundaries.

Preview Annotation v1 now supplies the preview-first selection layer before a
full timeline workspace. A user can attach an exact frame/range and comment
from a ready current-Revision preview to the left conversation. The durable
WorkspaceSelection and AgentTurnContext keep that quote bound to its Revision
and Artifact, deterministic grounding maps it to timeline IDs, and every
resulting change still goes through typed Patch and ProjectService. Preview
edit also supports a normalized letterbox-safe rectangle. The Job extracts an
outlined frame from the exact preview Artifact and supplies it to the
image-capable Director; unsupported model input or extraction failure is
surfaced rather than silently degrading to text-only inference.

Caption and audio Patch operations are now admitted by the bounded tool loop:
the Director can add a complete caption or authorized audio track, edit cue
text/timing/style, and change gain/fades/speech ducking. The new path is
deterministically preflighted and media-tested, but still needs a real-model
multi-turn Golden Run before it is considered production-qualified.

Deliver:

- natural-language scope resolution;
- local Patch operations for trim, replace, speed, caption, music, and crop;
- selection-aware editing, locks, manual edits, undo, and version comparison;
- affected-range preview with transition/audio handles.

Exit condition: the full generate/edit/lock/manual-edit/agent-edit/undo/export
scenario passes as an end-to-end test.

### Stage 5 - Style Reference

Style Reference is part of Autonomous Edit. It uses the same upload and media
analysis pipeline, derives immutable content-addressed grammar Blueprints,
freezes them into AgentRunContext, and never exposes reference media as a
selectable source. See `STYLE_REFERENCE_V1.md`.

Exit condition: an uploaded Style Reference changes the Director's declared
and reviewed pacing/grammar, survives retry and recovery, and cannot place its
pixels, audio, transcript, or Moments in the output.

### Extension - Reference remake

Reference Remake is intentionally deferred until after the Preview-only
investor demo. Its UI entry must remain disabled until this whole vertical path
exists.

Deliver:

- immutable Reference Blueprint with evidence;
- Remake Policy and user-selected preserve/adapt/exclude dimensions;
- moment matching and multi-shot Alignment;
- adaptation decisions and reference-aware review;
- reference/output comparison in the Web workspace.

Exit condition: the same footage can be edited against a shot-level reference,
missing footage is explained, and localized reference feedback updates only
the mapped output range.

### Stage 6 - Remotion and presentation quality

Deliver:

- Remotion Player reading the shared ProjectDoc timing model;
- animated captions, title cards, brand components, and approved motion graphics;
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
