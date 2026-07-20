# Governed Adaptation and Harness Evolution Design

**Status:** Approved for implementation planning on 2026-07-18
**Date:** 2026-07-18
**Target release:** 0.2.0 candidate
**Baseline:** `main` at `a4e34938298f1abb206f9be378fedd847f9e9613`
**Branch:** `feat/governed-adaptation-and-harness-evolution`

## 1. Purpose

This design extends the existing epistemic kernel with a governed vertical slice for
bounded adaptation, long-horizon progress measurement, natural evidence trails,
behavioral-rule consolidation, behavior-to-code navigation, typed
hypothesis-model-checker loops, evaluator succession, and fair harness-evolution
evaluation.

The release remains a transactional scientific-research kernel. It does not become a
generic workflow engine, an autonomous super-agent, a training framework, a truth
oracle, or an unrestricted self-modifying system.

The governing rule remains:

> Models, tools, and humans may propose. The harness owns committed evidence, claim
> state, validation, provenance, governance policy, audit history, and rollback
> boundaries.

The additional rule for adaptation is:

> The harness may improve outputs, procedures, memories, skills, adapters, and selected
> scaffold components only while the standards used to evaluate and authorize those
> improvements remain externally anchored, versioned, auditable, independently
> governed, and reversible.

## 2. Observed Baseline

The current package is version 0.1.0 and requires Python 3.12 or later. It uses strict
Pydantic models, SQLAlchemy, SQLite, Alembic, Typer 0.19.2, and Click 8.3.3. Strict mypy
and branch-aware coverage of at least 90 percent are fixed project requirements.

The implemented transaction flow is:

```text
typed attempt or untrusted direct proposal
-> BEGIN IMMEDIATE
-> workspace-integrity verification
-> exact idempotency lookup
-> strict proposal normalization
-> active-policy attribution
-> deterministic admission
-> accepted projection mutation, when any
-> transaction decision append
-> audit event append
-> atomic commit or rollback
```

The v0.1.0 database has one released migration, `0001_epistemic_kernel`. Its
authoritative append-only tables are governance policies, evidence records, claim
versions, transactions, and audit events. Governance state and claim heads are mutable
effective-state projections. Evidence bytes are stored outside SQLite in the existing
content-addressed artifact store.

The clean baseline test result in the isolated worktree is 429 passed and 3 skipped on
Python 3.12.13.

## 3. Goals

The 0.2.0 candidate will provide:

1. Typed classification and governance measurement for persistent adaptive changes.
2. Explicit separation of model configuration, operational scaffold, and transient
   execution state.
3. Versioned research runs, hierarchical progress, budgets, checkpoints, and
   false-finish detection.
4. Source-first evidence trails connected to atomic claims and exact source spans.
5. Versioned behavioral rules with incident retention, contradiction boundaries,
   redundancy repair, independent reviewer records, and a constrained integrator.
6. Quarantined representational primitives with old-frame and new-frame evaluation.
7. Domain-neutral hypotheses, model metadata, deterministic toy simulation, checker
   results, revision traces, and governed admission.
8. A deterministic behavior handbook based on manifests, Python AST facts, source
   hashes, and stale-location detection.
9. Matched-budget harness-evolution campaigns with discovery, validation, transfer,
   regression, and safety partitions.
10. Reversible evaluator succession and multidimensional collapse monitoring.
11. An offline deterministic end-to-end example and a stable grouped CLI.
12. Accurate source attribution for S21 through S29 without reproduction claims.

## 4. Non-goals

This release will not add:

- arbitrary shell or imported-code execution;
- network-dependent research flows;
- a live model provider requirement;
- a GPU requirement or live adapter training;
- automatic model downloads;
- automatic adapter, evaluator, quality-policy, or governance promotion;
- live laboratory control;
- full benchmark or paper reproductions;
- a graphical interface, distributed deployment, Kubernetes, or a workflow DSL;
- reinforcement learning;
- any implementation, adapter, fixture, example, schema, dependency, prompt, command,
  experiment, or roadmap item specific to the benchmark discussed by S29.

S29 may be named only in source attribution and architectural provenance. SSOH imports
only a domain-neutral scientific-control pattern.

## 5. Source Provenance

S01 through S20 remain the existing source register. S21 through S29 will be added with
the exact versions consulted on 2026-07-18:

| ID | Version and repository boundary | Project use | Limitation |
| --- | --- | --- | --- |
| S21 | arXiv:2607.07663v1, CC BY 4.0 | bounded adaptation, loop closure, grounding, verification hierarchy, collapse, governance measurement | survey taxonomy and observations are not universal formal laws |
| S22 | arXiv:2607.13104v1; companion repository `selfimproving-agent/Awesome-Self-Improving-Agents@06a48f9beddeb0ff711a3f63be857e3e95709923`, MIT | model/scaffold/state separation, update targets, signals, trajectories | survey classifications do not establish safe autonomous promotion |
| S23 | arXiv:2607.08964v2, CC BY 4.0 | dense progress, long-horizon telemetry, partial diagnostics | partial reward is not final correctness |
| S24 | arXiv:2607.09328v1, arXiv non-exclusive distribution license | source-first natural evidence trails and separated evaluation stages | a coherent trail is not a proof |
| S25 | arXiv:2607.09560v1, arXiv non-exclusive distribution license | vocabulary and verifier gaps, experimental primitives | conceptual position paper, not an established architecture |
| S26 | arXiv:2607.12227v1, CC BY 4.0; code repository `rethinking-harness-evolution/code@ffd1ba1c2c3e31099264f630b9ed44aec63a86a7`, no license file found | matched feedback and inference budgets, held-out transfer, causal attribution | repository code is not imported; unlicensed code is unavailable for reuse |
| S27 | arXiv:2607.13091v1, arXiv non-exclusive distribution license | persistent rules, pre-submission review, retained feedback | evidence is limited; SSOH conflict, redundancy, and reviewer design is original synthesis |
| S28 | arXiv:2607.13285v1, CC BY 4.0 | behavior-centric representation, progressive disclosure, localization | SSOH does not reproduce its LLM-assisted generation system |
| S29 | official GitHub Pages repository at `d907a3c18ac97fe6bf7b0bbe43ba938acb023b72`; no license file | inspiration for a generic hypothesis-model-checker-revision-admission pattern | not peer reviewed, public-set claims are self-reported, no source code or task assumptions are imported |

For each entry, the register will separately identify source proposal, evidence,
limitation, project adaptation, and project-original synthesis. Every S21-S29 entry will
be marked `adapted`, `inspired`, or `deferred`, never `reproduced`.

## 6. Alternatives Considered

### 6.1 Central expansion of the existing admission engine

Every new proposal could be added directly to the existing proposal union and handled
by branches in one `AdmissionEngine`. This gives a single obvious commitment path, but
it would turn the engine and `KernelService` into the giant service explicitly rejected
by the requirements. Every domain change would increase central coupling and the risk
to exact v0.1.0 behavior.

### 6.2 Transactional modular domains — selected

