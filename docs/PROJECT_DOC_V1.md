# ProjectDoc v1 Contract

Status: implemented domain contract with a separate Patch v1 request contract,
immutable persistence, HTTP/Web initialization, project-scoped media upload,
durable proxy/thumbnail processing, generated Web types, Patch execution,
history, restore, revision-bound rendering, and autonomous first-cut creation.

Source modules:

- `packages/aive/contracts/project_doc.py`
- `packages/aive/contracts/project.py`
- `packages/aive/contracts/timeline.py`
- `packages/aive/contracts/time.py`
- `packages/aive/contracts/reference.py`

Contract tests: `tests/contract/test_project_doc.py`.

Checked-in example: `examples/project_doc_v1.json`.

Revision lifecycle: `docs/REVISION_COMMIT_STATE_MACHINE.md`.

## 1. Purpose

`ProjectDoc` is the complete authoritative snapshot of one project revision.
It is the object that the Web editor displays, the Agent proposes changes to,
and renderers consume. It is not a model response, cache, job payload, or
database row assembled from several competing timeline representations.

Each saved revision contains one full ProjectDoc. The database project row
points to the current committed revision. Initialization and Patch execution
through ProjectService are the only write paths.

## 2. Top-level shape

```text
ProjectDoc
  schema_version
  project_id
  revision_id
  parent_revision_id
  intent
  assets[]
  creative_plan?
  timeline
    duration_frames
    output
    tracks[]
  locks[]
  reference_context?
  dependencies
```

The contract uses closed Pydantic models: unknown fields fail validation. The
models reject field assignment, and ProjectService must treat nested JSON
parameters as immutable, so application code creates a new revision instead
of mutating an existing snapshot in place.

## 3. Identity and revision fields

| Field | Meaning |
|---|---|
| `schema_version` | ProjectDoc format version; exactly `1.0` for this contract |
| `project_id` | Stable project identity shared by all revisions |
| `revision_id` | Identity of this immutable snapshot |
| `parent_revision_id` | Revision used to create this snapshot, or null for the initial revision |

IDs are internal stable identifiers, not array positions or display labels.
A revision cannot be its own parent.

## 4. Intent and creative plan

`intent` records the current project goal:

- `task_mode`: `autonomous` or `reference_remake`;
- target duration in output frames;
- original/current prompt;
- explicit user requirements;
- style tags used as creative context.

`creative_plan` is optional and contains section roles and goals. It explains
the intended structure, but the executable timeline is authoritative. A plan
cannot independently rebuild or overwrite the timeline.

Keeping the plan inside the same revision means a manual edit may update,
invalidate, or remove obsolete planned sections without leaving an older plan
as a second source of truth.

## 5. Assets

`assets` contains project-scoped references, not media bytes or storage paths.

Each AssetRef contains:

- stable `asset_id`;
- physical kind: video, photo, or audio;
- intended roles such as source, generic reference, style reference, remake
  reference, music, voiceover, or SFX;
- optional content hash and human-readable label.

Every media element in the timeline must reference an AssetRef declared by
the document. Video clips require video assets, image clips require photo
assets, and audio clips may read an audio asset or an authorized video's audio
stream.

Authorization, deletion state, storage keys, proxies, and analysis readiness
remain in the asset service/database and are checked again at execution time.

The Web upload path stores original bytes under the Project prefix, probes the
media, registers technical metadata in `assets`, and constructs a typed
`add_asset` Patch. That Patch creates one immutable Revision containing the
authorized AssetRefs. A batch rejected during media validation writes neither
registered assets nor a Revision; files are never silently skipped.

After the Revision commits, one durable media-processing Job claims the new
Assets and generates a normalized proxy plus thumbnail. Mutable readiness
(`uploaded`, `processing`, `ready`, or `failed`), derived storage keys, Job
identity, and diagnostic errors stay on the Asset record. They never create a
new Revision or mutate ProjectDoc. Semantic analysis remains a later Job over
the ready proxy.

## 6. Exact time model

ProjectDoc never stores authoritative time as floating-point seconds.

### Source time

```json
{"start": 12000, "end": 16000, "timescale": 1000}
```

This represents the half-open source interval `[12s, 16s)`. Start and end are
integer ticks in the asset's declared timescale.

### Output time

```json
{"start_frame": 240, "duration_frames": 120}
```

This represents `[240, 360)` in the project output. Project frame rate is a
reduced rational such as `30/1` or `30000/1001`.

