# Continuous Editing v1

Status: authoritative contract and runtime boundary for the first iterative
conversation-to-edit vertical slice.

## 1. Connected user path

```text
left-side user message + current workspace context
  -> durable Conversation message and continuous_edit Job
  -> frozen AgentTurnContext
  -> VideoAgentHarness bounded tool loop
       -> read_project
       -> search_moments
       -> inspect_media
       -> propose_patch
  -> closed typed Patch proposal
  -> ProjectService validation and atomic Revision commit
  -> revision-bound preview Render Job
  -> durable Agent explanation linked to the resulting Revision
  -> browser refreshes conversation, history, and preview
```

This is the first executable slice of the Continuous Editing Agent. It is not
a second chat-only editing system. The conversation records intent and
explanation; the committed ProjectDoc Revision remains the only authoritative
edit state.

## 2. Conversation and turn state

The investor-demo product uses one primary Conversation per Project. A user
message creates one durable AgentTurn and Job atomically. The turn stores:

- the exact `base_revision_id` and transient workspace selection;
- provider and provider version;
- a frozen `AgentTurnContext` before the first model call;
- each typed tool call and result in execution order;
- the final Patch and result Revision when an edit is applied; and
- the assistant explanation plus optional preview Artifact/Job identities.

Idempotency is project scoped. Reusing a key for the same request returns the
existing turn; reusing it for different content conflicts. A turn may run only
against the Project head it named. A newer head causes a typed conflict rather
than a silent overwrite.

The context contains the exact ProjectDoc snapshot, its hash, analyzed Moments,
up to the latest 20 messages, and the right-side workspace state. Truncation is
recorded explicitly. Conversation history and selection help resolve intent,
but neither can mutate the Project.

## 3. Closed Tool Registry

The v1 registry permits exactly four tools:

| Tool | Purpose | Mutates state |
|---|---|---|
| `read_project` | Return the exact frozen ProjectDoc so the model can use stable IDs | No |
| `search_moments` | Rank/filter the frozen project-scoped Moment evidence | No |
| `inspect_media` | Return full evidence for named Moments from that frozen set | No |
| `propose_patch` | Submit one closed typed Patch proposal for deterministic validation | No |

`search_moments` is therefore part of the Continuous Editing Agent, not a
separate first-cut-only subsystem. The model decides whether it needs search
and inspection based on the user request. The registry enforces the permission
list and project scope; a prompt cannot grant itself another tool.

`propose_patch` still does not commit. Only the application service may turn
its validated result into a Patch and submit it through ProjectService.

## 4. Bounded tool loop

One provider decision returns exactly one `tool_call` or a typed `finish`
response. The Harness executes the approved call, persists its result through
the application callback, then returns the trace to the provider for the next
decision. The current hard limit is six calls.

The loop rejects duplicate call IDs, non-contiguous recovered traces,
unregistered tools, malformed results, and proposals outside the permitted
operation union. A persisted trace can resume after process interruption
without silently rebuilding context from newer Project or analysis state.
Exhausting the limit fails the turn; it does not apply a partial edit.

## 5. Continuous edit operation subset

The continuous proposal union is deliberately smaller than all of Patch v1.
It currently permits the existing source, crop, center-reframe, gain, fade,
caption, and authorized-audio operations plus the exact structural operations
required for clip-boundary Trim, whole-clip Move, and whole-clip Delete:

- replace a visual source;
- set visual crop;
- set center reframe;
- replace an audio source;
- set audio gain;
- set audio fades;
- set or clear supported speech ducking;
- add a complete music, voiceover, or SFX track from a ready Asset authorized
  for that role, or insert a clip into an existing matching audio track;
- add a complete `caption.clean@1.0.0` track;
- replace the project output settings for an explicit canvas orientation or
  aspect-ratio request;
- add, edit, retime, delete, or restyle cues on an existing caption track;
- delete an existing visual or audio clip;
- set visual or audio output timing;
- set constant visual or audio playback;
- set timeline duration; and
- replace or clear the CreativePlan when a structural edit invalidates it.

