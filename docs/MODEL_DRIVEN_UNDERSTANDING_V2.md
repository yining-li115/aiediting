# Model-driven Understanding v2

Status: authoritative migration contract for source understanding, Style
Reference understanding, first-cut authoring, retrieval, and creative review.

## 1. Principle

AIVE defines the grammar of a model answer and validates its evidence. AIVE
does not encode content understanding or editorial taste as application
rules.

```text
media + objective evidence
  -> specialist model provider
  -> versioned structured understanding
  -> schema/evidence validation
  -> immutable derived record
  -> Director / Reviewer
  -> typed Patch
  -> ProjectService
```

ProjectDoc remains the only authoritative edit state. Understanding records,
Blueprints, embeddings, search results, edit decisions, and reviews are
derived evidence or audit state.

## 2. Deterministic ownership

Deterministic code owns:

- media decode, duration, dimensions, frame rate, audio presence, and exact
  source/output time conversion;
- durable upload, proxy, thumbnail, sample extraction, and evidence identity;
- project scope, authorization, locks, idempotency, Revision concurrency, and
  renderer capability;
- validation that model citations name supplied evidence and legal ranges;
- translation of an explicit model edit decision into the closed Patch
  protocol without adding creative choices; and
- render execution plus technical integrity checks such as decode, duration,
  dimensions, and required-stream presence.

Measurements such as candidate cuts, optical flow, luminance, onsets, beats,
transients, energy envelopes, loudness, or sharpness may be supplied to a
model as optional evidence. They are not a
quality score, style label, usability decision, or creative-review result.

## 3. Model ownership

Provider-backed models own:

- source content, people, actions, events, speech meaning, sound events,
  emotion, narrative function, visual/camera language, editorial value, and
  recommended uses;
- semantic multi-Shot Moment boundaries;
- Style Reference temporal, narrative, visual, motion, transition, color,
  typography, graphic, and audio-visual grammar;
- exact first-cut source selections, trims, output durations, sections,
  transitions, reframing, speed, audio, captions, music, graphics, and the
  rationale tying those choices to user intent and available evidence;
- semantic retrieval/reranking; and
- creative review of plans and rendered video.

## 4. Shared answer grammar

Every understanding and creative-review contract includes:

- a human-readable conclusion;
- exact evidence references and time ranges where applicable;
- confidence;
- explicit uncertainty or unresolved questions;
- provider, model, and prompt/configuration provenance; and
- a version that changes whenever the meaning of the contract changes.

Free descriptions are primary. Normalized tags are secondary retrieval aids.
Executable enums remain closed only where ProjectService or a renderer must
select a supported operation or registered capability.

## 5. Required contracts

### MediaEvidence

Objective and reproducible facts: Asset identity, exact source ranges,
physical/candidate Shot identities, sample identities, transcript evidence,
and optional raw signal measurements. It contains no `is_usable`, emotion,
style, pace, aesthetic score, or narrative conclusion.

### SourceUnderstanding

One model-authored interpretation of a source Moment: summary, subjects,
actions/events, narrative function, visual and camera language, emotions,
audio meaning, editorial value, recommended/avoid uses, worth-using judgment,
evidence citations, confidence, uncertainty, and provenance.

Raw sound is not inferred from `has_audio` or transcript keywords. An
independent `AudioUnderstandingProvider` receives the exact WAV evidence span
and returns speech meaning, sound events, music, rhythm/energy, emotion,
editorial value, uncertainty, and its own provenance. Source Understanding
preserves that answer rather than rewriting it with application rules.

For authorized uploaded `music` and `sfx` Assets, `AudioAssetAnalysis` keeps
two deliberately separate layers. `AudioSignalAnalysis` stores deterministic
level, energy, beat/accent, and transient timing facts. The provider stores
free-language meaning, mood, sound events, rhythm/energy, and editorial value.
Both are content-hash and provider-version bound, persisted as derived
evidence, and frozen into `AgentRunContext`; neither becomes project edit
state. Code never converts BPM or loudness into a creative decision.

### StyleBlueprint

One immutable model-authored transferable grammar for a Style Reference:
summary, temporal structure, narrative grammar, shot grammar, transition and
motion language, color/composition, audio-visual relationship, caption and
graphic language, transferable principles, prohibited copying, evidence,
confidence, uncertainty, and provenance. Objective measurements may be
attached as evidence but never converted into a style conclusion by code.

### EditBlueprint

One Director answer containing sections and explicit clip-level decisions.
Each visual choice names an authorized Moment, exact source range, target
output duration, narrative purpose, and optional supported crop/reframe,
speed, transition, audio, caption, music, or graphic intent. The Patch builder
translates these decisions; it does not choose defaults on the Director's
behalf.

Music and SFX choices follow the same rule. The Director selects an authorized
audio Asset, exact source/output range, gain/fades/ducking, motivating Moment
evidence, and any deliberate audio-visual synchronization. A sync decision
must cite an actually measured beat, accent, or transient and a real visual
cut, transition, or section boundary. Deterministic review verifies that
mapping; it never decides that every beat should receive a cut or which action
deserves an effect.

### CreativeReview

A model-authored assessment of an EditBlueprint or rendered Artifact against
the user intent, frozen source understanding, and Style Blueprints. Issues cite
evidence and identify whether they are repairable. Deterministic validators
remain separate and report only authorization, identity, timing, structural,
and capability violations.

## 6. Failure policy

There is no semantic or creative offline fallback. Provider configuration,
transport, schema, citation, or evidence failure becomes durable failed state
and is shown to the user. Raw evidence remains retryable and the last committed
Revision remains usable.

Test suites inject explicit providers. Test fixtures are not registered as a
production fallback and never prove real-model quality.

## 7. Migration and removal

The migration order is:

1. introduce the v2 contracts and provider ports;
2. migrate media understanding and immutable Style Blueprints;
3. migrate Director decisions and the Patch translator;
4. migrate retrieval and creative review;
5. qualify the path with real-model Golden Runs; and
6. remove `aive.pipeline`, legacy planner/matcher/critic/reference modules,
   legacy TimelineSpec HTTP validation, and their exported Web contracts.

Committed database migrations remain append-only. Historical v1 derived data
may remain stored, but it is never accepted as v2 understanding without a new
provider run.

The runtime migration is complete: legacy planner/matcher/critic/reference,
TimelineSpec renderer, CLI, validation HTTP API, exported Web contracts, and
their rule fallback providers have been removed. Historical database tables
remain only because committed migrations are append-only.