One transaction coordinator will retain idempotency, policy attribution, audit append,
and atomic commit. A fixed, source-controlled router will dispatch exact typed proposal
variants to focused domain admission handlers and projectors. There is no runtime
plugin discovery and no generic workflow language.

The existing `KernelService` remains the public compatibility facade. Existing
evidence and claim behavior remains characterized by its current tests. Transaction
coordination may be extracted behind that facade only after golden tests capture exact
replay, rejection, redaction, and audit semantics. The existing `AdmissionEngine`
becomes or delegates to the evidence-and-claim handler; new domains do not add branches
to it.

### 6.3 Independent domain ledgers

Each domain could own an independent transaction stream and only reference the audit
chain. That would reduce local coupling but fragment idempotency, governance
attribution, and rollback. Cross-domain admission would require compensation or
distributed reconciliation. This is unnecessary for the local SQLite architecture and
would weaken atomicity.

## 7. Selected Architecture

The release is a transactional modular monolith:

```text
CLI and deterministic examples
    -> domain application services
        -> shared transactional admission coordinator
            -> fixed proposal router
                -> domain admission handler
                -> domain projector
            -> existing idempotency, governance, audit, and integrity boundaries
        -> repository interfaces
            -> SQLite authoritative records and projections
            -> content-addressed external artifacts

source-controlled handbook manifest
    -> Python AST fact extraction
    -> deterministic verification
    -> generated handbook projections
```

Each domain unit must answer three questions without reading its internals:

1. Which typed proposals does it accept?
2. Which deterministic and independently supplied checks govern admission?
3. Which authoritative records and projections can it write in the shared transaction?

### 7.1 Package boundaries

Final file names follow existing repository conventions, but the intended cohesive
boundaries are:

```text
src/super_scientist/
    domain/
        improvement/
        research_runs/
        progress/
        evidence_trails/
        behavioral_rules/
        representations/
        hypotheses/
        evaluators/
        handbook/
        harness_evaluation/
    kernel/
        admission/
        governance/
        review/
        evaluator_succession/
    application/
        transactions/
        improvement/
        research_runs/
        progress/
        trails/
        rules/
        representations/
        hypothesis_testing/
        handbook/
        harness_evaluation/
    evaluation/
        long_horizon/
        trail_quality/
        harness_transfer/
        collapse/
    providers/
        storage/
        reviewers/
        simulation/
        training/
    cli/
```

This is a boundary map, not authorization for an empty directory scaffold. A package is
created only with its first cohesive behavior. Generic dumping grounds such as
`manager.py`, `utils.py`, `helpers.py`, and `common.py` are prohibited unless a later
review demonstrates a narrow, nameable responsibility.

## 8. Shared Transaction Contract

### 8.1 Proposal envelope

Every durable mutation uses a strict discriminated proposal record containing:

- proposal type and schema version;
- proposal identifier and idempotency key;
- proposer identity;
- optional typed approval;
- the complete intended domain record or transition;
- stable input provenance where applicable.

The proposal union remains closed and source controlled. Adding a new proposal kind is
a reviewed code and schema change. Unknown kinds and fields fail strict parsing.

### 8.2 Fixed handler interface

Each registered domain handler provides focused operations equivalent to:

```text
load_context(domain_read_capability, proposal) -> immutable typed context
decide(proposal, context) -> TransactionDecision
project(accepted_proposal, domain_write_capability) -> None
```

`decide` is deterministic and side-effect free. `project` is reachable only after an
accepted decision and writes through trusted repository methods in the current unit of
work. A handler cannot append its own transaction or audit event. The coordinator never
passes the wholesale `RepositorySet` to a new domain handler. It constructs a narrow,
typed read/write capability containing only that handler's required repositories. The
existing evidence-and-claim compatibility handler may retain its current repository
view behind the `KernelService` facade while it is characterized and narrowed.

### 8.3 Coordinator invariants

The coordinator preserves the current ordering:

1. Verify the complete workspace before mutation and exact replay.
2. Resolve trusted intent replay before invoking a proposal factory.
3. Strictly normalize proposals before hashing or dispatch.
4. Require an active registered policy matching the configured snapshot.
5. Reject self-approval and prohibited authority combinations.
6. Run the focused handler.
7. Apply accepted projections.
8. Append one transaction decision.
9. Append one audit event naming the governing policy.
10. Commit or roll back all relational changes together.

Prepared content-addressed files may remain after a rolled-back relational operation,
matching the existing harmless-orphan boundary. They never become authoritative without
an admitted metadata record.

### 8.4 Backward compatibility

Existing v0.1.0 proposal JSON, transaction rows, audit events, policy rows, CLI
envelopes, replay behavior, rejection codes, and evidence artifacts must continue to
decode and verify exactly. The audit envelope can remain schema version 1 while its
typed proposal union gains backward-compatible variants; any change to audit-envelope
fields requires a new audit schema version and explicit mixed-version verification.

### 8.5 Governance-policy evolution

`GovernancePolicyV1` is retained as an immutable model with exactly the existing fields,
validation, canonical serialization, and schema-version-1 hash procedure. It is never
upcast by adding defaulted v2 fields. `GovernancePolicyV2` is a separate strict model
with schema version 2, the v1 claim-check and human-approval fields, and a tuple of
strict `AdaptationRequirement` records. Each requirement names target and persistence,
minimum verification category, permitted grounding categories, required approver kind,
protected-evaluation requirement, and rollback requirement. `PolicyDocument` is a
strict discriminated union on `schema_version`.

Each policy version is hashed from the canonical JSON of its exact versioned model.
Workspace verification selects the decoder from the stored schema version, recomputes
the version-specific hash, and supports mixed immutable v1/v2 history without changing
historical v1 hashes.

Existing schema-version-1 policies remain valid and continue to govern v0.1 proposal
kinds. The default `init` behavior remains schema version 1 for exact compatibility.
Neither new nor migrated workspaces silently replace their active policy. An operator
must explicitly propose a versioned candidate policy containing persistence-aware
adaptation requirements before those persistent operations are enabled.

Phase A adds an explicit `ProposeGovernancePolicyTransition` handled under the prior
active policy. It requires a human proposer or approver according to the prior policy,
independent approval, a non-closed loop classification, a complete measurement record,
the prior and candidate hashes, compatibility validation, and a rollback target. An
accepted transition appends the candidate policy snapshot and changes the active-policy
projection atomically; its audit event is attributed to the prior governing policy and
records both hashes.

For bootstrap safety, the proposal is classified as the existing `governance_change`
kind. A non-configurable constitutional admission rule requires an independent human
approval even if a custom v1 policy omitted that kind from `human_approval_for`. The
candidate v2 policy therefore cannot authorize its own transition. Rollback is another
governed transition under the then-active policy, never an automatic pointer change.

Until a migrated workspace adopts a policy capable of governing a new persistent
operation, that operation fails closed. Policy transition can add requirements but
cannot remove source-controlled constitutional prohibitions, weaken the quality gate,
authorize self-promotion, or reinterpret prior decisions. Exact v0.1 `init` behavior
remains covered by compatibility tests.

## 9. Record Authority Classification

