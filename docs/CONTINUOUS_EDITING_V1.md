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
It currently permits the existing source, crop, gain, and fade operations plus
the exact structural operations required for clip-boundary Trim, whole-clip
Move, and whole-clip Delete:

- replace a visual source;
- set visual crop;
- replace an audio source;
- set audio gain;
- set audio fades;
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
- `propose_patch` runs a no-write ProjectService and renderer dry-run. Scope,
  lock, stale-ID, gap, overlap, media, and renderer errors become persisted
  tool results that the same bounded turn may correct. A rejected proposal is
  never committed.

The offline provider currently executes source-audio gain/mute requests and
uses Moment search/inspection before honestly returning no change for other
instructions it cannot safely convert. The shared OpenAI-compatible Director
adapter supports both Gemini and Volcengine model cards; either can choose any
registered tool and return any operation in the union. All IDs, scope, locks,
media authorization, capability checks, and stale-head rules are still
enforced by ProjectService.

The offline natural-language policy is temporary. Its removal condition is a
successful live production-Director Continuous Editing golden run plus
configuration that fails closed when the Director role is not configured.
After that checkpoint, runtime editing keeps only the formal model/tool path;
deterministic tests use explicit scripted provider fixtures rather than a
keyword implementation presented as product behavior.

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
captions, music placement, transitions, graphics, lock editing, and manual
workspace edits must not be claimed as complete. They are added by extending
the closed operation union and tests, not with arbitrary JSON Patch or a second
timeline schema.

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
committed, includes the resulting Revision identity, and never claims that a
proposal was applied before ProjectService succeeds.

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

The browser sends the current Revision and the frame currently displayed by a
revision-bound Preview Player. Full previews map media time from frame zero;
range previews add their absolute `output_range.start_frame`. A playhead from a
historical Revision or a source proxy is never attached to a turn against the
current Revision. The frozen playhead makes phrases such as “here” and “from
here” resolvable without making player state authoritative.

`WorkspaceSelection` also defines stable selected target IDs, visible range,
comparison Revision, and preview inspection frame. Those additional fields
become active only when the right-side editor exposes real selection state.

## 8. Next slices

The remaining Continuous Editing work is:

1. connect real right-side clip/track selection and visible-range state;
2. remove the runtime offline keyword policy once production Director
   configuration fails closed; keep explicit scripted providers only in tests;
3. add replace, crop/reframe, speed, captions, music, transition, graphic, and lock operations
   one bounded contract slice at a time;
4. add affected-range preview and a candidate/review gate for high-impact
   edits;
5. add explicit confirmation and clarification turn state where required;
6. replace simple 20-message truncation with measured compaction and summaries;
7. add model/tool latency, token, retry, and failure observability; and
8. qualify the final production Director model with the golden footage project.

The live synthetic Golden Run is reproducible with
`./.venv/bin/python scripts/check_continuous_edit_golden.py`. On 2026-09-26 it
completed Trim -> Move -> Delete -> clarification in one durable Conversation,
preserved a four-Revision chain, and generated 6s/5s/5s/3s FFmpeg previews,
each containing audio and video streams. This qualifies the temporary Gemini
chain and formal editing path; it does not freeze the final production model or
qualify investor-demo story footage.
