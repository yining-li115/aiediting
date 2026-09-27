# Autonomous Director v1

Status: authoritative constraints for the model-capable first-cut vertical slice.

## 1. Completed user path

For an autonomous project, the connected path is:

```text
upload -> proxy/thumbnail -> Shots/ASR/Moments
  -> durable Director Job
  -> VideoAgentHarness
       -> frozen AgentRunContext
       -> DirectorProvider professional planning stages
       -> deterministic review and at most one semantic AutoFix
  -> FirstCutDecision
  -> typed Patch v1
  -> ProjectService validation and immutable candidate Revision
  -> revision-bound preview Render Job
  -> deterministic media inspection + optional multimodal Reviewer
  -> at most one Reviewer-driven replacement candidate when repairable
  -> atomic candidate acceptance
  -> browser playback
```

Runtime Director resolution requires a real Gemini or Volcengine model card.
There is no offline Director adapter, keyword policy, or rule-authored first-cut
fallback. Missing credentials, invalid structured output, and exhausted
real-model availability fail the durable Job, preserve the current Revision,
and surface the recorded problem to the browser. Tests remain offline by
injecting explicit scripted fixtures that are not registered in runtime code.
Changing the real Director model does not change ProjectService, Patch,
Revision, or rendering behavior.

`VideoAgentHarness` is the runtime boundary around model-driven work, not a
second application or project state. It owns context construction, provider
invocation, decision review, bounded recovery, and rendered-output review.
`DirectorApplication` persists AgentRun state, submits the accepted decision
through the typed Patch path, and enqueues rendering. The Harness has no
repository, ProjectService, renderer, SQL, or HTTP access.

## 2. Frozen AgentRunContext

Before any model call, `AgentRunContextBuilder` snapshots:

- the exact base Revision and document hash;
- ProjectIntent, output settings, explicit requirements, and style tags;
- project-scoped ready source Asset runtime facts;
- analyzed Moment evidence in stable Asset/source order;
- content-hash-bound model understanding plus measured loudness, energy,
  beat/accent, and transient evidence for authorized music/SFX Assets; and
- stable IDs protected by ProjectDoc locks.

The context is stored on the Agent Run before a decision is requested. Recovery
therefore reuses the same evidence instead of silently observing a newer
analysis result. It is audit/recovery state, not a second timeline.

## 3. State ownership

- `ProjectDoc` in the committed Revision remains the only authoritative edit
  state.
- Moments are derived evidence. Selecting a Moment does not edit a project.
- `FirstCutDecision` records creative selection and explanation, but cannot
  render or replace ProjectDoc.
- The Director converts the decision into a closed Patch v1 request and calls
  `ProjectRevisionApplication.apply_patch`; it never writes ProjectDoc or SQL.
- `agent_runs`, Jobs, and RenderArtifacts are recovery/audit state. They point
  to revisions and cannot become a second timeline.

## 4. Professional planning and provider boundary

One Director remains responsible for the final proposal. A provider returns a
closed `FirstCutDecision` containing:

- selected existing Moment IDs;
- creative objective, hook, pacing, and story arc;
- draft title/copy and caption strategy; and
- music mode, mood, energy curve, and speech-ducking intent.

These are professional stages inside one composite Director call, not peer
Agents with write access. The provider cannot return a ProjectDoc, Patch,
renderer command, SQL, executable code, or an unregistered media identity.

The Director now materializes a speech/highlight caption strategy into one
`caption.clean@1.0.0` CaptionTrack using ASR text attached to the selected
Moments. Cue timing is mapped to the exact selected output clips. A music-bed
strategy must name one exact authorized `music` Asset from the frozen context
and explicitly provide every source/output range, gain, fade, and
speech-ducking parameter. SFX decisions likewise name an authorized `sfx`
Asset, exact timing, and the source Moment evidence motivating the effect.
The Patch builder translates those decisions into `music` and `sfx` tracks; it
does not select, tile, align, or score audio by rule. Deliberate cut/transition
sync cites a measured beat, accent, or transient. Deterministic review rejects
invented signal events and incorrect source-to-output mappings, while the
Director decides whether any synchronization is creatively appropriate.
Unknown or unlicensed Asset identities still fail deterministic review.
`ProjectIntent.caption_policy` and `music_policy` are also deterministic
review inputs: `none` forbids the corresponding layer, while `auto` requires it
when selected speech or an authorized music Asset makes it executable.