Natural-language phrases are not enumerated in deterministic code. The
configured Director model resolves the instruction against the frozen
ProjectDoc, playhead, selection, conversation, and Moment evidence. It may
then use only the closed operations above.

Structural edits obey these additional invariants:

- Trim changes exact source and output boundaries while preserving the
  ProjectDoc playback equation. A clip is never silently split.
- Move explicitly updates every displaced visual clip and matching source
  audio clip; the primary visual result must remain gapless.
- Delete removes the whole named clip and matching source audio, explicitly
  shifts later elements, and updates timeline duration.
- Replace must use a Moment returned by `search_moments` and then inspected by
  `inspect_media`. Its asset and selected source subrange are checked against
  that evidence. A video replacement also replaces matching source audio; an
  image replacement removes the old source audio.
- Crop uses normalized source coordinates and does not change timing. Only
  center Reframe is admitted because face, saliency, and manual Reframe are not
  yet supported by the FFmpeg renderer.
- Output orientation is a global, non-ripple edit. It must use
  `set_output_settings`; Crop or Reframe alone cannot claim to change a
  landscape canvas into portrait. Existing fps, audio sample rate, and color
  space are preserved unless the user explicitly asks to change them. The same
  proposal may include Crop or center Reframe operations to adapt shots to the
  new canvas.
- Constant Speed preserves the source range, derives a new output duration
  from the exact playback equation, applies the same rate/output mapping to
  matching source audio, and ripples the timeline when duration changes.
- Gain and Fade remain bounded audio edits. Gain is expressed in dB; fade
  lengths are integer project frames and cannot exceed the clip duration.
- New audio clips must use a ready runtime Asset whose ProjectDoc role matches
  the destination track, and their source range must fit its probed duration.
  Only music clips may use the implemented `speech` ducking group.
- New captions use `caption.clean@1.0.0` or an exact published Caption component
  returned by `search_components` in the same tool trace. New Graphics obey the
  same exact-version search evidence rule. Component props and integer output
  frames fail the same ProjectService/renderer preflight as every other
  capability.
- A newly introduced Registry component uses a tightly constrained global
  `set_dependencies` plus its layer operation: existing renderer/Skill/model
  and component dependencies are immutable, and the new entry must pin the
  searched bundle digest. The Agent cannot use this operation for unrelated
  dependency mutation.
- `create_component_candidate` may produce sandboxed source/bundle/PNG evidence
  when search finds no fit, but it cannot publish or use that candidate. A user
  or Reviewer must explicitly publish it before a later Agent turn can search
  and reference it.
- Tool Registry rejects a structural visual edit that omits or disagrees with
  an existing matching source-audio operation; audio/video sync is not left to
  prompt compliance.
- A duration-changing structural edit uses `scope.kind=global`,
  `allow_ripple=true`, includes `set_timeline_duration`, and may contain only
  the approved structural operations. Other continuous edits remain bounded.
- When the requested target, trim edge, destination, or ripple intent is not
  unambiguous, the Director finishes with a clarification question and no
  Patch.
- When no playhead, visible range, or selected target grounds the request, a
  model-based semantic gate first decides whether the latest message itself
  contains a concrete edit action and resolvable target reference. It does not
  use keyword routing, old conversation targets, or the project brief to fill
  missing current intent.
- The provider receives a deterministic `workspace_grounding` summary that
  maps the absolute playhead frame to the containing half-open visual interval
  and matching source-audio IDs. The model still interprets the instruction;
  it does not calculate timeline identity from tuple order.
- `propose_patch` runs a no-write ProjectService and renderer dry-run. Scope,
  lock, stale-ID, gap, overlap, media, and renderer errors become persisted
  tool results that the same bounded turn may correct. A rejected proposal is
  never committed.

