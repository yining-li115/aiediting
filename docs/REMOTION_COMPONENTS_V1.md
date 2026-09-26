# Remotion Component Registry and Sandbox v1

Status: authoritative contract for generated render components. The v1 usable
loop is complete for published `caption` and `graphic` overlay components.
Motion, transition, and pixel-transforming effect components remain outside the
executable support matrix until their media-input ABI is defined.

## 1. Ownership

Generated component code is an immutable render capability, not project state.

- `ProjectDoc` remains the only authoritative edit state and refers only to a
  published capability by `kind`, `name`, exact `version`, validated props, and
  a `dependencies.components` entry pinned to the published bundle digest.
- Canonical candidate source archives, bundles, and preview stills live in
  object storage under `_shared/component-candidates/`; they are never written
  into the repository's React source tree. The durable Agent tool trace may
  retain the submitted source as audit input, but it is not loaded as product
  application code.
- Approved versions live under
  `_shared/component-registry/{kind}/{name}/{version}/`.
- A published name/version is immutable. A different implementation requires a
  new semantic version.
- Candidate and registry objects are derived/executable artifacts. They never
  become a second Timeline, ProjectDoc, or Revision.

## 2. Lifecycle

```text
Agent or importer proposes manifest + source files
  -> contract and JSON Schema preflight
  -> isolated Remotion bundle
  -> render deterministic preview still
  -> persist immutable candidate and validation evidence
  -> explicit approval
  -> immutable registry publication
  -> Agent searches the published Registry
  -> typed Patch references the exact capability version
  -> ProjectService validates props and runtime availability
  -> named Revision renders the component into preview or export
```

Creation and publication are deliberately separate. A successful build does
not authorize a component for projects. Rejected candidates remain auditable
and cannot be published.

The manifest freezes:

- capability kind and intended usage;
- stable name and semantic version;
- Remotion runtime version;
- entrypoint and exported component;
- closed JSON Schema and example props;
- commercial-use license evidence;
- creating Agent Run identity when one exists.

## 3. Local sandbox boundary

The investor-demo adapter uses macOS `sandbox-exec`, a pinned Remotion runtime,
and Remotion's managed Chrome Headless Shell. It:

- copies at most 1 MB and 32 source files into a fresh temporary directory;
- exposes only an allowlist of browser-safe React/Remotion packages;
- strips provider secrets and `HOME` from the subprocess environment;
- denies external network access while allowing loopback required by the
  Remotion bundle server;
- denies filesystem writes outside the candidate temporary directory;
- applies a bounded timeout;
- requires a successful bundle, composition selection, and real PNG render;
- archives source and bundle deterministically and records SHA-256 digests.

This local profile is sufficient for the single-machine demo, but it is not the
production tenant-security boundary: loopback is available to the renderer and
`sandbox-exec` is macOS-specific. A deployed multi-tenant version must replace
the adapter with an ephemeral container or microVM with no host loopback,
read-only runtime dependencies, resource quotas, and object-store-only output.
The contracts and application service do not change when that adapter changes.

## 4. Dependency and license policy

Candidate code may import only the pinned packages in the runtime allowlist.
It cannot install packages during validation, import Node built-ins, or fetch
code from the internet. Adding a package is a repository change: pin it in
`apps/remotion/package.json`, review its license/security, then extend the
allowlist with a regression test.

Only manifests that explicitly permit commercial use may become validated
candidates. Official Remotion packages and third-party templates still need
their own recorded license source; “found on the internet” is not approval.

## 5. Runtime setup and verification

```bash
cd apps/remotion
npm install
npm run install-browser

cd ../..
uv run pytest tests/integration/test_remotion_component_sandbox.py -q
```

The integration tests are intentionally real: one generated component must
bundle and render to PNG with external networking denied, one candidate that
imports `node:fs` must be rejected, and one approved Graphic component must be
rendered to transparent ProRes 4444 and composited into a real MP4.

## 6. Runtime ABI

Candidate components export the manifest's `export_name`. At runtime the host
passes:

- every manifest-validated `componentProps` field as a normal React prop;
- one reserved `aive` prop containing stable render context;
- Remotion composition metadata derived from the ProjectDoc output and the
  exact visible duration.

For captions, `aive` contains `usage`, `text`, optional `speaker`, `elementId`,
`trackId`, `revisionId`, `timelineStartFrame`, and `visibleStartFrame`. Graphics
receive the same identity and timing fields except speech text/speaker. Local
component animation uses `useCurrentFrame()` and `useVideoConfig()`; it must not
read wall-clock time, randomness, environment variables, files, or the network.

The runtime renders a full-frame transparent overlay using ProRes 4444. The
hybrid `RendererProvider` uses the existing FFmpeg renderer for authoritative
video, photos, audio, `caption.clean`, and mix behavior, then composites each
approved Remotion overlay at its exact ProjectDoc frame range. Full preview,
range preview, and HQ export therefore retain the same Revision and Artifact
contracts.

## 7. Agent and approval boundary

The bounded Continuous Editing tool loop exposes:

- `search_components`, which returns only immutable published versions;
- `create_component_candidate`, which validates generated source in the
  sandbox and returns candidate evidence but has no publication authority.

An Agent-created manifest must bind `created_by_run_id` to the active
`AgentTurn`, use generated commercial-use license evidence, and target only the
currently executable `caption` or `graphic` usage. Before a Patch may reference
a non-built-in component, that exact usage/name/version must have been returned
by `search_components` in the same persisted tool trace. Introducing it also
requires one `set_dependencies` operation that preserves every existing
dependency and pins the new component's exact bundle digest.

Publication remains an explicit user/reviewer action through
`POST /components/candidates/{candidate_id}/publish`. The candidate metadata and
PNG are readable before approval. The model has no publish tool and a validated
candidate cannot be used until publication succeeds.

## 8. Current integration boundary

Completed:

- manifest, source, candidate, validation, approval, and publication contracts;
- storage-backed immutable candidate/registry repository;
- idempotent application service with license and JSON Schema preflight;
- actual Remotion bundle/render validation in a fail-closed local sandbox;
- exact registry lookup and prop-schema capability validator;
- Agent `search_components` and `create_component_candidate` tools with
  persisted results and explicit approval separation;
- candidate metadata/preview, publication, and Registry search HTTP adapters;
- hybrid FFmpeg + Remotion rendering of approved Caption and Graphic overlays
  for a named ProjectDoc Revision, including preview, range preview, and export;
- ProjectService preflight that rejects unpublished versions, invalid props,
  unsupported usages, or an unavailable runtime before commit.

Not yet connected or intentionally unsupported:

- browser approval UI for candidate review/publication;
- a first curated set of production caption/title/brand components;
- media-input ABI and preview/export support for motion, transitions, and
  pixel-transforming effects;
- multi-tenant container/microVM sandbox adapter;
- cleanup/retention policy for rejected candidates.

Publishing does not silently widen the renderer support matrix. The configured
capability validator currently admits only published `caption` and `graphic`
usages; published motion, transition, or effect entries remain rejected.
