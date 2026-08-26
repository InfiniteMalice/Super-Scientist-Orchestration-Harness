# Long-horizon Execution

SSOH records long-running work as a governed, append-only progress ledger. Progress is
diagnostic state: it describes what has been attempted and independently validated, but
it never grants authority to change claims, training data, adapters, harness code,
governance, quality policy, or the final outcome of a run.

## Admission boundary

The coordinator recognizes exactly five progress proposal kinds:

1. `record_progress_plan`
2. `append_progress_event`
3. `record_run_budget`
4. `record_run_checkpoint`
5. `decide_completion`

V1 policies reject every one of these durable proposal kinds and retain the rejected
transaction and audit event. Under V2, each kind uses the fixed
`RESEARCH_PROCESS`/`RUN_LOCAL` policy requirement. Callers cannot select a different
change target or persistence scope. The active requirement must demand an independent
deterministic check, permit human judgment, and name a human approver. The proposal must
carry an approval from an actor independent of its proposer, and every embedded record
must name the exact active policy hash. Progress proposals do not carry protected-evaluation
or rollback proofs, so a requirement that enables either flag fails closed before any
progress projection.

Each handler receives a narrow read/write capability for only its required tables. It
cannot commit, roll back, write audit events, access a generic repository set, or gain
filesystem, network, subprocess, dynamic-import, or runtime execution authority. The
coordinator alone owns the database transaction and audit boundary.

## Plans and effective progress

A `ProgressPlan` is an immutable version of one research run's dependency graph. Each
subtask records its completion criteria, evidence requirements, validator identity and
version, exact positive weight, dependency identifiers, and deterministic order. Plan
versions advance by exactly one per run. Subtask identifiers cannot be reused, weights
must sum exactly to one, and unknown, duplicate, or cyclic dependencies are rejected.

Progress changes are immutable `ProgressValidationEvent` records. The current state of a
subtask is the latest event ordered by `(occurred_at, event_id)`. Provisional completion
is reported separately. Official progress includes only a current `VALIDATED` event that
has retained evidence, a passed authoritative validation result, the plan's declared
validator identity and version, recomputed actor independence, and a still-valid
dependency closure. Invalidating a prerequisite therefore removes the effective weight
of every dependent without deleting history.

The mutable progress head only accelerates reads. Events may target only the run's highest
accepted plan version. Within that plan, each event must strictly advance the head by
`(occurred_at, event_id)`; an older timestamp or a non-increasing identifier at the same
timestamp is rejected. The head is rebuilt and verified against the same rule during
workspace replay.

## Budgets, telemetry, and checkpoints

`BudgetAllocation` separates exploration, implementation, verification, recovery, and
finalization reserves and usage. Usage cannot exceed its matching reserve; one category
does not silently borrow another's authority. Typed telemetry preserves episodes, model
and tool calls, token counts, operations, files changed, elapsed and verification time,
repeated and reverted work, checkpoint count, timeout state, termination reason, and
estimated cost.

An accepted `RunCheckpoint` binds the exact run and plan, replay-derived validated
subtasks, pending dependencies, hypotheses, environment snapshot, attempted operations,
failures, remaining reserves, next action, and telemetry. Its artifacts, raw logs, and
raw transactions use canonical content-addressed references. A checkpoint summary never
replaces those raw references. Checkpoints target only the current highest plan and require
an applicable durable budget under the same governing policy. The latest allocation is
selected by `(recorded_at, budget_id)`; remaining reserves equal that allocation's reserves
minus its cumulative usage in every category, and checkpoint telemetry must match. Pending
dependency identifiers are the unique deterministic replay-derived set, not a caller hint.

## Completion and false finishes

Final success requires the run's declared final validator and version. That validator
must be independent of both the run creator and the completion proposer, including model
configuration aliases. It must produce an authoritative passed assessment under the
active policy. The following checklist is complete, unique, and ordered:

1. review the charter;
2. enumerate deliverables;
3. check every completion criterion;
4. run the final validator;
5. inspect final artifacts;
6. search for unresolved errors and warnings;
7. compare intended and actual state;
8. record remaining uncertainty.

The completion handler recalculates progress, selects the latest retained budget, and
recomputes the false-finish finding. Every completed checklist item and the final validation
must name at least one retained evidence record, and every exact identifier must resolve
before a decision can be projected. A false finish is the exact conjunction of voluntary
termination, a completion claim, failed final validation, meaningful official progress,
and unused budget. Such a proposal is rejected and no completion decision is projected.
Even 100% official progress cannot substitute for final validation or an incomplete
checklist.

Termination remains a separate typed fact: `SUCCESS`, `TIMEOUT`, `BUDGET_EXHAUSTED`,
`EARLY_EXIT`, `USER_CANCELLED`, `HARNESS_ERROR`, `ENVIRONMENT_ERROR`,
`VALIDATOR_ERROR`, `SAFETY_BLOCK`, and `UNRECOVERABLE_STATE` are never collapsed into a
generic failure label.

## Compiled procedure binding

The 0.3.0 procedure compiler consumes only explicitly declared, current, accepted
capability-profile, artifact-catalog, tool-catalog, validator-catalog, and source-snapshot
receipts. It recomputes the compilation method, procedure DAG, artifact flow, required
tools, validators, resources, termination rules, and plan mapping. A compilation with
domain status `INVALID` is accepted and retained as history, but it creates no procedure
plan.

When a valid `ProcedureCompilationRecord` is bound to progress, the binding handler
requires the exact compilation receipt, active policy, plan identifier/version,
subtasks, dependencies, weights, evidence requirements, validator identities, and
termination mapping. The handler then calls the canonical `RecordProgressPlanHandler`
inside the same coordinator transaction. Attempting to bind an `INVALID` compilation is
rejected with transaction code `INVALID_PROCEDURE`. If either validation or either
projection fails, the coordinator rolls back the binding, progress plan, transaction,
and audit together. The compiler has no direct progress-head authority.

Run `python -m pytest tests/unit/procedures tests/integration/application/test_procedure_service.py
-q` to verify compilation and atomic binding. Run
`python -m pytest tests/adversarial/test_procedure_escalation.py -q` to verify that
recursive delegation, method anchoring, procedure-input injection, forbidden imports,
shells, providers, tools, and impossible governance authority cannot create a plan or
advance a progress head.

## Durability and reconstruction

Plans, subtasks, validation events, budgets, checkpoints, and completion decisions use
the fixed append-only 0003 tables. The only mutable progress record is the rebuildable
head. Before any proposal mutation, workspace verification strictly decodes every row,
replays accepted audited transactions in order, reconstructs all progress records and
heads, recalculates checkpoint progress, dependencies, budget state, telemetry, and content
addresses, and recomputes completion evidence, authority, false-finish, and finalization
bindings. Missing, extra, corrupt, reparented, rewound, dangling, semantically forged, or
cross-plan state fails closed before a replay or new proposal can mutate the workspace.

Exact idempotent replay returns the original decision with `replayed=true` and creates no
additional authoritative record, transaction, or audit event. Unexpected projection or
storage failures propagate and roll back the complete relational transaction rather than
being mislabeled as user input errors.
