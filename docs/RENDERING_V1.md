# ProjectDoc Rendering v1

Status: authoritative implementation constraints for preview and export.

This contract describes the first revision-bound FFmpeg path. It is a
capability baseline, not the final product limit. Extend the same path as
transitions, motion, captions, graphics, overlays, and Remotion arrive; do not
create another timeline or artifact model.

## 1. Identity and ownership

- Every render request names one exact `project_id` and immutable
  `revision_id`.
- The renderer reads `ProjectRevision.document`; it must not reconstruct or
  persist a legacy `TimelineSpec` as a second source of truth.
- Every preview/export is a derived `RenderArtifact` carrying the input
  revision, durable Job identity, renderer name, and renderer version.
- A historical artifact remains accessible, but `current preview` resolves
  artifacts for `projects.current_revision_id` only.
- Preview uses processed proxy media when only pixels are required. Because
  the analysis proxy deliberately has no audio, a preview that contains an
  explicit source-audio clip resolves that Asset to its original media. HQ
  export always resolves the same ProjectDoc against original media.

## 2. Job and storage rules

- Enqueue creates the Job and Artifact atomically and is idempotent within a
  project.
- Rendering never happens inside a database transaction.
- A completed file is committed to storage before the Artifact becomes
  `ready`.
- Interrupted `queued` or `running` Jobs are recoverable. Re-running a ready
  Artifact is a no-op.
- A failed render records both Job and Artifact failure and never mutates or
  clears the project revision.
- API routes orchestrate only. FFmpeg command construction remains behind the
  renderer provider.

## 3. Post-render review

An autonomous first-cut preview is a gate for an immutable candidate Revision,
not merely a thumbnail of an already committed edit:

1. ProjectService stores the validated candidate in `awaiting_review`.
2. A `revision_render` Job produces a candidate-bound proxy Artifact.
3. A separate `render_review` Job records frozen inspection context and a typed
   report on that Artifact.
4. Only an accepted report may trigger atomic candidate acceptance.

If all blocking issues are explicitly repairable and the bounded attempt has
not been used, rejection may trigger one `render_autofix` Job. That Job creates
a new candidate from the original committed base through the normal typed
Patch path. It never edits the rejected Revision or Artifact. The replacement
is rendered and reviewed through steps 2-4 again; a second rejection is
terminal.

`RenderReviewReport` binds `project_id`, `revision_id`, and `artifact_id` and
records provider/version, inspection-derived and model-derived issues, whether
a visual review actually occurred, and the final accepted decision. A
rejected or failed review cannot advance the project head.

The default deterministic Reviewer performs decode, duration,
dimension, representative-frame, audio-presence, and sharpness checks. A
configured Reviewer provider may add multimodal creative judgment through the
same contract. Manual preview and export requests are not automatically sent
to the creative Reviewer; the gate applies to autonomous candidate acceptance.

### 3.1 Continuous-edit preflight

Before an immediate continuous-edit candidate can advance the Project head,
the application passes that exact in-memory candidate through the configured
`RendererProvider.validate` path and resolves its proxy media inputs. This is a
read-only preflight: it creates no Artifact, performs no encode, and runs before
the repository commit transaction. A primary-track gap or overlap, unsupported
layer, or unavailable source rejects the Agent Turn while the previous Revision
remains current.

The preflight does not replace rendered-output review or user confirmation.
Those remain required for the later high-impact Candidate gate.

## 4. Currently executable hybrid subset

The current provider renders:

- one enabled, gapless primary visual track;
- hard cuts between video and photo clips;
- exact frame-range previews, rebased to zero;
- constant playback rates, normalized crop, and center reframe;
- explicit ProjectDoc audio clips with constant playback, gain, fades, delay,
  and mixing;
- authorized music/voiceover/SFX audio Assets, including the audio-only proxy
  and waveform-thumbnail ingest path;
- `speech` ducking on music clips through an FFmpeg sidechain compressor fed
  by active source and voiceover tracks;
- `caption.clean@1.0.0` CaptionTracks. The renderer validates the versioned
  component parameters, resolves the registered font through fontconfig,
  rasterizes transparent cue layers with Pillow, and overlays those layers at
  exact ProjectDoc frame ranges for both proxy preview and HQ export;
- approved Registry-backed Remotion Caption and Graphic components. Each exact
  version and prop object is validated, rendered as a transparent full-frame
  overlay in the restricted runtime, and composited at its ProjectDoc frame
  range for full/range preview and HQ export;
- BT.709 even-dimension H.264/AAC MP4 output;
- proxy preview (or original media when source audio is required) and
  original-media HQ export through the same compiler path.

Until implemented and covered by media regression tests, the provider must
reject rather than ignore:

- b-roll or overlay tracks;
- motion definitions, transitions, and effects;
- face, saliency, or manual reframe;
- variable playback and ducking groups other than `speech`;
- non-BT.709 output and odd H.264 dimensions.

## 5. Extension rule

To add a capability:

1. keep timing and parameters in ProjectDoc/Patch contracts;
2. add a focused failure or media regression test;
3. implement it in the renderer adapter or a Remotion adapter behind the same
   provider boundary;
4. remove only the corresponding explicit rejection;
5. verify full preview, affected-range preview, and HQ export agree.

Remotion may complement FFmpeg for frame-driven captions and motion graphics.
It must consume the same ProjectDoc and produce the same revision-bound
Artifact contract.

The generated-component lifecycle and runtime ABI are defined in
`REMOTION_COMPONENTS_V1.md`. The configured hybrid provider now resolves exact
published Caption and Graphic bundles and produces preview/export from the
named Revision. A validated but unpublished candidate is never renderable, and
published motion/transition/effect usages remain rejected until their distinct
media-input execution paths exist.

`caption.clean` remains the deterministic static baseline. Word-level
highlighting, karaoke timing, bounce/pop motion, and other per-frame typography
belong in new approved Remotion caption component versions rather than hidden
conditionals inside the FFmpeg adapter.

The FFmpeg renderer host must provide `ffmpeg`, `ffprobe`, and fontconfig's
`fc-match`. A missing font runtime or unresolved component font is a render
failure; the renderer must not silently substitute a different caption path.
