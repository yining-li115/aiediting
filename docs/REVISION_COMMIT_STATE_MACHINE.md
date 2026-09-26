# Immutable Revision and Commit State Machine

Status: immutable Revision contract, ProjectService candidate construction,
SQLAlchemy repository persistence, idempotency, atomic head compare-and-swap,
restore-as-new-revision, HTTP endpoints, generated Web types, and committed
history/restore UI are implemented.

Source modules:

- `packages/aive/contracts/revision.py`
- `packages/aive/project/commit_state.py`
- `packages/aive/project/service.py`
- `packages/aive/project/application.py`
- `packages/aive/project/repository.py`
- `packages/aive/media/upload.py`
- `packages/aive/media/processing.py`
- `packages/aive/db/media_repository.py`
- `packages/aive/db/project_repository.py`
- `packages/aive/db/models.py`
- `migrations/versions/6d2f0d4e8a91_project_revision_repository.py`
- `migrations/versions/84c9a6e75d21_media_processing_readiness.py`

Tests:

- `tests/contract/test_revision.py`
- `tests/unit/test_commit_state.py`
- `tests/unit/test_project_service.py`
- `tests/test_revision_repository.py`

## 1. Invariants

1. A `ProjectRevision` contains one complete ProjectDoc snapshot.
2. The snapshot, provenance, and document hash never change after creation.
3. A candidate Revision is not current merely because it exists or has a
   preview.
4. `projects.current_revision_id` is the sole Project head.
5. Commit advances that pointer only with an atomic compare-and-swap from the
   request's `base_revision_id`.
6. A failed, rejected, abandoned, or conflicting attempt never changes the
   Project head.
7. Restore creates a new child Revision; it never edits an old Revision or
   moves history backward in place.

The Revision row does not carry a mutable `candidate/committed` flag. Its
current or historical role is established by successful commit records and the
Project head pointer. This keeps the saved document immutable while allowing a
validated candidate to exist during asynchronous preview or review.

## 2. Revision provenance

Every revision records one of three reasons:

| Kind | Parent | Required provenance |
|---|---|---|
| `initial` | none | origin and optional actor/request metadata |
| `patch` | current base revision | `patch_id` |
| `restore` | current base revision | `restored_from_revision_id` |

The restore source is historical content, while the parent is the Project head
at restore time. They are intentionally different identities.

`document_hash` is intended to be the SHA-256 of canonical ProjectDoc JSON.
The service will compute it; clients and models do not supply trusted hashes.

## 3. Commit request identity

One commit attempt carries:

- `commit_id`;
- `project_id`;
- request kind: `initialize`, `patch`, or `restore`;
- stable request ID;
- exact `base_revision_id`, except for initialization;
- project-scoped `idempotency_key`;
- server-computed canonical `request_hash`;
- candidate/result revision IDs and a typed issue when applicable.

The idempotency key identifies one logical request. The hash distinguishes a
safe replay from accidental reuse of the same key for different content.

## 4. States

| State | Meaning | Terminal |
|---|---|---|
| `received` | Typed request accepted for processing | no |
| `candidate_created` | New immutable ProjectDoc candidate built from the base | no |
| `validated` | Scope, locks, graph, authorization, and capability checks passed | no |
| `awaiting_review` | Validated candidate may be previewed before acceptance | no |
| `committed` | Candidate became the new Project head | yes |
| `duplicate` | Same idempotent request already has a result | yes |
| `rejected` | Deterministic request/domain validation failed | yes |
| `conflict` | Base was stale initially or head changed before commit | yes |
| `abandoned` | Reviewed candidate was declined or superseded | yes |
| `failed` | Retryable or diagnostic infrastructure failure | yes |

## 5. Legal transitions

```text
received
  ├─> candidate_created
  │     ├─> validated
  │     │     ├─> committed
  │     │     ├─> awaiting_review
  │     │     │     ├─> committed
  │     │     │     ├─> abandoned
  │     │     │     ├─> conflict
  │     │     │     └─> failed
  │     │     ├─> conflict
  │     │     └─> failed
  │     ├─> rejected
  │     └─> failed
  ├─> duplicate
  ├─> rejected
  ├─> conflict
  └─> failed
```

All terminal states reject further transitions. In particular, a request
cannot jump from `received` to `committed`, and validation success alone never
moves the Project head.

## 6. Immediate and reviewed commits

Manual edits and low-risk Agent edits normally use:

```text
received -> candidate_created -> validated -> committed
```

An expensive or high-impact Agent edit may use:

```text
received -> candidate_created -> validated -> awaiting_review
```

The validated candidate can be persisted and rendered by ID. Acceptance still
performs the final head compare-and-swap. If another edit committed during
review, the attempt becomes `conflict`; it does not replace the newer head.

Declining a candidate produces `abandoned`. The immutable candidate and its
preview may remain available for diagnostics or later comparison, but neither
is presented as committed history.

## 7. Idempotency

The repository enforces a unique constraint on
`(project_id, idempotency_key)`.

On replay:

1. Load the existing commit attempt.
2. Compare its server-computed `request_hash` with the new request hash.
3. If they differ, reject with `idempotency_conflict`.
4. If they match and a result exists, return `duplicate` with the original
   `result_revision_id`.
