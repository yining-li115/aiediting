# Repository Instructions

This repository is building the complete investor-demo path described in
`docs/INVESTOR_DEMO_ROADMAP.md`. Before changing code, read:

1. `docs/ARCHITECTURE_GUARDRAILS.md`
2. `docs/AGENTIC_WORKSPACE_ARCHITECTURE.md`
3. `docs/INVESTOR_DEMO_ROADMAP.md`
4. `docs/AI_CODING_RULES.md`
5. `docs/PROJECT_DOC_V1.md` when changing project or timeline state
6. `docs/PATCH_V1.md` when adding or changing edit behavior
7. `docs/REVISION_COMMIT_STATE_MACHINE.md` when changing version or commit behavior
8. `docs/DIRECTOR_V1.md` when changing first-cut or Agent behavior
8. `docs/RENDERING_V1.md` when changing preview, export, or renderer behavior

These rules apply to every change unless the user explicitly overrides them.

## Required boundaries

- `ProjectDoc` in an immutable project revision is the only authoritative edit
  state. Do not create a second authoritative timeline, plan, or UI document.
- All edits, whether produced by the UI or an agent, go through the same typed
  Patch and `ProjectService` commit path.
- API routes and worker entry points orchestrate only. They do not contain
  domain rules, direct SQL, model prompts, or FFmpeg command construction.
- Domain contracts do not import FastAPI, SQLAlchemy, vendor SDKs, FFmpeg, or
  frontend code.
- Vendor-specific behavior stays behind provider interfaces. Rendering stays
  behind `RendererProvider`.
- Keep the current FFmpeg implementation unless a task explicitly replaces a
  supported path. Remotion complements FFmpeg; it does not create a second
  timeline model.

## Change discipline

- Make the smallest coherent change that completes the requested behavior.
- Do not add speculative abstractions, unused configuration, placeholder
  services, duplicate schemas, or empty directory scaffolding.
- Do not perform unrelated cleanup in a feature change.
- Do not add a dependency without documenting the concrete use in the change.
- Do not delete or rewrite committed migrations. Add a new migration.
- Preserve user changes already present in the working tree.
- Never silently weaken a lock, permission, revision check, or unsupported
  media/effect constraint.

## Required implementation order

For a new behavior, work in this order:

1. Contract and invariant.
2. Focused test demonstrating the behavior or failure.
3. Domain/application service.
4. Repository or external adapter.
5. API/worker entry point.
6. UI integration.
7. Vertical integration or end-to-end test.

If a requested change cannot fit this order, explain why before implementing.

## Completion standard

- Run the smallest relevant tests while iterating, then the affected suite.
- Run Python tests and Web type checking for changes that cross the API
  boundary.
- Report unsupported cases explicitly; do not claim success from mocks alone.
- A feature is not complete when only a schema, mock, route, or UI control
  exists. The user-visible path must be connected or clearly labeled partial.