| Class | Records | Mutation rule |
| --- | --- | --- |
| Authoritative append-only | proposals and decisions; research-run definitions and events; progress-plan versions and validation events; checkpoints; evidence-trail versions, nodes, edges, and checker results; rule incidents, rule versions, reviewer assessments, consolidation decisions, regression cases, and behavior-rule link versions; primitive versions and evaluations; hypothesis versions, model specs, checker specs/results, revisions, and admission decisions; evaluator versions and succession decisions; harness campaigns, budgets, split manifests, run observations, metrics, confounds, and decisions; self-improvement measurements | insert only; update/delete rejected by triggers |
| Mutable projections | active governance pointer; claim heads; run heads; current progress status; rule heads; primitive heads; hypothesis heads; active evaluator pointer; campaign summary indexes | rebuildable from authoritative records; verified against history |
| External artifacts | source bytes; raw logs; large telemetry; evaluator reports; simulator outputs; dataset manifests; generated packages | content addressed; relational records store hashes, sizes, media type, and provenance |
| Derived indexes and reports | progress summaries; rule-bloat metrics; handbook output; self-improvement reports; harness-evaluation reports; documentation | never admission authority; reproducible from authoritative records and source |

## 10. Research Run Foundation

The deterministic example and progress ledger require an explicit `ResearchRun`
identity even though the candidate CLI list omitted it.

An immutable run definition contains:

- run identifier, charter, scope, creator, creation time;
- active governance-policy hash;
- declared model and scaffold configuration versions, if any;
- budget allocation;
- final validator identity and version;
- environment snapshot identifier;
- schema version.

Lifecycle changes are append-only `ResearchRunEvent` records. `ResearchRunHead` is a
verified projection. A run cannot report success without an accepted final-validation
event.

## 11. Self-improvement Classification and Measurement

### 11.1 Classification

Every adaptive operation carries typed values for:

- **Change target:** `OUTPUT`, `SESSION_STATE`, `PERSISTENT_MEMORY`,
  `BEHAVIORAL_RULE`, `SKILL`, `PROMPT`, `TOOL_ROUTING`, `TOOL_DEFINITION`,
  `ORCHESTRATION`, `MODEL_ADAPTER`, `MODEL_WEIGHTS`, `EVALUATOR`,
  `RESEARCH_PROCESS`, `GOVERNANCE_POLICY`.
- **Loop closure:** `HUMAN_IN_LOOP`, `HUMAN_ON_LOOP`, `CLOSED_LOOP`.
- **Persistence:** `EPHEMERAL_OUTPUT`, `RUN_LOCAL`, `SESSION_LOCAL`,
  `CROSS_RUN_MEMORY`, `PERSISTENT_RULE`, `PERSISTENT_SKILL`, `MODEL_ADAPTER`,
  `HARNESS_CODE`, `EVALUATOR_POLICY`, `GOVERNANCE_POLICY`.
- **Verification level:** `FORMAL_VERIFIER`, `EXECUTION_FEEDBACK`,
  `EXTERNAL_EMPIRICAL_MEASUREMENT`, `INDEPENDENT_DETERMINISTIC_CHECK`,
  `INDEPENDENT_LEARNED_JUDGE`, `RUBRIC_JUDGE`, `CROSS_MODEL_AGREEMENT`,
  `SELF_CRITIQUE`, `SELF_CONSISTENCY`, `MODEL_CONFIDENCE`, `MODEL_LIKELIHOOD`.
- **External grounding:** `HUMAN_JUDGMENT`, `PRIMARY_SOURCE`,
  `REAL_WORLD_OBSERVATION`, `CONTROLLED_EXPERIMENT`, `FORMAL_SYSTEM`,
  `INDEPENDENT_TEST_SUITE`, `EXTERNAL_BENCHMARK`, `INDEPENDENT_MODEL`,
  `PHYSICAL_CONSTRAINT`, `NONE`.
- **Improvement signal:** `INTRINSIC_GENERATIVE_DEMONSTRATION`,
  `INTRINSIC_EVALUATIVE_FEEDBACK`, `EXTRINSIC_GROUNDED_EXPERIENCE`,
  `EXTRINSIC_SIMULATED_EXPERIENCE`, `HUMAN_CORRECTION`, `FORMAL_VERIFICATION`,
  `EXECUTION_FEEDBACK`, `EMPIRICAL_MEASUREMENT`.

There is no generic `verified` Boolean. Formal mechanisms are named verifiers,
deterministic incomplete mechanisms are checkers, and learned or prompted mechanisms
are judges.

### 11.2 Persistence-aware policy

Required review increases monotonically with persistence. At minimum:

- Ephemeral and run-local changes may be self-critiqued but cannot become durable
  authority.
- Cross-run memory and persistent rules require non-`NONE` grounding and independent
  admission.
- Adapter, harness-code, evaluator-policy, and governance-policy changes require
  protected evaluation, rollback metadata, and human authority.
- Closed-loop governance, quality-policy, evaluator promotion, or adapter promotion is
  prohibited.

### 11.3 Measurement record

`SelfImprovementMeasurementRecord` is authoritative and append only. It records the
stable `change_id`, change classification, proposer, evaluator and tier, grounding,
baseline and candidate versions, protected metrics, countermetrics, full performance
trajectory, separate execution/search/evaluation/judging/human budgets, costs, compute,
tokens, elapsed time, tool use, human interventions, failures, regressions, rollback
target, `evaluator_audit_id`, decision, and decision authority.

It is required for persistent memory, rules, skills, harness code, evaluator changes,
adapter candidates, research-process changes, quality-policy proposals, and governance
proposals.

Every performance trajectory explicitly retains `m_0` through `m_T`, peak and final
metrics, attempted, admitted, and rejected changes, regressions, rollback events, and
the separate resources consumed at each step. Best-only and final-only summaries are
insufficient.

An append-only `EvaluatorAuditRecord` answers who audited the evaluator. It records the
auditor actor identity, version, and category; evaluator identity and version; auditor
relationship to both proposer and evaluator; the enforced independence result; evidence
inspected; checks run; assumptions and limitations; result; timestamp; and governing
policy hash. Durable promotion requires a passed independent evaluator audit whose
identifier is bound into the measurement record. An evaluator cannot audit itself, and
neither the change proposer nor the candidate-producing actor can serve as its auditor.

### 11.4 Universal assessment provenance

Every verifier, checker, or judge result records identity, version, category,
deterministic-or-learned status, relationship to the proposer, assumptions, evidence
inspected, checks run, known coverage limitations, result, meaningful confidence,
timestamp, and the governing policy hash. A learned judge can never claim the formal
verifier category.

### 11.5 Prohibited adaptive operations

The admission layer has explicit fail-closed policy findings, each with a negative test,
for:

- adapter self-promotion;
- rule-proposer self-approval;
- a harness optimizer altering its evaluation results;
- an evaluator altering its threshold during evaluation;
- automatic replacement of an active evaluator by its candidate successor;
- candidate or training access to protected holdout answers;
- deletion or omission of failed experiments to improve a report;
- a model declaring itself independently verified;
- closed-loop governance-policy change;
- closed-loop weakening, skipping, or replacement of the quality gate;
- direct canonical-rule-registry edits outside an admitted integrator proposal;
- imported benchmark-specific implementation or hidden domain assumptions;
- partial progress committed as final success;
- a summary substituted for raw evidence or required raw references; and
- confidence, likelihood, self-consistency, or textual agreement treated as evidence.

