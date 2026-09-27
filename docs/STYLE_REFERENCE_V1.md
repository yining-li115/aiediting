# Style Reference v2

Style Reference is a model-understood input to Autonomous Edit. It transfers
editing grammar from one or more user-supplied videos without turning their
content into selectable output media.

Remake Reference is a separate, later workflow. A Style Reference never creates
shot-to-shot alignment, copies a reference sequence, or switches the project to
`reference_remake` mode.

## Invariants

1. A Style Reference is a project Asset with roles `reference` and
   `style_reference`. It uses the durable upload, proxy, physical-Shot,
   ASR/raw-audio understanding, model-authored Moment, and evidence pipeline.
2. Style-reference Assets and Moments never enter
   `AgentRunContext.assets` or `AgentRunContext.moments`; they cannot be
   selected by a first-cut or continuous-edit Patch.
3. A `StyleUnderstandingProvider` watches the reference and receives its
   objective facts plus analyzed Moments. It authors the transferable grammar.
   Application code does not infer pace, genre, emotion, shot language,
   transitions, or taste from thresholds and distributions.
4. The resulting `StyleReferenceBlueprint` is immutable and content-addressed
   by its canonical objective evidence and analyzed Moments, including their
   model provenance. Identical evidence reuses the same Blueprint; changed
   evidence creates another Blueprint.
5. The exact Blueprints used by a Director run are frozen into its durable
   `AgentRunContext`. Retry and recovery cannot silently adopt another result.
6. The Director may use a Blueprint only as a creative prior. It still chooses
   authorized source Moment IDs and returns the same closed `EditBlueprint`.
7. Deterministic validation checks identity, project scope, evidence closure,
   exact ranges, schema, authorization, and renderer support. Whether the edit
   expresses the reference well is decided by the creative Reviewer model.
8. Model, validation, and render failures remain visible and leave the current
   Revision unchanged. There is no heuristic or offline style fallback.

## StyleReferenceBlueprint v2

The Blueprint defines a structured grammar for a model answer while keeping the
creative vocabulary open:

- a free-language summary;
- relative temporal phases and their creative purpose;
- narrative, shot, transition, motion, color/composition, audio-visual, caption,
  typography, and graphic patterns;
- how each pattern should transfer to different source material;
- transferable principles and explicit `avoid_copying` guidance;
- evidence citations to supplied reference Moments/ranges;
- confidence, uncertainty, provider, model, and prompt version;
- only objective container facts: width, height, duration, and Moment count.

It stores no pixels, audio bytes, transcript body, storage key, keyframe path,
or application-authored pace/style label. Free descriptions are primary; closed
enums remain only where an executable renderer capability requires one.

## Runtime sequence

```text
upload Style Reference
  -> media processing and objective evidence
  -> physical Shot detection
  -> ASR + raw Audio Understanding + Source Understanding
  -> model-authored semantic Moments
  -> StyleUnderstandingProvider
  -> immutable StyleReferenceBlueprint v2
  -> frozen AgentRunContext.style_reference_blueprints
  -> Director EditBlueprint
  -> deterministic legality review + model creative review
  -> typed Patch -> candidate Revision -> preview -> render review
```

The ProjectDoc remains the only authoritative edit state. Blueprints are
derived, auditable evidence and never become a second timeline.