Runtime continuous editing has no offline Director adapter or keyword/rule
fallback. The shared OpenAI-compatible Director adapter supports both Gemini
and Volcengine model cards; either can choose any registered tool and return
any operation in the union. Missing credentials, invalid structured output,
exhausted real-model availability, and rejected tool decisions fail the Agent
Turn and surface its error to the browser while the current Revision remains
unchanged. Deterministic tests inject explicit scripted fixtures that runtime
code cannot resolve from the provider registry.

All IDs, scope, locks, media authorization, capability checks, and stale-head
rules are still enforced by ProjectService. Those checks reject unsafe model
output; they never replace it with a locally authored edit.

For internal demo footage, the Director role may use an ordered list of pinned
Gemini model cards. `AIVE_ROLE_DIRECTOR_MODEL` names the primary model and
`AIVE_ROLE_DIRECTOR_FALLBACK_MODELS` contains comma-separated fallbacks.
Failover is allowed only for quota, timeout, or temporary provider-capacity
errors. It never sends a malformed decision, invalid Patch, failed scope/lock
check, or renderer rejection to another model. Quota/429 failures cool down
for five minutes; transient 5xx/timeout failures use a short 15-second
cooldown, and each chain member has at most two attempts. The AgentTurn records
the configured chain; per-call accounting and call logs record the actual
model, prompt version, and turn ID. Rolling `*-latest` aliases are forbidden.

The free Gemini tier may use submitted content to improve Google products.
This chain is therefore restricted to owned/synthetic demo media and is not a
production privacy policy for customer footage.

Mid-clip removal that requires a new split identity, variable retiming,
animated or word-timed captions, catalog search/replacement, transitions,
graphics, lock editing, and manual workspace edits must not be claimed as
complete. They are added by extending the closed operation union and tests,
not with arbitrary JSON Patch or a second timeline schema.

## 6. Commit, rendering, and explanation

The current continuous path uses immediate commit. A proposal first creates a
pure, unpersisted candidate that passes the same lock, scope,
complete-document, revision, and renderer rules. A gap, overlap, unsupported
layer, unavailable source, or invalid scope is returned to the model as a
bounded correction result while the old head remains unchanged. Only an
accepted dry-run reaches the atomic commit path; validation is repeated there,
then the application enqueues a preview bound to the new Revision. The later
Candidate/confirmation gate is still required before broader high-impact
editing is enabled. If creation of the durable preview Job fails after the
commit for infrastructure reasons, the turn is still completed truthfully and
tells the user that the saved Revision needs a render retry. It must not report
the edit itself as failed after ProjectService has already advanced the head.

The assistant message is generated from the operations that were actually
committed and never claims that a proposal was applied before ProjectService
succeeds. It is user-facing product copy: it summarizes the visible result and
must not expose Revision IDs, service names, schema names, provider traces, or
other implementation details. The resulting Revision identity remains linked
structurally on the Message and AgentTurn for history, audit, and recovery.

The browser presents each turn in causal order:

```text
user request -> collapsible Agent activity -> final assistant summary
```

The final summary is not shown while its render Job is still active. A
completed turn must not retain a running/Thinking indicator; a render failure
is shown in the activity before the saved-edit summary.

Agent activity is an audit-friendly projection of events that actually
happened, not a canned list of expected stages. The continuous-edit worker
emits model-decision start, tool start/result, preview-evidence preparation,
Patch application, commit, and render progress in causal order. Tool events
carry safe result facts such as the actual search query, result count,
proposal summary, and operation types. While collapsed, the browser displays
the latest real action in one muted line; expanding it reveals the ordered
event history. A completed turn replaces the live action with elapsed time and
the preview result. The UI must never display a future action, an invented
percentage, or a generic fixed tool sequence.

## 7. HTTP and browser behavior

- `POST /projects/{project_id}/conversations` creates or replays the primary
  Project conversation.
- `GET /projects/{project_id}/conversations/{conversation_id}` returns its
  durable messages.
- `POST .../turns` records a message and queues the continuous-edit Job.
- `GET .../turns/{turn_id}` returns status, frozen context, tool trace, Patch,
  Revision, and preview identities.