## 12. Model, Scaffold, and Execution-state Separation

The conceptual agent configuration is recorded as `A_t = (theta_t, Sigma_t)`:

- `theta_t` identifies the foundation model and active adapter configuration.
- `Sigma_t = (p_t, m_t, T_t, g_t)` identifies prompts, memory, tools, and control.
- `X_t` is transient execution state and is not persistent improvement.

Typed contracts include `AgentConfiguration`, `FoundationModelConfiguration`,
`ScaffoldConfiguration`, `PromptConfiguration`, `MemoryConfiguration`,
`ToolConfiguration`, `ControlConfiguration`, `ExecutionState`,
`ConfigurationVersion`, and `ConfigurationDiff`.

Only configuration metadata enters the kernel. No live model SDK is introduced.

Fast-loop scaffold candidates are reversible, branch isolated, and never automatically
promoted. Slow-loop adapter support is metadata-only in this release: dataset lineage,
candidate metadata, evaluation, promotion decision, rollback record, and a deterministic
fake trainer protocol. Torch, Transformers, PEFT, and GPU dependencies do not enter the
core installation.

## 13. Long-horizon Progress and Checkpoints

### 13.1 Progress plan

A versioned `ProgressPlan` belongs to one research run. Its immutable subtask records
contain identifier, description, dependency identifiers, completion criteria, validator
identity/version, weight, evidence requirements, and ordering metadata.

Status changes are append-only events using:

- `NOT_STARTED`
- `IN_PROGRESS`
- `BLOCKED`
- `PROVISIONALLY_COMPLETE`
- `VALIDATED`
- `INVALIDATED`
- `ABANDONED`

Only currently `VALIDATED` subtasks whose dependencies remain valid contribute to
official progress. Provisional progress is reported separately. Invalidating a
dependency deterministically invalidates its dependent contribution without deleting
history.

Every validation event binds the completion-proposing actor; validator actor identity,
version, and category; the validator's relationship to the run creator and completion
proposer; evidence inspected; checks run; assumptions and limitations; result;
timestamp; governing policy hash; and a deterministically enforced `are_independent`
decision. A subtask reaches `VALIDATED` only after an accepted independent validation
event. The final-goal validator must be independent of both the research-run creator and
the completion proposer. Self-validation and same-authority aliases are rejected rather
than merely reported.

Dense, provisional, or official progress is diagnostic state, never admission
authority. No progress value by itself may commit an atomic claim, promote training
data, admit an adapter, alter a harness, change governance or quality policy, or record
final success. Those actions must pass their own typed proposal, evidence, review, and
authority checks.

Final-goal validation, subtask progress, execution health, budget use, stopping
decision, and harness failure are independent dimensions.

### 13.2 Telemetry and budgets

Typed telemetry records episodes, model calls, token classes, tool calls, operations,
files changed, elapsed and verification time, repeated and reverted actions,
checkpoints, timeout state, termination reason, and estimated cost.

Budgets are separated into exploration, implementation, verification, recovery, and
finalization reserves. Evaluation records additionally separate execution, improvement
search, judging, and human-review budgets.

### 13.3 Checkpoints

An append-only checkpoint references validated progress, plan version, pending
dependencies, current hypotheses, artifact hashes, environment snapshot, attempted
operations, failures, remaining budget, next recommended action, and content-addressed
raw logs/transactions. A summary never replaces those references.

### 13.4 False finishes

A completion proposal is rejected as a candidate false finish when voluntary
termination and a claim of completion coexist with a failed final validator, meaningful
validated progress, and unused budget. The decision records charter review, deliverable
enumeration, criteria checks, validator result, artifact inspection, unresolved errors,
intended-versus-actual comparison, and uncertainty.

The completion service enforces the ordered checklist: re-read the charter, enumerate
deliverables, check every completion criterion, run the final validator, inspect final
artifacts, search unresolved errors and warnings, compare actual and intended state,
record unresolved uncertainty, and only then submit a completion proposal.

Termination reasons are `SUCCESS`, `TIMEOUT`, `BUDGET_EXHAUSTED`, `EARLY_EXIT`,
`USER_CANCELLED`, `HARNESS_ERROR`, `ENVIRONMENT_ERROR`, `VALIDATOR_ERROR`,
`SAFETY_BLOCK`, and `UNRECOVERABLE_STATE`. Harness, environment, and validator failures
are never relabeled as reasoning failures.

## 14. Natural Evidence Trails

### 14.1 Versioned trail

An `EvidenceTrail` is a versioned append-only graph tied to an atomic claim. A new edit
creates a new trail version; nodes and relations in prior versions remain immutable.

The trail version records source identifiers, required/supporting/opposing/redundant
node sets, ordering constraints, trail geometry, status, construction method, and every
checker or independent assessment identifier.

Each node records source and evidence identifiers, exact span, structural location,
content hash, trail role, temporal and causal position, confidence, and necessity
status. Structural locations support section, subsection, page, paragraph, table,
figure, footnote, timestamp, speaker, event sequence, appendix, and reference target.

Initial relation types are `SUPPORTS`, `CONTRADICTS`, `PRECEDES`, `FOLLOWS`,
`CAUSES_CANDIDATE`, `ENABLES`, `PREVENTS`, `QUALIFIES`, `EXPLAINS`, `SAME_ENTITY`,
`SAME_EVENT`, `DEPENDS_ON`, and `ALTERNATIVE_EXPLANATION`.

Temporal order and co-occurrence never authorize a causal assertion.

### 14.2 Source-first process

The application flow is:

```text
ingest immutable source
-> preserve source structure
-> propose evidence nodes
-> propose relations
-> formulate candidate claim or question
-> check clue necessity
-> check answer groundedness
-> search counterevidence
-> admit, conflict, abstain, or reject
```

Conclusion-first retrieval of agreeable passages is not a supported application path.

### 14.3 Validation

Deterministic checks cover source existence, exact-span fidelity, content hash,
structural bounds, ordering, relation schema, scope, temporal order, and modality.
Typed independent assessments cover clue necessity, groundedness, relation fidelity,
counterevidence, causal-overclaim risk, rubric fidelity, contamination, and
answerability.

Trail outcomes are `SUFFICIENT`, `PARTIALLY_SUPPORTING`, `CONFLICTED`, `INSUFFICIENT`,
`UNANSWERABLE`, and `INVALID_TRAIL`.

Every generated report sentence is a derived record linked to its atomic claim, exact
trail version, source spans, contradictions, uncertainty, and modality.

## 15. Behavioral-rule Governance

### 15.1 Authoritative rule history

Concrete incidents are immutable authoritative records. A rule version references one
or more accepted human reviews, verified failures, reproduced bugs, failed scientific
workflows, security incidents, quality-gate failures, repeated mistakes, or validated
counterexamples. Speculative model advice alone cannot create a durable rule.

