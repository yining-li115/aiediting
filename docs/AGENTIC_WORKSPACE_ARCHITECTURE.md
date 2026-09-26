# Agentic Workspace Architecture

Status: authoritative product and runtime model for the investor-demo build.

## 1. Product identity

AIVE is not a chatbot placed beside a conventional video editor. It is a
project-centered, artifact-centric Agent workspace in which a user and an
Agent continuously edit the same versioned video project.

The intended interaction is:

```text
prompt + uploaded media
  -> project creation and media understanding
  -> Agent-generated first revision
  -> left-side conversation + right-side preview/workspace
  -> conversational or manual edits
  -> revision-bound preview, history, restore, and export
```

The split-pane UI expresses this model, but it does not define it. The same
runtime rules apply to API clients, workers, and future alternative clients.

## 2. State ownership

| Object | Owns | Must not own |
|---|---|---|
| Project | Long-lived identity, settings, current revision pointer | A second timeline |
| Conversation | User intent, Agent explanation, confirmation, progress messages | Authoritative edit state |
| ProjectDoc Revision | Complete authoritative creative and timeline state | Jobs, caches, raw media bytes |
| Patch | One requested, scoped change from an exact base revision | A replacement project history |
| Agent Run | Reasoning/tool execution for one user turn | Direct database mutation |
| Job | Durable progress for analysis, render, or export | Project truth |
| Artifact | Preview/export produced from a named revision | Editable timeline state |
| Workspace Session | Selection, playhead, zoom, open panels | Persisted project content |

Chat may explain the work, but ProjectDoc answers what the work currently is.
A preview may show the work, but it is derived from a named revision. UI
selection helps scope a request, but it becomes durable only through a Patch.

## 3. Hybrid runtime: deterministic shell, agentic core

The outer product workflow is deterministic:

```text
create project
  -> upload and ingest
  -> analyze
  -> create first cut
  -> preview
  -> iterate
  -> export
```

Inside an editing turn, the Agent may dynamically decide which approved tools
to call:

```text
read current revision and selection context
  -> inspect or search media evidence when needed
  -> resolve the requested scope
  -> propose typed Patch operations
  -> request candidate validation or preview
  -> explain the result or ask for confirmation
```

The Agent controls creative decisions and tool choice. It does not control
authorization, locks, concurrency, idempotency, commit, or renderer capability
truth. Those remain deterministic service responsibilities.

Do not encode every possible user request as a fixed DAG. Do not let the Agent
replace the reliable upload/analyze/render/export job lifecycle with free-form
reasoning. Both layers are required.

### 3.1 Video Agent Harness

`VideoAgentHarness` is the runtime and control boundary around model-driven
editing. It owns frozen context construction, model/provider invocation,
workflow-local decision review, rendered-output review, and bounded recovery.
The autonomous first-cut `DirectorPipeline`, post-render Reviewer, and bounded
continuous-edit tool loop are workflows hosted by this Harness.

The Harness does not own repositories, ProjectDoc mutation, Patch commit,
render execution, or HTTP behavior. `DirectorApplication` persists Harness
inputs and outputs, submits the resulting typed Patch through ProjectService,
and enqueues the revision-bound Render Job. This separation lets later tool
permissions, hooks, compaction, observability, render review, and additional
workflows extend one runtime boundary without creating another project state.

## 4. One project, potentially many conversations

A Project is the durable collaboration unit. One or more conversations may
work on it over time, but every turn reads an explicit current revision and
submits a Patch against an explicit `base_revision_id`.

This permits future focused conversations, branches, or collaborators without
making chat history the source of truth. Concurrent work that starts from an
older revision receives a typed conflict instead of silently overwriting the
newer project.

For the investor demo, one primary conversation is sufficient. The data model
must still avoid assuming that exactly one chat owns a Project.

## 5. Initial creation flow

1. Create the Project and its minimal initial ProjectDoc revision atomically.
2. Store uploaded media as project-scoped Assets; add authorized AssetRefs to
   ProjectDoc through the normal edit path.
3. Run ingest and analysis Jobs without modifying the project revision.
4. Start an Agent Run against the current revision and available evidence.
5. The Agent emits a Patch for the first cut.
6. ProjectService builds and validates an immutable candidate revision.
7. Retain the autonomous first cut as an `awaiting_review` candidate.
8. Render a preview Artifact naming that candidate revision.
9. Inspect and optionally visually review the Artifact.
10. For repairable rejection, run one bounded AutoFix and create a new
    candidate from the same committed base.
11. Render and review the replacement, then atomically accept only when review
    passes.

Analysis results are evidence used to create edits. They do not become an
alternative timeline or silently rewrite a revision.

### 5.1 Media evidence hierarchy

Media understanding uses three distinct units:

1. A `Shot` is a physical camera/edit boundary found deterministically.
2. An `AnalysisWindow` is a bounded, overlapping sampling range used only to
   inspect a long Shot; it is not persisted as an edit boundary.
3. A `Moment` is searchable evidence whose source span covers one or more
   complete, consecutive Shots and retains their ordered `shot_ids`.