5. If they match and processing is still active, return the existing attempt
   instead of starting another one.

The same logical action can therefore be retried after network interruption
without creating multiple revisions.

## 8. Optimistic concurrency

ProjectService checks the base twice:

1. Before candidate construction, reject an already stale base.
2. During atomic commit, compare-and-swap the Project head because it may have
   changed while validation, preview, or review was running.

The persistence operation is implemented as one conditional database update:

```sql
UPDATE projects
SET current_revision_id = :candidate_revision_id
WHERE id = :project_id
  AND current_revision_id = :base_revision_id;
```

Zero updated rows means `project_head_changed`. The Project head remains
untouched, the attempt becomes `conflict`, and the immutable candidate may
remain stored for diagnostics; it is never presented as committed history.

For project initialization, inserting the Project and setting its initial
Revision head occur in one transaction without a base revision.

## 9. Atomic commit boundary

The implemented transaction performs only database work:

1. verify or insert the idempotent commit record;
2. ensure the immutable candidate Revision exists;
3. compare-and-swap `projects.current_revision_id`;
4. retain provenance on the immutable Revision;
5. mark the commit attempt `committed` with its result revision; and
6. commit the transaction.

Per-operation audit rows and outbox events are deferred to the API/event slice;
they must join this same transaction rather than creating another commit path.

Media inspection, models, downloads, rendering, and capability discovery never
run inside this transaction.

Continuous editing may run a read-only renderer/source preflight on the pure
in-memory candidate before this transaction begins. A failed preflight stores
neither the candidate nor a commit attempt and cannot move the Project head.

## 10. Implemented persistence shape

Migration `6d2f0d4e8a91` adds:

### `project_revisions`

- revision ID and project ID;
- parent revision ID;
- commit ID;
- immutable ProjectDoc JSON;
- canonical document hash;
- provenance JSON or queryable provenance columns;
- creation timestamp.

The document JSON and hash are insert-only.

### `project_commits`

- commit/request/base/idempotency identities;
- request hash and request kind;
- current lifecycle state;
- candidate and result revision IDs;
- typed issue JSON;
- created/updated timestamps;
- unique `(project_id, idempotency_key)`.

### `projects`

- new `current_revision_id` foreign key;
- legacy project fields remain compatibility data until the ProjectDoc browser
  path replaces them.

The new Project head is nullable so legacy rows do not receive a fabricated
Revision during migration. A later explicit backfill must create real initial
ProjectDoc snapshots before those projects enter the new editing path.

## 11. Current implementation boundary

The state machine and in-memory ProjectService now:

- construct the minimal initial ProjectDoc candidate for a replay-safe project
  creation request;
- apply all 48 Patch v1 operations in declared list order;
- reject unknown or wrong-kind stable targets;
- enforce bounded scope, preserve IDs, and existing ProjectDoc locks against
  each operation's calculated direct impact;
- prohibit Agent-origin Patches from removing protection locks;
- invoke a required capability-validator port for all versioned executable
  definitions/components;
- validate the complete ProjectDoc only after coordinated operations have been
  applied; and
- return an immutable candidate Revision in `validated` or `awaiting_review`.

The SQLAlchemy Revision Repository now:

- creates the Project row, initial Revision, initialize Commit, and head in one
  transaction, rolling back all four on failure;
- stores insert-only ProjectDoc snapshots and mutable commit-attempt rows;
- enforces project-scoped idempotency in code and with a database uniqueness
  constraint;
- returns the original result for a matching replay and rejects key reuse with
  a different request hash;
- persists reviewed candidates without changing the Project head;
- excludes uncommitted candidates from user-facing committed history;
- advances `projects.current_revision_id` with one atomic compare-and-swap;
- records `project_head_changed` without overwriting a newer edit; and
- implements undo as a restore candidate whose content comes from history but
  whose parent is the current head.

The Project HTTP boundary now exposes:

- atomic and replay-safe `POST /projects` initialization;
- all-or-nothing project media upload followed by a server-generated,
  authorized `add_asset` Patch;
- current, historical, and named Revision reads;
- immediate or review-gated typed Patch submission;
- project-scoped candidate acceptance; and
- restore-as-new-revision submission.

The Web client consumes generated `CreateProjectRequest`, `ProjectDoc`,
`Patch`, `ProjectRevision`, `CommitAttempt`, and restore/mutation types. The
home workspace creates a Project and enters its project route; the history
screen shows committed revisions only and restores a selected snapshot through
the same application service and repository path.

It does not yet:

- authenticate users or provide the concrete installed-capability registry;
- perform automatic ripple edits (no Patch v1 operation currently requests
  one);
- backfill initial ProjectDocs for legacy Project rows;
- provide the production model-backed continuous editing loop.

Until the installed-capability registry exists, the default HTTP composition
permits only exact capabilities inherited from a committed base/restore source
Revision. A new definition, version, owner, or parameter value fails closed;
production code does not silently accept unknown effects. New browser projects
initialize correctly, upload video/photo Assets through an immutable Revision,
derive Moments, run the durable offline Director, commit an agent-origin
first-cut Revision, and automatically render a revision-bound preview. See
`DIRECTOR_V1.md` and `RENDERING_V1.md`.