Rule versions contain identifier, semantic version, title, canonical statement,
rationale, authority, scope, triggers, required and prohibited behavior, exceptions,
decision boundary, precedence, source incidents, evidence, counterexamples, regression
tests, retrieval terms, aliases, related rules, conflicts, supersession, status,
creator, approver, and timestamps.

Statuses are `PROPOSED`, `UNDER_REVIEW`, `ACTIVE`, `EXPERIMENTAL`, `QUARANTINED`,
`SUPERSEDED`, `DEPRECATED`, and `REJECTED`. Authority levels are `CONSTITUTIONAL`,
`GOVERNANCE`, `PROJECT`, `DOMAIN`, `COMPONENT`, `TASK`, and `RUN_LOCAL`.

### 15.2 Contradictions

The project adopts:

> Treat a contradiction as evidence of a scope error, invalid rule, missing exception,
> obsolete assumption, or competing failure modes. Resolve it into a canonical decision
> rule with an explicit boundary.

Contradiction assessments classify true logical contradiction, overlapping scope,
missing precondition or exception, precedence, temporal version, environment or model
dependence, competing failure modes, invalid or outdated rules, and measurement
conflict.

Resolution must preserve both motivating incidents, identify the variable separating
conditions, create an explicit decision boundary, attach tests for both failures and
the boundary, and supersede rather than delete history. Newest-rule wins, majority
vote, arbitrary priority, and ungrounded model intuition are invalid resolution methods.

### 15.3 Redundancy

The project adopts:

> Treat recurrence under an existing rule as evidence that the rule's abstraction,
> clarity, trigger, retrieval, enforcement, or scope is inadequate. Revise the canonical
> rule and preserve all incidents as regression cases.

Overlap classifications include exact duplicate, semantic duplicate, narrower
instance, broader reformulation, partial overlap, same-trigger/different-action,
different-trigger/same-action, and non-redundant.

Exact duplicates cannot create another active rule. Semantic duplicates enter review.
Potential remedies are wording clarification, scope expansion or narrowing, trigger or
retrieval repair, enforcement, exception, merge, split, or no change. Active count,
length, overlap, retrieval precision/recall, trigger errors, conflicts, supersession
depth, unused rules, and recurrence remain separate metrics.

### 15.4 Reviewer records and integrator

Semantic, conflict, abstraction, adversarial, and verification reviewers submit strict
append-only `ReviewerAssessment` records. A registry integrator receives all assessment
identifiers and creates one candidate canonical diff. It preserves disagreement and
explains accepted and rejected recommendations.

Reviewer records include role, model/adapter identity where relevant, proposal, rules
and incidents examined, classification, findings, candidate wording, scope, triggers,
exceptions, conflicts, redundancies, counterexamples, tests, confidence, uncertainty,
and recommended action.

Reviewers and deterministic fake reviewers have no repository interface that can write
rule heads, governance, the quality registry, protected tests, evaluation thresholds,
or promotion state. They may only return or import typed assessment proposals.

Actions are `ACCEPT`, `ACCEPT_WITH_REVISION`, `MERGE_WITH_EXISTING`, `SPLIT`,
`QUARANTINE`, `REJECT`, and `ESCALATE_TO_HUMAN`. Correlated unanimity is not independent
verification.

## 16. Representational Primitive Registry

The quarantined primitive registry distinguishes `INTRA_SPACE_TRANSFORMATION` from
`GENERATIVE_REPRESENTATION_PROPOSAL`.

Primitive versions contain stable identity, semantic version, definition, motivation,
parent vocabulary, contrasts, examples and counterexamples, construction method,
expected uses, dependencies, measurements, falsification tests, ambiguity, proposer,
and status.

Statuses are `PROPOSED`, `DUPLICATE_SUSPECTED`, `UNDER_DEFINITION`, `EXPERIMENTAL`,
`LOCALLY_USEFUL`, `REPLICATED`, `STABILIZED`, `REJECTED`, `SUPERSEDED`, and `RETIRED`.

Old-frame evaluation checks preserved constraints, established tests, and regressions.
New-frame evaluation checks new predictions, independent operationalization,
non-circular test construction, and later reuse. A primitive and an evaluator created
for it cannot approve one another. Experimental primitives cannot enter canonical
claim schemas, governance, active evaluators, adapter data, or public conclusions.

Semantic versions use major for incompatible meaning, minor for compatible expanded
operationalization, and patch for clarification. Claims using an experimental primitive
are marked `EXPERIMENTAL_VOCABULARY`.

## 17. Hypothesis, Model, Checker, and Revision Loop

### 17.1 Domain-neutral contracts

The generic control pattern is:

```text
typed hypothesis
-> executable-model metadata or allowlisted deterministic simulator
-> checker
-> explicit revision trace
-> governed admission
```

Contracts include `HypothesisSpec`, `ExecutableModelSpec`, `ModelInput`, `ModelOutput`,
`SimulationResult`, `VerificationMechanismSpec`, `VerificationResult`,
`CounterexampleRecord`, `RevisionRecord`, and `AdmissionDecision`.

`VerificationMechanismSpec` and `VerificationResult` are strict discriminated unions of
formal verifier, deterministic checker, and learned or rubric judge records. This
provides records equivalent to `VerifierSpec` and `VerifierResult` without falsely
labeling every mechanism a formal verifier.

They declare schemas rather than embedding assumptions about any benchmark or
scientific domain.

`ExecutableModelSpec.model_type` may describe source-controlled code metadata, symbolic
equations, a probabilistic model, causal graph, state-transition system, deterministic
simulator, formal specification, or another separately approved domain model. Model
type describes the artifact; it does not grant execution authority.

### 17.2 Safe execution boundary

Model records cannot supply import paths, entry points, source text to execute,
subprocess arguments, shell commands, or network locations. Execution mode is either:

- `METADATA_ONLY`, which records a content-addressed model artifact without execution;
  or
- `BUILTIN_DETERMINISTIC_SIMULATOR`, which selects a source-controlled simulator from a
  fixed registry by identifier.

Built-in simulators use strict typed input/output, deterministic seeds, explicit step
and memory limits, and no filesystem, network, subprocess, dynamic import, `eval`, or
`exec` authority. Untrusted imported artifacts are never executed.

### 17.3 Revision and admission

Each hypothesis version is immutable. A revision references the prior version,
triggering observation or failed check, assumptions and variables added/removed/changed,
mechanism changes, preserved elements, changed predictions, changed falsification
conditions, motivating checker results, considered counterexamples, author, timestamp,
and resulting version.

Stable admission requires schema and provenance validation, declared assumptions and
scope, registered predictions and falsification conditions, checker execution,
counterexample search, valid revision history, required independent review, and an
explicit decision under the active policy. Confidence, self-consistency, and proposer
approval cannot independently authorize admission.

The imported-pattern lifecycle is `SOURCE_PATTERN_REVIEW_PENDING`,
`GENERIC_PATTERN_EXTRACTED`, `TRANSFER_TESTING`, `TRANSFER_VALIDATED`,
`DOMAIN_SPECIFIC`, `BENCHMARK_SPECIFIC`, `REJECTED`, and `ADMITTED_TO_SSOH`.