The active Director is the pinned `doubao-seed-2-1-pro-260915` Ark model,
selected by the `doubao-seed-2.1-pro` ModelCard and authenticated with
`AIVE_ARK_BASE_URL` plus `AIVE_ARK_API_KEY`. Model identity no longer comes
from one shared `AIVE_ARK_MODEL_ID`; Pro, Lite, Embedding, and Seed-ASR have
separate auditable cards and adapters.

For internal investor-demo qualification,
`AIVE_ROLE_DIRECTOR_MODEL=gemini-3.6-flash` may be paired with the pinned,
comma-separated `AIVE_ROLE_DIRECTOR_FALLBACK_MODELS`. The chain switches only
after quota, timeout, or temporary capacity failures. It does not use a second
model to hide malformed decisions or deterministic validation failures. Free
Gemini traffic is marked data-sharing and is restricted to owned/synthetic
demo footage.

## 5. Decision review and bounded AutoFix

Model output is reviewed before Patch creation. The deterministic Reviewer
rejects unknown, cross-project, unauthorized, unusable, or insufficient Moment
selection and impossible music strategies. Warnings do not block a proposal;
errors do.

One semantic AutoFix attempt may ask the same provider for a complete corrected
decision using the frozen context and typed review issues. The limit is code,
not a prompt suggestion. If the corrected decision still fails:

- the Agent Run stores the rejected decision, review, and repair count;
- the Job becomes failed;
- no Patch is submitted; and
- the current Revision and last usable preview remain unchanged.

OpenAI-compatible structured-output parsing may separately repair one malformed
JSON response. That formatting repair does not increase the semantic AutoFix
limit.

## 6. Post-render Reviewer gate

The first-cut Patch is stored as an immutable `awaiting_review` candidate. It
does not advance `projects.current_revision_id` yet. The workflow renders that
exact candidate and starts a separate durable `render_review` Job.

The post-render review has two layers:

- deterministic inspection verifies decode, duration, output dimensions,
  representative non-black/non-flat frames, expected audio, and a sharpness
  warning; and
- the configured `reviewer` provider may inspect the proxy or extracted frames
  and return typed creative issues. A video-capable Reviewer that declares
  audio-reading support also reviews music fit, dialogue intelligibility,
  source-audio continuity, fades, ducking, picture-music synchronization, and
  SFX appropriateness. Frame-only review records that audio was not reviewed.

An error issue rejects the candidate. An accepted report permits
`ProjectRevisionApplication.accept_candidate` to perform the normal atomic
head compare-and-swap. A rejected render, failed render, failed Reviewer, or
stale-head conflict never makes the candidate current.

The active creative Reviewer is the same pinned Pro model, behind the Reviewer
provider boundary. Deterministic inspection still runs first, but there is no
offline substitute for creative visual judgment. Missing credentials or an
invalid model response fail visibly and preserve the prior committed Revision.
Default tests inject fixtures or mock HTTP transports and never make paid calls.

When every blocking issue is marked `repairable`, the Harness may start one
durable `render_autofix` Job. The Director receives the original frozen
AgentRunContext, rejected decision, exact candidate/artifact identities, and
typed RenderReviewReport. It returns a complete replacement FirstCutDecision,
which must pass the same deterministic decision Reviewer before application
code can build another Patch.

