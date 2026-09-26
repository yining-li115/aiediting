# AI Coding Rules

These constraints exist to prevent locally plausible code from turning the
repository into a collection of overlapping abstractions.

## Before writing code

An AI coding agent must state:

1. which user-visible behavior is being completed;
2. which authoritative contract owns the state;
3. which existing module is extended or replaced;
4. which tests prove completion;
5. whether the change introduces a dependency, migration, or compatibility
   boundary.

If those answers are unclear, inspect the repository before adding files.

## Structure rules

- One module has one reason to change.
- Model-driven editing workflows belong behind `VideoAgentHarness`. The Harness
  may build frozen context, invoke providers, run workflow-local review, and
  apply bounded recovery; it must not persist ProjectDoc, commit Patches,
  execute renders, issue SQL, or contain HTTP behavior.
- Application services persist Harness inputs and outputs and invoke the
  existing ProjectService and Job paths. Do not let a new Agent workflow create
  a parallel project state or write path.
- Continuous-edit model access must go through the closed Tool Registry and its
  hard step limit. A provider may choose tools, but prompts may not add tools,
  raise limits, execute a Patch, or bypass persisted AgentTurnContext/tool
  traces.
- Prefer a named domain module over `utils.py`, `helpers.py`, `common.py`, or a
  generic `manager.py`.
- Do not duplicate Pydantic models as handwritten TypeScript interfaces.
- Do not represent the same domain object as several incompatible dictionaries.
- Do not pass `dict[str, Any]` across domain or application boundaries when a
  stable schema is known.
- Do not add a class whose only purpose is to forward calls unchanged.
- Do not introduce a base class until at least two real implementations share
  behavior. Use a small Protocol for ports.
- Do not split a short cohesive implementation into many one-function files.
- Treat 400 lines per module and 60 lines per function as review signals, not
  automatic reasons to fragment code. Split by responsibility, not line count.

## Behavior rules

- A UI control must either work end to end or be visibly disabled and labeled.
- A route declared by the frontend must exist in the backend before the
  feature is called complete.
- A mock passing does not prove a vendor or media integration works.
- Never silently clamp, drop, or reinterpret a user instruction, asset,
  reference requirement, lock, or unsupported effect.
- Keep IDs stable. Never address timeline elements by mutable array position.
- Long operations return a Job. They do not hold an HTTP request open.
- Persist the frozen Agent context before its first provider call and each tool
  result before the next call. Recovery must not silently substitute a newer
  Revision, Moment set, conversation history, or workspace selection.
- Every artifact records the project revision and implementation/configuration
  version that produced it.
- Autonomous Agent candidates that require rendered-output review must remain
  non-current until a revision-bound ReviewReport is accepted. A failed or
  rejected review must not be converted into a commit by fallback behavior.
- Reviewer-driven AutoFix must create a fresh typed Patch and immutable
  candidate from the still-current committed base. Never mutate, relabel, or
  temporarily commit the rejected candidate, and enforce the attempt limit in
  code rather than in a prompt.

## Scope rules

- Implement only the current vertical slice and the smallest supporting
  contract needed by the approved architecture.
- Do not pre-build marketplaces, generalized plugin systems, extra providers,
  abstract workflow engines, or speculative extension points.
- Do not add fallback behavior unless the product behavior is defined and
  tested.
- Do not keep old and new implementations active indefinitely. During a
  migration, name the compatibility adapter and define its removal condition.

## Database and migration rules

- Repositories are the only modules that issue domain persistence queries.
- Transactions must not call models, render media, or download files.
- Store immutable revision snapshots; operations are audit/explanation data,
  not the only reconstruction source.
- Use foreign keys and uniqueness constraints for project scope and
  idempotency where the database can enforce them.
- Never edit or delete a committed migration. Add a migration and a test.

## Test rules

- Every defect fix begins with a failing regression test when reproducible.
- Every Patch operation needs success, invalid-range, locked-target, and stale
  base-revision coverage where applicable.
- Tests must use isolated databases, storage, and provider fakes.
- Do not make live paid model calls from the default test suite.
- A vertical slice is accepted only after a real API-to-service-to-adapter path
  is exercised; UI-only and unit-only completion are insufficient.

## Change review checklist

Before declaring completion, verify:

- Is there now more than one source of truth?
- Did business logic leak into a route, React component, worker, or adapter?
- Was an existing module bypassed instead of extended or deliberately replaced?
- Are errors typed and user-actionable?
- Is revision identity carried through jobs, previews, and exports?
- Are new files discoverable from the target repository layout?
- Were unrelated files changed?
- Do comments explain decisions rather than restate the code?