## 18. Evaluator Succession and Collapse Monitoring

Evaluator versions, threshold histories, benchmark versions, disagreement cases,
external tests, promotion rationales, and rollback targets are append-only.

The lifecycle is:

```text
active evaluator N
-> propose N+1
-> evaluate N+1 with N
-> run protected external tests
-> human review
-> canary
-> promote or reject
```

A candidate cannot select all tests, read protected answers, rewrite thresholds, delete
disagreement, reinterpret earlier failures, or approve itself. Promotion updates only
an active-evaluator projection and is reversible to the recorded predecessor.

Collapse monitoring stores separate metrics for protected and external performance,
calibration, response and hypothesis diversity, source and experiment diversity,
adapter-output entropy, repeated errors, confidence/error coupling, evaluator
disagreement, catastrophic regression, task-distribution narrowing, and externally
grounded-data proportion. No aggregate score authorizes promotion.

Prohibited patterns are recorded as explicit checker findings, including shared
proposer/judge weights or adapters, confidence-as-reward, self-consistency-as-truth,
correlated reviewers, self-generated data admitted by self-approval, evaluator gains
without external gains, confidence without evidence, paraphrase-only refinement, and
textual agreement displacing empirical evidence.

## 19. Harness Behavior Handbook

### 19.1 Authority

The handbook is a derived index. Source, tests, governance policy, and active rules
remain authoritative.

Source-controlled JSON behavior manifests provide behavior identity, purpose, public
contract, inputs, outputs, preconditions, postconditions, failure modes, state read and
written, tools, permissions, source symbols, tests, related behaviors, and governing
rules. JSON avoids a new runtime YAML dependency.

### 19.2 Deterministic build

The builder uses the Python standard-library AST to locate declared modules, classes,
functions, and methods. It records repository, commit, relative path, symbol, exact line
range, relationship, verification method, and source hash. Paths must remain contained
under the resolved repository root and must reject symlinks and Windows reparse-point
escapes.

The builder does not infer behavioral truth from syntax. Human-authored manifests state
the behavior; AST facts verify that claimed source locations exist and remain current.

Generated JSON and Markdown provide progressive disclosure:

1. behavior summary;
2. contracts, dependencies, and governing rules;
3. modules and symbols;
4. exact source locations and tests;
5. bounded implementation context.

A `HandbookVerificationRecord` references the manifest hash, repository commit, source
hashes, generated artifact hash, stale locations, missing symbols, and result. Source
changes identify affected behaviors, which in turn identify governing rules.

## 20. Fair Harness-evolution Evaluation

### 20.1 Campaign design

Every campaign records applicable variants:

- `UNCHANGED_HARNESS_SINGLE_ATTEMPT`
- `UNCHANGED_HARNESS_BEST_OF_N`
- `UNCHANGED_HARNESS_RETRY_WITH_FEEDBACK`
- `UNCHANGED_HARNESS_TASK_LEVEL_SEARCH`
- `RANDOM_HARNESS_SEARCH`
- `SIMPLE_PARAMETER_SEARCH`
- `EVOLVED_HARNESS`

The campaign fixes or explicitly marks mismatches in model, model version, adapter,
tasks, feedback, tools, attempts, tokens, reasoning budget, evaluator calls, wall-clock
limit, cost, and human intervention.

Task identities belong to exactly one partition within a campaign version:
`HARNESS_DISCOVERY_TASKS`, `HARNESS_VALIDATION_TASKS`, `HARNESS_TRANSFER_TASKS`,
`HARNESS_REGRESSION_TASKS`, or `HARNESS_SAFETY_TASKS`. Partition manifests are immutable
and content addressed. A task cannot cross partitions after campaign creation.

### 20.2 Protected holdouts

Protected expected outputs live in a physically separate `ProtectedEvaluationStore`
with its own SQLite database and artifact root. It is absent from the ordinary
`RepositorySet`, campaign export, candidate dependency graph, application logs, error
envelopes, and audit payloads. The ordinary campaign database stores protected-content
hashes and typed independent checker results, never plaintext protected answers or
reversible answer references.

Capability-scoped roles enforce the boundary:

- a candidate receives only `PublicTaskInputReader` and an immutable budget view;
- the campaign coordinator can write public manifests but cannot read protected data;
- an evaluator executor receives read-only protected answers, candidate outputs, and a
  fixed checker configuration, but cannot invoke or import candidate code;
- an evaluator-facing spawned result validator has no database authority and may
  return only a strict `ProtectedCheckerResult` DTO containing typed hashes,
  aggregates, and checker outcomes;
- a coordinator-local result gateway may append that DTO only through its supplied
  active main-database unit-of-work connection, is never placed in evaluator or
  candidate graphs, and cannot transmit protected bytes or reversible references; and
- decision authorities receive admitted result records without expected outputs, while
  a separately privileged integrity auditor may verify protected-store hashes.

Deterministic adversarial tests inspect dependency object graphs and exercise
serialization, exceptions, logs, audit events, exports, and indirect-reference fields to
prove candidate and coordinator services cannot fetch, construct, infer through object
references, or leak protected answers. Evaluator tests prove that only the validated
result DTO crosses the worker boundary and that its schema cannot contain answer
material. Coordinator tests prove the local gateway uses the exact supplied
transaction, including same-transaction campaign visibility and atomic rollback,
without opening a competing SQLite writer.

### 20.3 Results and decisions

Append-only records preserve every iteration, negative result, budget use, evaluator
configuration, confound, and rollback. Reports distinguish discovery, validation,
transfer, regression, and safety results.

Statuses are `PROPOSED`, `DISCOVERY_GAIN`, `VALIDATION_GAIN`, `TRANSFER_VALIDATED`,
`REGRESSION_DETECTED`, `BENCHMARK_SPECIFIC`, `INCONCLUSIVE`, `REJECTED`, `ADMITTED`, and
`ROLLED_BACK`.

A discovery gain cannot authorize admission. Unmatched budgets are incomparable unless
an independently approved analysis establishes a bounded interpretation. Evaluator
changes, extra inference, feedback, attempts, leakage, and stopping differences are
recorded as potential causal confounds.

## 21. Storage and Migrations

Released migration `0001_epistemic_kernel` remains unchanged. Reviewable forward
migrations align with the implementation phases:

1. `0002_governed_adaptation_foundation` — research runs, configurations,
   self-improvement classification/measurement, evaluator versions and succession.
2. `0003_progress_and_evidence_trails` — plans, progress events, budgets, checkpoints,
   trail versions/nodes/relations/checks.
3. `0004_behavioral_rules` — incidents, rule versions, reviewer assessments,
   consolidation, regression cases, heads.
4. `0005_hypotheses_and_representations` — primitive versions/evaluations, hypotheses,
   model/checker specs and results, counterexamples, revisions, admissions.
5. `0006_handbook_and_harness_evaluation` — behavior/rule links, handbook verification,
   campaigns, splits, budgets, observations, metrics, confounds, decisions.