ASR windows are another provider-input constraint, not an edit boundary. Each
window is extracted temporarily from the original audio, its provider-relative
span is mapped back into Asset time, and only timed transcript evidence plus
provenance is persisted. The semantic grouper receives ordered visual and
speech evidence. Its output is accepted only when it covers every Shot exactly
once in source order; invalid or failed grouping falls back to one Moment per
Shot and marks the Asset analysis partial.

Never split a continuous Shot into final Moments merely because a model has a
short input limit. Model or embedding failures retain deterministic Shot,
quality, and keyframe evidence and mark the Asset analysis as partial.

## 6. Continuous editing flow

Both conversational and manual edits follow the same path:

```text
user or Agent intent
  -> typed Patch(base_revision_id, scope, operations)
  -> immutable candidate ProjectDoc
  -> lock/scope/capability/full-document validation
  -> atomic commit
  -> revision-bound preview
```

A manual drag may create a Patch without invoking a model. A conversational
request may require an Agent Run before it creates a Patch. After that point,
their safety and version behavior are identical.

## 7. Workspace context

The right-side workspace may send transient context with a prompt:

- selected track, clip, caption, graphic, or section IDs;
- playhead frame and visible output range;
- current revision ID;
- active comparison revision;
- current preview frame or inspection target.

This context helps resolve scope. Selection, playhead, panel layout, zoom, and
hover state do not belong in ProjectDoc. If a user says “replace this,” the
resolved stable target ID belongs in the resulting Patch.

## 8. Preview, review, and export

Every preview and export names the exact revision it consumes. A newer commit
does not relabel an older Artifact.

Preview annotations are revision-bound workspace evidence, not edit state. A
temporal annotation names the ready preview Artifact, its Revision, an exact
frame range, and the user's comment. It may be persisted with the Conversation
and frozen into an AgentTurnContext, but it never mutates ProjectDoc. The
application validates the reference and resolves its overlapping timeline IDs;
only a later typed Patch may create a new Revision.

Immediate edits may commit after validation and then render. Expensive or
high-impact Agent edits may render a validated candidate first and wait for
confirmation. A candidate is immutable but is not the Project's current
revision until atomic commit succeeds.

The autonomous first cut uses this candidate path automatically. Its
post-render Reviewer combines deterministic media inspection with an optional
multimodal provider. Rejection preserves the previous head. The offline
Reviewer records that no visual judgment occurred instead of pretending model
review happened.

Reviewer-driven AutoFix is bounded to one replacement attempt. It consumes a
frozen rejected-review context, creates a fresh typed Patch/candidate, and
re-enters the same render/review gate. It cannot mutate or commit the rejected
candidate. Non-repairable errors bypass the model and terminate safely.

If the Project head changes during review, acceptance conflicts and must be
rebased or regenerated. It never overwrites the newer work.

## 9. Recovery and history

Undo and restore do not mutate or delete historical revisions. They create a
new revision whose content is copied from an older revision and whose parent is
the current revision at restore time.

Failed Agent Runs, analysis Jobs, previews, renders, or commits never clear the
last committed revision. The workspace always has a known usable head.

## 10. Architectural anti-patterns

Do not introduce:

- Chat messages as the only reconstruction source;
- Agent-authored SQL or direct ProjectDoc writes;
- frontend-only timeline state that cannot round-trip through ProjectDoc;
- renderer-specific project formats;
- preview state that silently becomes current;
- in-place revision mutation;
- long-running analysis or render inside a database transaction;
- a separate manual-edit protocol beside Patch;
- an open-ended workflow engine for tasks already represented by Jobs and the
  Agent tool loop.

## 11. Implementation order

The shared runtime is built in this order (items 1-6 and the model-backed
autonomous first-cut vertical slice are now implemented):

1. ProjectDoc and Patch contracts;
2. immutable Revision and commit state machine;
3. ProjectService candidate/application/validation behavior;
4. revision repository and atomic persistence;
5. atomic browser project initialization, API, generated frontend contracts,
   and committed revision history;
6. project-scoped media upload, followed by revision-bound preview and export
   (**upload, durable proxy/thumbnail, timed transcript evidence, semantic
   multi-Shot Moment analysis, ProjectDoc FFmpeg preview/range preview/HQ
   export, HTTP delivery, and browser playback implemented**);
7. Agent Harness, tools, and continuous conversational editing (**explicit
   VideoAgentHarness runtime boundary, frozen AgentRunContext, typed Moment
   search/inspection, OpenAI-compatible Director, structured
   creative/copy/caption/music decision, deterministic review, one bounded
   AutoFix, first-cut Patch -> candidate Revision -> automatic preview ->
   post-render Reviewer -> bounded replacement AutoFix -> atomic acceptance
   implemented; durable Conversation/AgentTurnContext, closed Tool Registry,
   six-step tool loop, Moment search/inspection, initial local typed Patch,
   explanation, automatic preview, HTTP, and left-panel chat are also
   implemented. Revision-bound Preview playhead context and the formal
   provider-neutral typed path for clip-boundary Trim and whole-clip
   Move/Delete are connected without keyword intent rules. A real Gemini
   whole-clip Delete reached a Revision and preview enqueue in an isolated
   golden run; full workspace selection, compaction, Trim/Move and real-footage
   model qualification remain**).

Each later layer depends on the earlier state boundary and must not replace it.
