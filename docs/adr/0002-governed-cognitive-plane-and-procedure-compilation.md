# ADR 0002: Governed cognitive plane and procedure compilation

- Status: Proposed
- Date: 2026-08-23
- Target release: 0.3.0 candidate
- Baseline: `main` at `1ea575e4887633d94fc0f3c183dc86d868fd9e15`

## Context

SSOH 0.2.0 already has a deterministic transaction coordinator, fixed proposal
routing, typed progress plans, append-only research records, protected evaluation,
and matched-budget harness campaigns. The next vertical slice needs capability-aware
cohorts, bounded peer collaboration, executable procedure compilation, guidance
evaluation, model-by-harness analysis, harness-native traces, and reward-validity
checks.

Those concepts could accidentally create a second authority path. In particular, a
peer topology, procedure compiler, model score, or reward signal could be mistaken for
permission to mutate canonical state. A new orchestration engine could also duplicate
the existing progress planner and harness-evaluation framework, weaken replay, or
persist hidden chain-of-thought.

The 0.2.0 baseline also has a narrower integrity-coverage gap: durable rule and harness
evaluation tables are not all included in workspace durable-state detection and full
projection reconstruction. Extending durable evaluation state without closing that
gap would make the new records less verifiable than the original kernel records.

## Decision

SSOH will add one governed, evidence-only cognitive plane above the existing control
plane.

1. Capability grounding, cohort selection, peer contributions, procedure compilation,
   guidance observations, model-by-harness cells, harness traces, and reward-validity
   assessments are typed proposals or deterministic derived values. None is admission
   authority.
2. `TransactionCoordinator` remains the only canonical commit boundary. New handlers
   use the fixed `ProposalRouter`, the supplied unit of work, exact idempotency, active
   policy attribution, audit append, and rollback behavior. No model, peer, tool,
   compiler, evaluator, or reward component receives repository or transaction
   authority.
3. The cognitive plane is decentralized only inside a deterministic collaboration
   envelope: a fixed peer roster, declared artifacts, bounded hops and contributions,
   stable topology updates, explicit termination reasons, and no network, subprocess,
   provider proxy, or arbitrary code execution.
4. Operational diversity is diagnostic. It remains separate from governance
   independence. `DiversityAssessment` cannot call, replace, satisfy, or weaken
   `governance.independence.are_independent()`.
5. Candidate methods compile through a pure, deterministic compiler into a typed
   `ExecutableProcedure` and static `ProcedureValidationReport`. Invalid compilations
   remain durable evidence but cannot produce a `ProgressPlan`. A valid compilation
   may be bound to the existing progress domain only through a second coordinated
   proposal that reuses the progress domain's validation and storage rules.
6. Existing `ProgressPlan`, budget, checkpoint, completion, and false-finish semantics
   remain canonical. The new compiler targets those types; it does not introduce a
   competing planner.
7. Existing `HarnessCampaign` remains the promotion-facing harness comparison. New
   guidance and model-by-harness records are evidence and diagnostics. They may support
   a later governed campaign, but cannot promote a harness, model, procedure, or reward
   rule by themselves.
8. Harness-native traces contain declared context, transformation, tool-observation,
   output, and generation metadata. Missing token IDs, log probabilities, or other
   unavailable data are represented explicitly as unavailable. Hidden
   chain-of-thought, scratch reasoning, secrets, answer-bearing protected data, and
   invented telemetry are prohibited.
9. Reward value and reward validity are different fields. Only a current, explicitly
   `valid` assessment may enter promotion-facing aggregates. `invalid`,
   `inconclusive`, missing, or stale rewards remain inspectable evidence and fail
   closed for admission use.
10. Migration 0007 will add append-only records and triggers without rewriting 0.2.0
    rows. Workspace integrity, replay, export, import, and durable-state detection will
    cover the new records and close the existing rule/harness coverage gap.

## Alternatives considered

### One aggregate cognitive-run document

A single immutable JSON document would minimize tables and transaction handlers. It
would obscure which facts were checked, make references and replay coarse, and turn
schema evolution into a large all-or-nothing contract. It is rejected.

### A live multi-agent or reinforcement-learning runtime

A runtime could dynamically recruit peers, call providers, train policies, and use
rewards to steer later work. It would add new execution and authority surfaces, violate
the offline vertical-slice boundary, and make deterministic replay impossible. It is
rejected.

### Typed modular evidence on the existing transaction spine — selected

Small strict records and pure services preserve domain boundaries while sharing one
commit protocol. This costs more explicit schemas and repositories, but it keeps
authority, replay, and failure behavior auditable.

## Consequences

The system can describe decentralized cognition without decentralizing governance.
Every durable claim about capability, diversity, collaboration, procedures,
evaluation, traces, or reward validity has a proposal, policy attribution, decision,
audit event, schema version, and content hash.

Procedure compilation becomes testable without a live model. Invalid methods can be
studied without becoming executable plans. Existing progress behavior remains the
single source of truth for execution and completion.

Model and harness effects can be compared only where task identity and budgets match.
Interaction results remain descriptive unless an explicitly designed experiment
supports a stronger claim. This is intentionally more conservative than treating one
score or reward as causal evidence.

The release gains more record types and integrity expectations. Migration, replay,
bundle compatibility, and documentation tests therefore become mandatory release
gates rather than follow-up work.

## Verification obligations

Implementation may be accepted only if tests demonstrate:

- identical valid proposals replay to identical records and hashes;
- all new writes roll back atomically with transaction and audit writes;
- peers, compilers, traces, and reward objects cannot reach repository authority;
- same-model prompt diversity never satisfies governance independence;
- collaboration terminates deterministically at every configured bound and detects
  loops, churn, and contribution monopoly;
- invalid procedures never create progress plans, while valid procedures reuse all
  existing dependency, budget, checkpoint, and completion checks;
- unmatched task or budget cells cannot produce held-constant comparisons;
- unavailable generation metadata stays unavailable through export/import;
- invalid, inconclusive, missing, and stale rewards are excluded from
  promotion-facing aggregates;
- legacy 0.2.0 workspaces upgrade without row rewrites and still verify; and
- full replay, export, import, tamper detection, packaging, and offline example gates
  cover the complete 0.3.0 record set.