Each migration adds append-only triggers for authoritative tables and explicit foreign
keys for new relationships where SQLite can enforce them without changing v0.1.0
semantics. SQLite foreign-key enforcement is enabled and tested for new tables. Mutable
head tables use constrained references to immutable versions and are reconciled by
whole-workspace verification.

Migration tests cover upgrade from a genuine `0001` database, clean upgrade to head,
downgrade where supported, append-only behavior, foreign keys, orphans, corrupt JSON,
audit reconciliation, idempotent import, unsupported schema versions, and unknown
fields.

## 22. CLI Design

The top-level JSON envelope remains schema version 1. Existing command names, output,
and exit behavior remain unchanged. Exit status remains 0 for success, 2 for invalid or
rejected input, 3 for integrity failure, and 4 for not found.

Complex records are supplied as strict UTF-8 JSON through `--input PATH`; this avoids a
wide, unstable option surface. Read commands accept stable identifiers as arguments.
Every command supports the existing human output and `--json` mode.

The grouped surface is:

```text
research-run create --root PATH --input PATH

governance propose --root PATH --input PATH
governance show --root PATH

improvement classify --root PATH --input PATH
improvement report --root PATH CHANGE_ID

progress add --root PATH --input PATH
progress validate --root PATH --input PATH
progress status --root PATH RUN_ID

trail create --root PATH --input PATH
trail add-node --root PATH --input PATH
trail add-relation --root PATH --input PATH
trail validate --root PATH TRAIL_ID

rule propose --root PATH --input PATH
rule review import --root PATH --input PATH
rule consolidate --root PATH --input PATH
rule history --root PATH RULE_ID

primitive propose --root PATH --input PATH
primitive evaluate --root PATH --input PATH

hypothesis propose --root PATH --input PATH
hypothesis revise --root PATH --input PATH
model register --root PATH --input PATH
verifier record --root PATH --input PATH

handbook build --root PATH --repository PATH --manifest PATH --output-dir PATH
handbook verify --root PATH --repository PATH --manifest PATH

harness-eval create --root PATH --input PATH
harness-eval record --root PATH --input PATH
harness-eval report --root PATH CAMPAIGN_ID
```

The public command remains `verifier record` for compatibility with the requested
surface, but the input schema requires a category and distinguishes formal verifier,
deterministic checker, and learned judge. Human output uses the precise category rather
than calling every result verification.

Handbook paths must resolve under the declared repository root, reject symlink/reparse
escapes, and constrain output to the declared repository. No command accepts arbitrary
shell, Python, module, provider, or executable arguments.

## 23. Security Design

All papers, source text, proposal JSON, reviewer results, model output, tool output,
manifests, simulation results, evaluator reports, and imported benchmark data are
untrusted.

The release requires:

- frozen strict schemas and unknown-field rejection;
- stable identifier and schema-version validation;
- path containment, regular-file checks, and symlink/reparse protection;
- artifact size and SHA-256 validation;
- fixed redaction of validation failures before durable storage;
- explicit provenance and permissions;
- bounded simulator and checker resources;
- protected split interfaces;
- evaluator/proposer relationship records;
- independent promotion authority;
- tamper-evident decisions and complete workspace reconciliation.

No record can cause dynamic import, shell execution, subprocess execution, network
access, or evaluation of supplied source text. The development quality command remains
separate from scientific runtime authority.

## 24. Error Handling

Malformed proposals with safe proposal and idempotency identifiers become durable,
audited, replayable rejections. Inputs without truthful durable identity receive stable
non-durable errors. Unexpected programming, database, filesystem, and integrity errors
propagate and roll back instead of being mislabeled as invalid user input.

Every domain defines stable rejection codes for missing entities, invalid lineage,
permission failure, self-approval, insufficient grounding, prohibited closed loop,
unmatched budgets, protected-data access, stale handbook mapping, invalid dependency,
false finish, circular evaluator approval, and benchmark-specific admission.

Imported structured records are idempotent by canonical content and stable intent
identity. A changed record under an existing key is an audited conflict, not an update.

## 25. Quality Gate

All existing checks and thresholds remain. Domain validation is added through tests
invoked by the existing fixed test check, including source-register uniqueness,
contradictions, duplicate fixtures, supersession, reviewer boundaries, handbook
staleness, trail integrity, dependency integrity, false finishes, succession, protected
splits, imported-pattern isolation, migrations, and audit verification.

Wheel installation requires one reviewed extension to the fixed registry. The prior
eight-check gate must pass unmodified before that registry changes. The proposed ninth
fixed check installs the built wheel into an isolated temporary environment and runs a
CLI smoke test. It accepts no user path, check selection, skip, or threshold. Approval
of this written specification authorizes proposing that additive gate change; it does
not authorize weakening or replacing any prior check.

A `QualityPolicyProposal` is an append-only governance record referencing the intended
source diff hash, prior registry hash, measurement, rationale, regression tests,
approval, and rollback commit. Runtime admission cannot edit source or the quality
registry. After human approval, the ordinary development workflow applies the reviewed
source change, first demonstrates the old gate under the prior registry, and then
demonstrates the new additive gate.

## 26. Test Strategy

Every behavioral component follows red-green-refactor TDD. Test types are:

- unit tests for strict contracts, decisions, transitions, and calculations;
- property tests for immutability, idempotency, ordering, dependency graphs, and audit
  reconciliation;
- integration tests for repositories, migrations, filesystem boundaries, application
  transactions, and CLI envelopes;
- adversarial tests for permissions, protected data, circular approval, source drift,
  semantic overlap fixtures, and imported-pattern isolation;
- deterministic end-to-end tests for the complete governed adaptation scenario.

Required domain assertions include:

| Domain | Required assertions |
| --- | --- |
| Adaptation | closed-loop governance fails; self-promotion fails; confidence cannot authorize persistence; grounding `NONE` blocks stable promotion; required validation rises with persistence; durable promotion binds a passed independent evaluator audit |
| Progress | provisional is not official; validation requires an independent actor; invalidation reduces effective progress; dependencies are enforced; false finishes fail; final validator controls success; progress cannot authorize claims, training data, adapters, harness changes, or final success; exhaustion differs from failure; checkpoints retain raw references |
| Evidence trails | missing or modified sources fail; missing spans and invalid relations fail; contradictions remain; insufficiency supports abstention; report sentences remain traceable |
| Rules | exact duplicates cannot both activate; semantic duplicates enter review; contradictions require boundaries; incidents survive consolidation; lower authority cannot override; reviewers cannot write the registry; dissent remains |
| Primitives | primitive/evaluator circular approval fails; duplicate concepts are flagged; semantic versions hold; experimental concepts cannot enter canonical schemas; history remains available |
| Hypotheses | revisions are immutable and explicit; unsupported models do not execute; checker limitations persist; confidence cannot admit; transfer status is required |
| Handbook | stale paths and missing symbols fail; rules link to behaviors; source changes identify behaviors; handbook cannot override source or tests |
| Harness evaluation | unmatched budgets are incomparable; discovery is not transfer; partitions cannot overlap; benchmark-specific gain is labeled; negative results persist; evaluator changes are confounds; protected answers are absent from ordinary object graphs, exports, errors, logs, and audit payloads |
| Audit/storage | accepted and rejected proposals are audited; tampering is detected; authoritative updates/deletes fail; `0001` upgrade works; import/export round trips; exact replay remains unchanged |