The replacement Patch is generated against the original committed base, with
fresh stable Patch, clip, operation, candidate Revision, render, and review
identities. The rejected candidate is never mutated or temporarily committed.
The one-attempt limit is enforced in code. A second rejection, any
non-repairable blocking issue, provider failure, or stale Project head is
terminal for that Agent Run and preserves the previous committed Revision.

## 7. Durable and idempotent behavior

- A first-cut request names an exact `base_revision_id` and project-scoped
  idempotency key.
- Enqueue creates the Agent Run and Director Job atomically.
- The selected Moment decision is persisted before Patch application.
- Stable Patch and Render idempotency keys are derived from the Agent Run, so
  recovery cannot create duplicate revisions or previews.
- ProjectService stores the candidate first and performs the final stale-head
  compare-and-swap only after its render review is accepted. If a user edits
  first, the Director cannot overwrite that newer revision.
- A provider failure records a failed Job/Agent Run and leaves the previous
  current Revision intact.
- Render and review are separate durable Jobs. Startup recovery resumes the
  candidate render/review/accept sequence, including the narrow case where a
  report was saved just before process shutdown.
- Render AutoFix is also a separate durable Job. Its input context and
  replacement decision are persisted before Patch creation so process recovery
  cannot silently use a different review or model answer.
- A render or review failure leaves the previous committed Revision and preview
  current.

## 8. Director failure policy

The Director may retry the same real provider within its bounded transport
policy and may switch to another explicitly configured real model only for
quota, timeout, or temporary server-capacity failures. Invalid structured
output, an unsafe or unsupported Patch, failed scope/lock checks, and rejected
review do not trigger another model or a local edit policy.

On terminal failure:

- the Agent Run and Job store an actionable error summary;
- the browser displays that error instead of a fabricated result;
- no Patch is submitted and no candidate becomes current; and
- the last committed Revision and its preview remain available.

Full exception traces stay in server logs. The Job API exposes the concise
error message needed by the user rather than embedding a traceback in the UI.

## 9. Provider and Patch constraints

A model provider may choose and order Moment IDs and explain its
decision. It may not return SQL, FFmpeg commands, arbitrary JSON Patch paths,
or a replacement timeline document. Every returned decision is validated,
resolved against exact project-scoped Moments, converted to Patch v1, and
passed through the existing locks/scope/capability/revision checks.

The first-cut path currently requires:

- autonomous mode;
- an empty base timeline;
- ready source Assets with `ready` or `partial` analysis;
- at least one analyzed, usable Moment;
- the currently executable FFmpeg subset documented in `RENDERING_V1.md`.

The typed `search_moments` and `inspect_media` tools are implemented over frozen
context. They now also run inside the durable bounded continuous-edit tool loop
documented in `CONTINUOUS_EDITING_V1.md`. Reference remake remains later work.

## 10. HTTP and browser behavior

- `POST /projects/{project_id}/first-cuts` queues an explicit first cut.
- `GET /projects/{project_id}/first-cuts/latest` returns its durable status.
- Upload processing automatically advances through analysis, Director,
  candidate rendering, review, and acceptance.
- The browser follows the project head while automatic generation is active,
  shows Director/render/review progress, displays the explanation and Reviewer
  summary, and plays the revision-bound preview only after acceptance.

## 11. Remaining boundary

The first-cut model/context/review path is implemented, but production model
quality is not claimed until a real golden-footage run succeeds. Render review
requires a configured multimodal model and fails visibly when it is unavailable;
there is no offline creative-review substitute.
Animated/word-timed captions, a searchable licensed music/SFX catalog with
stored license evidence, more capable effect-aware repair,
full workspace selection, and the wider continuous-edit operation set belong
to following slices. The formal continuous-edit schema already includes
clip-boundary Trim and whole-clip Move/Delete. A real Gemini model has completed
an isolated multi-turn Trim, Move, Delete, Replace, Crop, Speed, Gain and Fade
run through ProjectService, immutable Revisions, and revision-bound previews.
Investor-demo footage and final production-model selection remain unqualified.
