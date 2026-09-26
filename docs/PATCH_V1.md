# Patch v1 Contract

Status: implemented request contract, operation whitelist, ProjectService,
revision persistence, HTTP API, and Web media initialization path.

Source modules:

- `packages/aive/contracts/patch.py`
- `packages/aive/contracts/patch_project.py`
- `packages/aive/contracts/patch_timeline.py`

Contract tests: `tests/contract/test_patch.py`.

Checked-in example: `examples/patch_v1.json`.

## 1. Purpose

`ProjectDoc` answers “what is the project now?” A `Patch` answers “what exact,
bounded change should create the next project revision?”

Manual editor actions and Agent actions use this same contract. A Patch is not
stored as the only source of truth: after a successful commit, the new complete
ProjectDoc snapshot is authoritative and the Patch remains audit/explanation
data.

Patch v1 is deliberately not RFC 6902 JSON Patch. It has no arbitrary `path`,
`value`, or mutable array index. Every operation:

- has a stable `operation_id`;
- uses a domain name such as `set_audio_gain`;
- targets stable project IDs;
- carries a closed, typed payload;
- is applied in list order inside one atomic candidate revision.

## 2. Patch envelope

| Field | Meaning |
|---|---|
| `patch_version` | Exactly `1.0` |
| `patch_id` | Stable identity of this Patch request |
| `project_id` | Project to change |
| `base_revision_id` | Exact revision the author saw; stale bases must be rejected |
| `idempotency_key` | Replay key; the same request must not commit twice |
| `origin` | `user`, `editor`, `agent`, or `system` |
| `request_id` | Optional correlation ID for a UI action or Agent turn |
| `summary` | Optional human-readable explanation, never executable state |
| `scope` | Boundary the complete effect of the Patch must stay inside |
| `operations` | One or more typed operations, executed in order |

The schema rejects unknown envelope and operation fields. Patch objects are
immutable after validation, and operation IDs must be unique within a Patch.

## 3. Scope

`bounded` scope requires at least one `target_id` or an `output_range`. It is
used for local conversational and manual edits. `global` scope has no target
IDs or output range and is reserved for changes whose declared effect is the
whole project, such as replacing output settings or the creative plan.

| Scope field | Meaning |
|---|---|
| `target_ids` | Stable IDs that may be directly changed |
| `output_range` | Optional half-open output interval that may be affected |
| `allow_ripple` | Whether dependent timing outside direct targets may move |
| `preserve_ids` | IDs that must remain unchanged even when ripple is allowed |

Target and preserve sets cannot contain duplicates or overlap. Scope is a
declared constraint, not proof that an operation is safe. ProjectService must
calculate the actual direct and indirect effects before committing.

## 4. Supported operations

Patch v1 supports exactly 48 operations. Adding an operation requires updating
the discriminated union, the ordered whitelist, this document, and contract
tests. Generic `replace`, JSON Pointer paths, and arbitrary mutation payloads
are not supported.

### Project document and tracks

| Operation | Required payload | Semantics |
|---|---|---|
| `set_intent` | `intent` | Replace the complete ProjectIntent value |
| `add_asset` | `asset` | Add one project-scoped AssetRef |
| `update_asset` | `asset` | Replace the AssetRef with the same `asset_id` |
| `remove_asset` | `asset_id` | Remove an unreferenced asset from the project |
| `set_creative_plan` | `creative_plan` | Replace or clear the complete CreativePlan |
| `set_timeline_duration` | `duration_frames` | Set the positive output timeline duration |
| `set_output_settings` | `output` | Replace output size, fps, audio rate, and color space |
| `add_track` | `track`, `before_track_id?` | Add a typed track before a stable sibling, or append |
| `remove_track` | `track_id` | Remove a track and its contained elements |
| `move_track` | `track_id`, `before_track_id?` | Reorder by stable sibling ID, or move to the end |
| `set_track_enabled` | `track_id`, `enabled` | Enable or disable a track without deleting it |
| `add_lock` | `lock` | Add an explicit protection rule |
| `remove_lock` | `lock_id` | Remove a lock; authorization is checked by the service |
| `set_reference_context` | `reference_context` | Replace or clear the complete reference context |
| `set_reference_policy` | `policy` | Replace preserve/adapt/exclude policy and music policy |
| `upsert_reference_alignment` | `alignment` | Add or replace one alignment by `alignment_id` |
| `remove_reference_alignment` | `alignment_id` | Remove one reference/output alignment |
| `upsert_adaptation_decision` | `decision` | Add or replace one decision by `decision_id` |
| `remove_adaptation_decision` | `decision_id` | Remove one adaptation decision |
| `set_dependencies` | `dependencies` | Replace the complete reproducibility dependency set |

Whole-value setters are intentional for cohesive aggregates such as intent,
creative plan, output settings, and dependencies. They prevent dozens of tiny
field operations while retaining strict validation.

### Visual timeline