The imported-control-pattern firewall is neutral executable code. Plaintext prohibited
source-attribution terms live only in the designated attribution policy document. The
executable checker pins that document's SHA-256 digest and exact permitted attribution
paths, validates its strict schema before use, and scans the source tree to prove the
terms occur nowhere else. It also inspects executable schemas and package inventories
for domain-specific spatial, color, cell, grid, dataset-loader, adapter, command, and
dependency assumptions without embedding a benchmark implementation in tests.

The firewall digest and path allowlist are part of the versioned quality-policy hash.
Changing the policy document, deleting a prohibited term, broadening an allowed path, or
changing the pinned digest requires a `QualityPolicyProposal`, successful execution of
the immutable prior eight-check gate, and human approval. Tests deliberately remove
terms, broaden paths, modify the document, and mismatch the digest; each tamper must
fail before the proposed policy can become active.

## 27. Deterministic Transfer Cases

The generic hypothesis-model-checker loop is evaluated on independently authored SSOH
fixtures:

1. A thermal-chamber state-transition model with bounded heating and cooling.
2. A numerical exponential-decay simulation with falsifiable parameter predictions.
3. A synthetic equipment-incident document requiring temporally dispersed evidence.
4. A simulated software-maintenance task over an in-memory file manifest, with no shell
   execution.
5. A sensor-calibration planning task with experiments that distinguish competing
   hypotheses.

Matched conditions compare direct deterministic reasoning, ordinary plan-and-execute,
retry with checker feedback, and the typed revision loop. Metrics remain separate:
correctness, checker accuracy, false admission, hypothesis diversity, revision utility,
unsupported-model rate, abstention, cost, transfer, and regressions.

## 28. End-to-end Demonstration

The offline example uses synthetic thermal-chamber research records and equipment
incident notes created for SSOH. It will:

1. initialize the v0.1-compatible kernel;
2. submit and independently approve the explicit V1-to-V2 governance-policy transition;
3. add immutable synthetic source evidence;
4. create a research run and progress plan;
5. propose competing thermal hypotheses;
6. register the built-in deterministic thermal simulator;
7. record predictions and falsification criteria;
8. construct and validate a natural evidence trail;
9. validate some progress while retaining provisional work;
10. reject a false finish;
11. preserve a failed hypothesis and explicit revision;
12. create a rule incident and proposed rule;
13. import all five reviewer-role records;
14. detect overlap and consolidate one canonical boundary rule;
15. preserve both incidents as regression cases;
16. link the rule to a behavior and verify its source mapping;
17. compare a candidate harness change with matched baselines;
18. record a discovery gain that fails transfer as `BENCHMARK_SPECIFIC` and reject it;
19. admit a second candidate only after held-out transfer and independent authority;
20. export a self-improvement measurement report; and
21. verify the entire workspace and audit chain, including mixed V1/V2 policy history.

The example requires no model, API, network, GPU, imported code, or arbitrary shell.

## 29. Documentation

The release updates `README.md`, `ARCHITECTURE.md`, `GOVERNANCE.md`, `SECURITY.md`,
`CLAIM_LEDGER.md`, the source register, and research inspirations. It creates the
currently absent `REPRODUCIBILITY.md` and `THREAT_MODEL.md` rather than pretending they
already exist.

Focused documents cover self-improvement governance, long-horizon execution, evidence
trails, behavioral rules, the handbook, harness-evolution evaluation, representational
primitives, hypothesis-model-checker control, and the deterministic governed-adaptation
example.

Every document labels features as implemented, interface-only, deterministic fake,
experimental, deferred, source-inspired, reproduced, or not reproduced. The release
does not claim scientific truth, solved hallucination, safe recursive self-improvement,
open-ended autonomy, formal verification from a judge, general improvement from local
benchmark gains, reproduction of S21-S29, or compatibility with S29's benchmark.

## 30. Implementation Phases

| Phase | Scope | Completion gate |
| --- | --- | --- |
| A — source and governance | S21-S29; research runs; configurations; classifications; measurement; evaluator succession; migration 0002 | TDD, source review, migration tests, governance docs, focused commit |
| B — progress and evidence | progress plans/events; budgets; checkpoints; false finishes; evidence trails; migration 0003 | TDD, source-integrity and dependency tests, docs, focused commit |
| C — behavioral rules | incidents; rule versions; duplicate/conflict handling; reviewer protocols; integrator; regression cases; migration 0004 | TDD, fresh-context spec and code reviews, docs, focused commit |
| D — hypotheses and representations | primitives; hypotheses; safe model metadata; built-in simulators; checker results; revisions; migration 0005 | TDD, imported-pattern firewall and transfer tests, docs, focused commit |
| E — handbook and evaluation | manifest/AST handbook; source verification; matched-budget campaigns; protected splits; collapse metrics; migration 0006 | TDD, stale mapping and transfer tests, docs, focused commit |
| F — CLI and proof | grouped CLI; deterministic example; reports; package version; full docs; additive wheel-install gate | old gate under prior registry, new full gate, independent reviews, draft PR |

Each phase uses fresh-context reviewer agents that submit findings rather than editing
authoritative registries or protected tests. Specification compliance and code quality
are separate reviews.

## 31. Acceptance

The release is complete only when:

- every v0.1.0 behavior and old test remains intact;
- all new unit, property, integration, migration, CLI, adversarial, and end-to-end tests
  pass;
- strict mypy, Ruff, branch coverage, Bandit, dependency audit, build, Twine, wheel
  installation, and audit verification pass;
- migration from the exact `0001` schema and a clean database passes;
- S21-S29 metadata and limitations are accurate and none is marked reproduced;
- official progress and final success remain independent;
- evidence trails retain exact source bindings and contradictions;
- rule conflicts create explicit boundaries and recurrence repairs canonical rules
  without deleting incidents;
- reviewer records cannot mutate canonical state;
- handbook mappings detect source and symbol drift;
- harness comparisons disclose budgets, splits, evaluator changes, negative results, and
  transfer status;
- benchmark-specific gains cannot be admitted as general improvements;
- executable records cannot introduce code-execution authority;
- evaluator succession is independent and reversible;
- protected holdouts remain inaccessible to candidates;
- no quality check is weakened or removed;
- the deterministic example and complete audit verification pass;
- executable code, configuration, fixtures, examples, commands, dependencies, and
  domain contracts contain no implementation or hidden assumptions from S29's
  benchmark; and
- the draft pull request records architecture, migrations, test commands and outputs,
  attribution, security analysis, known limitations, deferred work, and evidence for
  each acceptance criterion.

## 32. Approved Design Decision

The user approved the transactional modular-domain architecture on 2026-07-18. This
written specification must receive a separate review before the implementation plan is
created. Approval of the architecture does not waive the remaining specification,
planning, TDD, independent review, quality-gate, or pull-request gates.