- The left workspace panel waits for the Agent Job and its preview Job, then
  refreshes the current Revision, history, conversation, and right preview.
  While those Jobs run, the browser keeps the previous ready preview visible
  and pauses background project polling. After the new Artifact is ready, the
  new Revision and its Artifact are loaded before one atomic UI switch; the
  player must not flash an unrendered Revision or substitute a raw source
  Asset between versions.

The browser sends the current Revision and the frame currently displayed by a
revision-bound Preview Player. Full previews map media time from frame zero;
range previews add their absolute `output_range.start_frame`. A playhead from a
historical Revision or a source proxy is never attached to a turn against the
current Revision. The frozen playhead makes phrases such as “here” and “from
here” resolvable without making player state authoritative.

Preview Annotation adds a `Preview | Preview edit` mode without creating a
second timeline. A user pauses or scrubs a ready current-Revision preview,
marks one frame or a half-open frame range, optionally drags a rectangle over
the rendered picture, writes a comment, and attaches that reference to the
left-side composer. Each `PreviewAnnotation` carries a stable annotation ID,
exact Revision ID, ready preview Artifact ID, output frame range, optional
normalized rectangle, and comment. Rectangle coordinates are measured against
the actual video content, excluding player letterbox/pillarbox. The application
rejects missing, non-preview, non-ready, cross-Revision, out-of-artifact, and
out-of-picture references before an AgentTurn is queued. Accepted annotations
are stored inside the durable `WorkspaceSelection`, frozen into
`AgentTurnContext`, and displayed with the originating user message.

Before model inference, deterministic grounding resolves every annotated range
to overlapping visual clips, source audio, captions, and graphics. The Director
may interpret the user's instruction and annotation comment semantically. For
a spatial annotation, the Job also extracts the representative midpoint frame
from the exact immutable preview Artifact and draws the normalized rectangle on
that frame. The annotated image is supplied to every bounded Director step with
its annotation ID and absolute Revision frame. Spatial turns fail visibly when
the configured Director cannot accept images or evidence extraction fails; the
runtime never silently drops the pixels and falls back to keyword rules.

Neither temporal nor spatial evidence can turn itself into state: any resulting
edit still requires a closed typed Patch, ProjectService validation, a new
immutable Revision, and a Revision-bound preview. Annotation comments and image
pixels are untrusted user evidence, never system instructions. Freehand strokes,
polygons, masks, and tracked regions remain outside this contract.

`WorkspaceSelection` also defines stable selected target IDs, visible range,
comparison Revision, and preview inspection frame. Those additional fields
become active only when the right-side editor exposes real selection state.

## 8. Next slices

The remaining Continuous Editing work is:

1. connect real right-side clip/track selection and visible-range state;
2. keep model errors actionable in the browser as providers and prompts evolve;
3. run a real-model Golden Run for the implemented caption/music operation
   subset, then add transition, graphic, and lock operations
   one bounded contract slice at a time;
4. add affected-range preview and a candidate/review gate for high-impact
   edits;
5. add explicit confirmation and clarification turn state where required;
6. replace simple 20-message truncation with measured compaction and summaries;
7. add model/tool latency, token, retry, and failure observability; and
8. qualify the final production Director model with the golden footage project.

The live synthetic Golden Run is reproducible with
`./.venv/bin/python scripts/check_continuous_edit_golden.py`. On 2026-09-26 it
completed Trim -> Move -> Delete -> Replace -> Crop/Center Reframe -> Constant
Speed -> Gain/Fade -> clarification in one durable Conversation. It preserved
eight immutable Revisions and generated 6s/5s/5s/3s/3s/3s/2.5s/2.5s FFmpeg
previews, each containing audio and video streams. Replace used searched and
inspected Moment evidence, and Speed preserved source-audio playback mapping.
This qualifies the temporary Gemini chain and formal editing path; it does not
freeze the final production model or qualify investor-demo story footage.