| Operation | Required payload | Semantics |
|---|---|---|
| `insert_visual_clip` | `track_id`, `clip`, `before_clip_id?` | Insert a complete video/image clip, or append |
| `delete_visual_clip` | `clip_id` | Remove one visual clip |
| `replace_visual_source` | `clip_id`, `source` | Replace with typed video source range or photo asset |
| `set_visual_output` | `clip_id`, `output` | Set output position and duration together |
| `set_video_playback` | `clip_id`, `playback` | Set constant or variable video time mapping |
| `set_visual_crop` | `clip_id`, `crop` | Set or clear normalized crop |
| `set_visual_reframe` | `clip_id`, `reframe` | Set or clear center/face/saliency/manual reframe |
| `set_visual_motion` | `clip_id`, `motion` | Set or clear a versioned motion definition |
| `set_visual_transition` | `clip_id`, `transition` | Set or clear the outgoing transition; clear means hard cut |
| `upsert_visual_effect` | `clip_id`, `effect` | Add or replace an effect by `effect_id` |
| `remove_visual_effect` | `clip_id`, `effect_id` | Remove one effect |

### Audio timeline

| Operation | Required payload | Semantics |
|---|---|---|
| `insert_audio_clip` | `track_id`, `clip`, `before_clip_id?` | Insert a complete audio clip, or append |
| `delete_audio_clip` | `clip_id` | Remove one audio clip |
| `replace_audio_source` | `clip_id`, `asset_id`, `source` | Replace the asset and exact source interval |
| `set_audio_output` | `clip_id`, `output` | Set output position and duration together |
| `set_audio_playback` | `clip_id`, `playback` | Set constant or variable audio time mapping |
| `set_audio_gain` | `clip_id`, `gain_db` | Set gain from -96 dB through +24 dB |
| `set_audio_fades` | `clip_id`, fade fields | Set non-negative fade-in and fade-out frames together |
| `set_audio_ducking` | `clip_id`, `ducking_group` | Set or clear the ducking group |

### Captions

| Operation | Required payload | Semantics |
|---|---|---|
| `add_caption` | `track_id`, `cue` | Add a complete cue to a caption track |
| `update_caption_text` | `cue_id`, `text` | Replace cue text without changing timing |
| `set_caption_output` | `cue_id`, `output` | Set cue position and duration together |
| `delete_caption` | `cue_id` | Remove one cue |
| `set_caption_style` | `track_id`, `style` | Replace the complete versioned track style |

### Graphics

| Operation | Required payload | Semantics |
|---|---|---|
| `add_graphic` | `track_id`, `graphic` | Add a complete versioned graphic clip |
| `set_graphic_output` | `graphic_id`, `output` | Set graphic position and duration together |
| `update_graphic_props` | `graphic_id`, `props` | Replace the complete component props map |
| `delete_graphic` | `graphic_id` | Remove one graphic clip |

Motion, transition, effect, and graphic parameters are JSON-shaped only at
their versioned definition/component boundary. ProjectService must verify that
the exact definition and version exist, that their parameter schema accepts the
payload, and that the selected preview/export renderer supports them.

Neither AI nor the editor may put executable JavaScript, JSX, GLSL, FFmpeg
commands, or remote code into these parameter maps. New implementations enter
through a reviewed capability definition; a Patch can only select and
parameterize one.

## 5. Required execution semantics

The schema only proves that a request is well formed. The in-memory
`ProjectService.create_patch_candidate` path now performs the candidate steps
below; repository responsibilities remain explicitly separate:

1. the API/repository layer authenticates project access and resolves
   `project_id` (**not yet implemented**);
2. the repository returns the prior result for an already committed
   `idempotency_key` and rejects reuse with a different request hash;
3. reject a `base_revision_id` that differs from the supplied immutable base
   with a typed conflict; the repository must repeat this check against the
   current head during atomic commit;
4. resolve every stable target and validate track/element kind compatibility;
5. calculate direct effects, then enforce bounded scope, preserve IDs, and all
   existing locks; Patch v1 currently has no automatic ripple operation, so
   `allow_ripple` grants permission but does not itself move any element;
6. invoke the required capability-validator port for every versioned motion,
   transition, effect, caption style, and graphic component; asset authorization
   remains an API/repository concern;
7. apply every operation in order to an in-memory candidate;
8. validate the resulting complete ProjectDoc graph;
9. atomically store one immutable revision and advance
   `projects.current_revision_id` with the implemented Revision Repository.

Any failure rejects the complete Patch. Partial application is forbidden.

## 6. Deliberate v1 exclusions

Patch v1 does not include arbitrary JSON field edits, numeric array positions,
implicit asset upload, media analysis, model prompts, rendering, export, Job
state, revision commit commands, or undo commands. Upload/analysis/render are
separate workflows; commit/undo/restore belong to ProjectService revision
operations rather than document mutation operations.

Revision and commit state transitions are defined in
`docs/REVISION_COMMIT_STATE_MACHINE.md`. Candidate construction, ordered Patch
application, scope/lock enforcement, capability-port validation, asset
authorization, complete ProjectDoc validation, and repository-backed atomic
commit are implemented. Media upload uses the same `add_asset` operation
instead of directly editing ProjectDoc.