### Playback mapping

Constant playback stores an exact positive rational rate. The document checks
that source duration, output duration, frame rate, and rate agree exactly.

Variable playback stores monotonic mapping points relative to the clip. The
first point must be `(0 output frames, 0 source ticks)` and the final point
must match both the clip's complete output duration and source duration.

FFmpeg and Remotion adapters may convert these values at their boundaries;
they do not replace them with a second float-based timeline.

## 7. Timeline tracks

The v1 timeline supports four track kinds:

| Track | Elements | Purpose |
|---|---|---|
| video | video clips and image clips | Primary footage, B-roll, and visual overlays |
| audio | audio clips | Source sound, music, voiceover, and SFX |
| caption | caption style and cues | ASR, generated, or user-authored text |
| graphic | graphic clips | Versioned Remotion or approved motion components |

All timeline element IDs are globally unique inside the document. Every
element's output interval must end within `timeline.duration_frames`.

The schema includes crop, reframe, visual motion, transitions, audio gain,
fades, constant/variable playback, versioned effects, and parameterized
graphics. Whether a configured definition is supported by a selected renderer
is a capability validation performed by ProjectService before preview or
commit.

### Extensible visual motion and transitions

Visual motion and transitions are versioned definition references rather than
closed effect-name enums:

```json
{
  "definition_id": "transition.cross_zoom",
  "definition_version": "1.0.0",
  "duration_frames": 10,
  "parameters": {"strength": 0.75}
}
```

`MotionSpec` uses the same definition identity and parameter shape without a
duration because its owning clip already supplies the output interval. Both
video and image clips may carry motion. A null motion means no additional
motion; a null outgoing transition means a hard cut.

The core schema intentionally does not enumerate FFmpeg, Remotion, GL
Transitions, or other library effect names. A later reviewed capability
catalog will map each definition/version to its allowed parameter schema,
license provenance, and supported renderer adapters. ProjectService must
reject unknown or unsupported definitions. AI selects definitions and valid
parameters; it does not inject renderer code into ProjectDoc.

## 8. Locks

A lock names a stable target and one or more protected properties.

Targets may be the project, section, track, clip, caption, or graphic.
Protection categories are:

- content;
- source range;
- output position;
- duration;
- audio;
- style;
- deletion.

The schema verifies that the target exists. ProjectService will later compute
indirect/ripple effects and reject a Patch that changes a protected property.

## 9. Reference context

Reference context is stored in the same revision and contains:

- reference asset and immutable Blueprint identity/version;
- Remake Policy dimensions divided into preserve, adapt, and exclude;
- reference-to-output Alignment records;
- explicit Adaptation Decisions.

The three policy sets cannot overlap. Alignment output elements must exist in
the same ProjectDoc and its output range must fit the timeline.

A `reference_remake` setup revision may exist before analysis produces a
ReferenceContext. Once context is present, the task mode must be
`reference_remake` and its reference asset must be a declared video asset.

## 10. Versioned dependencies

`dependencies` records the versions needed to understand or reproduce the
committed work:

- selected renderer implementation;
- Skills used by the project;
- motion/graphic components;
- analysis model configurations relevant to the revision provenance.

Secrets, vendor file handles, temporary model session IDs, and runtime
credentials are never stored in ProjectDoc.

## 11. Deliberately outside ProjectDoc

The following data is not authoritative edit state and must remain outside:

- uploaded media bytes and object-storage paths;
- proxies, thumbnails, waveforms, embeddings, and search indexes;
- full transcripts and raw model responses;
- Jobs, retries, leases, cancellation state, and Agent checkpoints;
- preview/export files and their delivery URLs;
- API credentials, provider sessions, and deployment configuration;
- full revision history and operation audit rows.

These objects reference `project_id` and `revision_id`; they never overwrite
the document.

## 12. Current migration boundary

The existing `aive.specs.EditPlanSpec` and `TimelineSpec` remain operational
only for the old pipeline while Stage 1 is implemented. They are not aliases
for ProjectDoc and new product behavior must not extend them.

Their removal condition is now met for the new investor-demo path:
ProjectService persistence, Patch application, semantic analysis, autonomous
first-cut authoring, FFmpeg rendering, and browser preview all consume
ProjectDoc. The legacy types remain only for still-unmigrated legacy CLI and
reference modules; new behavior must not extend them.
