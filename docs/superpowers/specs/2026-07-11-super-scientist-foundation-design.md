# Super Scientist Orchestration Harness Foundation Design

Status: Approved in design review on 2026-07-11

## 1. Purpose

Build a model-agnostic, local-first scientific research orchestration harness in
which models and tools may propose work, but a deterministic kernel owns durable
state, admission, provenance, permissions, evaluation policy, and rollback.

The first release is a scientific research operating-system foundation, not a
single autonomous scientist. It does not claim to guarantee scientific truth.

## 2. Scope

The release contains:

1. Python packaging, developer tooling, and typed configuration.
2. Replaceable model, training, execution, and storage protocols.
3. Append-only evidence and a content-addressed artifact store.
4. An Atomic Claim Ledger with validated status transitions.
5. Hypotheses, contradictions, experiments, plans, and memory views.
6. A proposal, admission, and transaction kernel.
7. Role-based orchestration and a Socratic critic interface.
8. A validated research-run state machine.
9. Permission, budget, audit, and reproducibility records.
10. A CLI with stable machine-readable output.
11. A repository-local quality-gate command.
12. Quarantined QLoRA dataset, training, evaluation, promotion, and rollback
    scaffolding.
13. Documentation and a deterministic end-to-end example.
14. Unit, integration, property, adversarial, migration, CLI, packaging, and
    end-to-end tests.

The release excludes unrestricted shell execution, autonomous laboratory control,
live self-modification, automatic adapter promotion, a graphical interface,
distributed infrastructure, a workflow language, and an RL framework.

## 3. Design Provenance

The source register is `docs/sources/source-register.yaml`. The attribution and
source-review narrative is `docs/research-inspirations.md`.

The following are project-specific synthesis decisions and are not attributed to
any cited paper:

- Combining the components into a scientific research operating system.
- The immutable epistemic kernel and its exact authority boundaries.
- The complete Atomic Claim Ledger schema and status lifecycle.
- The claim-drift firewall.
- Separation of proposal, admission, evaluation, and promotion authorities.
- The four learning levels and QLoRA promotion lifecycle.
- The constitutional distinction between ordinary and protected assets.
- The combined anti-self-gaming policy.
- The CLI, package layout, persistence model, and hash-chain format.
- Superpowers and RepoQualityGate integration contracts.

No cited system is reproduced by this design. Compatibility is not claimed with
any source repository.

## 4. Alternatives Considered

### 4.1 Layered transactional modular monolith - selected

Use one local process, SQLite database, and content-addressed artifact directory.
Separate domain, application, orchestration, evaluation, provider, and persistence
interfaces. This gives the first release one understandable transaction boundary
without preventing later storage or provider implementations.

### 4.2 Full event sourcing - rejected for the first release

Making every domain fact an event would provide strong temporal reconstruction but
would materially increase replay, migration, projection, concurrency, and debugging
complexity. The selected design keeps an append-only decision log while using
normalized effective-state projections.

### 4.3 CRUD state with an audit sidecar - rejected

This is simpler initially, but domain state and audit history could diverge. It does
not make immutability or tamper evidence structural enough for the project.

## 5. Architecture

Dependencies point inward:

```text
CLI / Python API
       |
Application services
       |
Orchestration ---- Evaluation
       |               |
Immutable epistemic kernel
       |
Repository interfaces
       |
SQLite + content-addressed artifacts
```

### 5.1 Epistemic kernel

Owns typed proposals, deterministic admission, authority checks, claim transitions,
evidence immutability, active governance policy, transaction decisions, and the
tamper-evident audit chain. It cannot call models or execute arbitrary tools.

### 5.2 Application services

Expose use cases and transaction boundaries to the CLI and Python API. They convert
validated requests into proposals and return typed decisions. They cannot bypass
admission.

### 5.3 Orchestration

Coordinates research runs, roles, plans, memory views, critics, experiments,
permissions, and budgets. Every durable change returns through the kernel.

### 5.4 Evaluation

Hosts claim-drift validators, scientific-review interfaces, robustness checks,
reproducibility checks, and adapter evaluations. Evaluation results are evidence for
admission or promotion; evaluators do not write committed state directly.

### 5.5 Providers

Implement application-owned protocols for model calls, embeddings, vision,
training, execution, storage, clocks, and identifiers. Vendor message types and SDKs
do not enter the kernel. Deterministic providers are the default in tests.

### 5.6 Persistence

SQLite stores structured records and projections. Immutable raw evidence and larger
artifacts live in a content-addressed directory. SQLAlchemy models remain separate
from Pydantic API and domain contracts.

## 6. Package Boundaries

```text
src/super_scientist/
    cli/
    config/
    domain/
        evidence/
        claims/
        hypotheses/
        experiments/
        runs/
        adapters/
    kernel/
        transactions/
        admission/
        governance/
        audit/
        state_machine/
    application/
    orchestration/
        coordinator/
        roles/
        planning/
        memory/
        critics/
    providers/
        models/
        training/
        execution/
        storage/
    evaluation/
        claim_drift/
        scientific/
        robustness/
        regression/
    quality/
```

Modules have focused contracts. Generic `manager`, `utils`, and `helpers` modules are
not permitted as accumulation points.

## 7. Persistence Decisions

### 7.1 Database and migrations

- SQLite is accessed through SQLAlchemy 2.x.
- Alembic owns explicit schema migrations.
- Every migration declares source and target schema versions.
- Tests cover upgrade from the preceding schema fixture and effective-state parity.
- Historical audit payloads retain their original schema versions and are not
  rewritten by migrations.

### 7.2 Artifact storage

Artifacts are stored at `artifacts/sha256/<prefix>/<digest>`. The writer:

1. Streams bytes to a temporary file under the artifact root.
2. Computes and verifies SHA-256.
3. Rejects unsafe links and paths outside the configured root.
4. Atomically renames the file to its digest-derived location.
5. Treats an existing digest as immutable and verifies its bytes.

Database records store the digest, size, media type, and relative reference. Artifact
preparation precedes database admission. An unreferenced artifact is harmless and can
only be removed by an explicit, audited maintenance operation.

### 7.3 Transaction isolation and idempotency

Admission begins with SQLite `BEGIN IMMEDIATE`, serializing commit decisions against
the latest effective state. Every proposal has a globally unique proposal identifier
and caller-supplied idempotency key. A duplicate returns the original decision. Reuse
of a key with different canonical content is rejected.

### 7.4 Audit chain

Accepted and rejected proposals both create audit events. Each event records:

- Event and schema identifiers.
- Canonical JSON payload hash.
- Previous event hash.
- Event hash over the previous hash and canonical event envelope.
- Actor, transaction, policy, and timestamp references.

Projection updates, the transaction decision, and audit append commit atomically.
Verification replays the chain, rehashes referenced artifacts, rebuilds projections,
and compares state hashes.

### 7.5 Governance policy

Approved policy snapshots are content-addressed and registered in the database. The
active policy is an immutable hash reference. Editing a working configuration file
cannot silently alter active rules. A mismatch fails closed. Constitutional changes
require a governance transaction, review under the active policy, quality evidence,
and human approval.

**Approved third final-review correction:** a missing active-policy pointer is not an
invitation to initialize when any registered policy or other durable kernel state
exists. Initialization may activate a policy only in a genuinely empty database;
orphaned governance fails closed and remains inspectable through storage-only audit
verification. Policy input is strict schema-version-1 JSON: invalid UTF-8, unsupported
versions, and unknown fields are rejected. Externally parsed nested domain records also
forbid unknown fields so canonical transaction identity cannot discard future input.

## 8. Domain Contracts

### 8.1 EvidenceRecord

Contains a stable identifier, evidence type, source locator, retrieval time, content
hash, raw artifact reference, exact span or structured observation, provenance,
license metadata, ingestion actor, and verification state. Content is append-only.
Availability and verification changes create new events.

### 8.2 AtomicClaim

Contains the canonical proposition, scope, population or system, time range,
variables and units, epistemic modality, exact evidence spans, derivations,
assumptions, counterevidence, contradictions, confidence, verification results,
status, version lineage, content hash, and timestamps.

Allowed primary status transitions are:

```text
PROPOSED
  -> EVIDENCE_LINKED
  -> TESTABLE
  -> REPRODUCED | CORROBORATED | CONSTRAINT_VALIDATED
  -> SUPERSEDED

Any nonterminal state -> FALSIFIED | WITHDRAWN
FALSIFIED -> SUPERSEDED
```

An evidence-linked claim that is not experimentally testable may advance directly to
`CORROBORATED` after independent evidence review. `CONSTRAINT_VALIDATED` always
names the encoded constraints and assumptions. Terminal records are not revived;
corrections create successor claims.

`REPRODUCED` requires a distinct reproduction record. `CORROBORATED` requires
independent evidence or review under configuration-aware identity rules. Different
prompts on the same model and adapter do not count as independent replication.

**Approved final-review correction:** configuration awareness identifies execution
provenance but does not create independence. Two model actors with the same provider,
model, and adapter are aliases for approval purposes even when their
`configuration_hash` values differ; independence requires a distinct model/adapter
identity or a distinct typed human/non-model authority. In the implemented vertical
slice, typed falsification/counterevidence reviews and successor references are not yet
modeled, so `FALSIFIED` and `SUPERSEDED` transitions fail closed as
`INDEPENDENT_REVIEW_REQUIRED`. Withdrawal is status-only apart from required lineage,
timestamp, and authorized creator metadata.

### 8.3 Hypothesis and contradiction

A hypothesis contains its statement, mechanism, assumptions, predictions,
falsification criteria, competitors, belief record, evidence on both sides, and
planned discriminating experiments. Beliefs are timestamped assessments, not facts.
Contradictions link claims or hypotheses and remain visible after resolution.

### 8.4 ExperimentSpec and ExperimentResult

The specification records the research question, hypotheses, predicted outcomes,
variables, controls, procedure, required tools, permissions, budget, statistical plan,
stopping conditions, safety constraints, and reproduction requirements. Results link
immutable observations, execution metadata, deviations, failures, and analyses.

### 8.5 Transaction

Contains proposal identity and type, proposer, inputs, proposed changes,
preconditions, policy snapshot, admission checks, decision, structured reasons,
committed changes, audit linkage, and timestamps.

### 8.6 ResearchRun

Contains the charter, user question, operational definition, plan, state, role
assignments, budgets, permissions, inputs, outputs, transactions, final claims,
uncertainties, and reproducibility manifest.

## 9. Admission

Every mutation uses one pipeline:

```text
typed proposal
-> identity, permission, and budget checks
-> idempotency and conflict checks
-> deterministic domain validators
-> independent-review requirements
-> admit, reject, or escalate
-> atomic projection and audit append
```

Admission does not call a model. Model-assisted checks can request review but cannot
issue deterministic certification.

Claim-drift validators return exactly one of:

- `PASS_DETERMINISTIC`
- `FAIL_DETERMINISTIC`
- `REQUIRES_INDEPENDENT_REVIEW`
- `NOT_APPLICABLE`

Each result records validator version, input hashes, reason code, evidence references,
and execution time. Validators cover atomicity, source and span existence, scope,
population, modality, causation language, numbers, units, time, assumptions,
contradictions, derivations, confidence bounds, and reconstruction requirements.

Stable rejection codes include `SELF_APPROVAL`, `MISSING_EVIDENCE`,
`EVIDENCE_HASH_MISMATCH`, `INVALID_STATUS_TRANSITION`, `SCOPE_DRIFT`,
`MODALITY_DRIFT`, `CONTRADICTION_UNRESOLVED`, `PERMISSION_DENIED`,
`BUDGET_EXHAUSTED`, `INDEPENDENT_REVIEW_REQUIRED`, and
`POLICY_HASH_MISMATCH`.

Policy rejection is a durable domain result, not an exception. Exceptions are reserved
for infrastructure faults. Audit, policy, artifact, and projection integrity failures
fail closed.

Expected proposal-construction validation failures are durable only when a typed,
trusted attempt envelope establishes proposal ID, idempotency key, proposer, and kind
before factory invocation. An exact retry replays that rejection without invoking the
factory. Unexpected programming and storage exceptions roll back and propagate as
infrastructure faults; they are never converted to `INVALID_PROPOSAL`.

## 10. Identity and Authority

`ActorIdentity` contains actor kind, stable identifier, role grants, and model,
provider, and adapter configuration hashes when applicable. Authorities are logical
identities even if one physical service fills multiple roles during development.

Ordinary evidence and claim transactions may be admitted automatically when all
deterministic checks and authority-separation rules pass. Human approval is mandatory
for constitutional governance changes and adapter promotion.

A proposer cannot approve its own proposal. A role-prompt change does not create an
independent evaluator. Model confidence and multi-agent agreement are not evidence.

## 11. Orchestration

The coordinator is an untrusted proposal loop:

```text
load committed state and source-linked memory view
-> construct provider-neutral role request
-> receive untrusted typed output
-> parse a proposal
-> submit through the application layer
-> observe the recorded decision
```

Malformed provider output is recorded as a failed proposal attempt with redacted
diagnostics and cannot create partial committed state.

### 11.1 Run state machine

The normal path is:

```text
CREATED -> CHARTERED -> EVIDENCE_GATHERING
-> HYPOTHESIS_FORMATION -> SOCRATIC_REVIEW
-> EXPERIMENT_PLANNING -> EXECUTION_PENDING -> EXECUTING
-> ANALYSIS -> INDEPENDENT_REVIEW
-> REPRODUCTION_PENDING | CLAIM_ADMISSION
-> COMPLETE
```

The declarative transition policy permits explicit loops to evidence gathering,
hypothesis formation, experiment planning, and independent review. `ABSTAINED`,
`FAILED`, and `CANCELLED` are terminal. `BLOCKED` records the prior state and requires
an authorized resume transaction.

### 11.2 Planning and memory

Plan nodes form a dependency graph with parent, prerequisites, completion criteria,
owner, status, and checkpoint references. Memory namespaces cover evidence, claims,
hypotheses, experiments, methods, failures, and decisions.

Summaries are immutable views linked to raw evidence and transactions. Retrieval
returns summary content, exact source references, and coverage diagnostics. Missing
information is distinguishable from retrieval failure. Initial retrieval uses
deterministic traversal and filtering; learned navigation is optional and deferred.

### 11.3 Roles and critic

The system represents coordinator, retriever, hypothesis generator, Socratic critic,
experiment planner, executor, statistical reviewer, citation auditor, reproduction
agent, claim-admission authority, adapter evaluator, and promotion authority roles.

The critic may question assumptions, request counterexamples, and propose
contradictions. It has no admission authority.

## 12. Providers, Permissions, and Budgets

Provider protocols cover chat or completion, embeddings, vision, training, execution,
storage, clocks, and identifiers. Requests contain structured roles, context, tools,
limits, and expected output schemas. Deterministic fakes cover success, malformed
output, timeout, refusal, and adversarial responses.

Permissions are typed grants for allowlisted tool categories and resource scopes.
Executors accept validated requests, never command strings. The first release includes
a deterministic in-process executor and protocol fixtures, not a shell executor.

Budgets use reservation and consumption records for wall time, model calls, tokens,
storage, and execution steps. Exhaustion blocks new work but cannot waive integrity,
safety, reproduction, or review requirements.

## 13. QLoRA Learning

Training support is optional. The core can create manifests, jobs, evaluations, and
adapter records without importing PEFT, Transformers, CUDA, or model SDKs.

The lifecycle is:

```text
DRAFT -> DATASET_REVIEW -> TRAINING -> QUARANTINED
-> EVALUATING -> CANARY -> PROMOTED
```

Failure branches lead to `REJECTED` or `REVISION_REQUIRED`. Promoted adapters may
become `ROLLED_BACK` or `RETIRED`.

Promotion requires immutable adapter and dataset hashes, frozen threshold and policy
hashes, split and contamination reports, independent evaluator identity, quality and
review evidence, human approval, and a tested rollback target.

Training and evaluation use capability-scoped dataset handles. Trainers cannot read
protected evaluation answers. Evaluation results are append-only. Thresholds are
snapshotted before evaluation. An adapter, trainer, optimizer, or proposer cannot
promote itself.

The first implementation provides QLoRA configuration schemas, a deterministic fake
trainer, dataset fixtures, and dry-run registration. A real Hugging Face provider is a
later optional implementation.

Adapters may influence proposals. They are never authoritative stores for facts,
evidence, permissions, governance, safety rules, benchmark answers, or transactions.

## 14. Development Governance

Harness changes use typed governance evidence with design, implementation-plan,
tests, review, verification, and quality-gate references. The runtime records generic
`DevelopmentGovernanceEvidence` containing tool name, version or hash, artifact
locator, result, timestamp, and verifier identity.

Superpowers is used as an installed development methodology [S19]. RepoQualityGate is
a locally installed, unversioned skill with unknown redistribution rights [S20]. The
project does not import or vendor either source. The repository quality command is an
independent project implementation, not a reproduction of RepoQualityGate.

## 15. CLI and API

`scientist-harness` exposes the required command groups for initialization, runs,
evidence, claims, hypotheses, experiments, transactions, audit, adapters, and quality.

`--json` returns a versioned envelope with `schema_version`, `command`, `success`,
`decision`, `data`, and structured errors. Human output renders the same result.
Rejected admissions, invalid transitions, broken audit chains, and failed quality
checks return nonzero exit codes.

The Python API uses the same application-service contracts and decision envelopes as
the CLI.

## 16. Security

- Core operation requires no network, model API, GPU, or shell.
- Retrieved content and provider output are untrusted data.
- Tool definitions, permissions, and policies cannot be introduced through retrieved
  text.
- Artifact paths are hash-derived and constrained beneath configured roots.
- Unsafe links and traversal are rejected.
- Secret-pattern checks run before persistence; configured sensitive fields are
  redacted from diagnostics and logs.
- Audit, policy, evidence, and projection integrity failures stop mutation.
- GitHub ownership and CI files express protected governance ownership, while
  documentation states that branch protection requires external configuration.

Prompt-injection risks and provider trust boundaries are documented in the threat
model. Least authority and dry-run behavior are defaults.

## 17. Deterministic Demonstration

The offline example asks which rule generated observations:

```text
H1: y = 2x
H2: y = x^2
```

Evidence at `x=1` and `x=2` fits both hypotheses. A discriminating experiment at
`x=3` predicts `6` and `9`; the deterministic executor returns `6`.

The example creates a run, adds two evidence records, proposes both hypotheses,
records falsification criteria, plans and executes the discriminating experiment,
records a critic challenge, rejects an unsupported claim, admits a scoped claim,
exports and verifies the audit chain, harvests a procedural lesson, registers and
evaluates a fake adapter, rejects self-promotion, records human-authorized promotion,
and rolls the adapter back.

## 18. Testing

Tests are divided into unit, integration, property, adversarial, CLI, migration,
packaging, and end-to-end suites. The initial coverage threshold is 90 percent line and
branch coverage. Critical admission and transition policies require explicit path
coverage rather than incidental line coverage.

Property tests cover append-only evidence, audit-chain preservation, idempotency,
deterministic replay, and legal transitions. Adversarial tests attempt:

- Proposer self-approval.
- Adapter self-promotion.
- Optimizer edits to evaluation results.
- Hidden holdout access.
- Evidence replacement and summary substitution.
- Audit corruption.
- Mid-evaluation threshold changes.
- Metric changes that evade the prior policy.
- Path traversal and unsafe artifact links.
- Verification bypass under budget pressure.
- False independence from identical configurations.
- Promotion without required development-governance evidence.

A clean built wheel is installed and exercised without training dependencies.

## 19. Quality Gate

`scientist-harness quality-gate` executes a fixed, non-user-extensible registry:

- Ruff format and lint checks.
- Strict mypy.
- All test suites and the coverage threshold.
- Bandit and dependency audit.
- Secret scan.
- Wheel and source build validation.
- Clean wheel installation.
- Documentation-link and license checks.
- Schema compatibility and migration tests.
- Reproducibility-manifest validation.
- Audit-chain and claim-evidence integrity tests.
- Training/evaluation separation and rollback tests.

Quality configuration is a hashed governance asset. Changes require review under the
prior policy. Fixed developer-tool subprocesses are distinct from research-run
execution and do not create a general shell interface.

## 20. Documentation

The release creates and maintains:

- `README.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `ARCHITECTURE.md`
- `GOVERNANCE.md`
- `CLAIM_LEDGER.md`
- `QLORA_LEARNING.md`
- `THREAT_MODEL.md`
- `REPRODUCIBILITY.md`
- `docs/sources/source-register.yaml`
- `docs/research-inspirations.md`
- `docs/examples/first-research-run.md`
- Superpowers design and implementation-plan documents
- ADRs for persistence, audit, governance, and authority decisions

Documentation distinguishes implemented behavior from roadmap items and does not
advertise guaranteed truth, compatibility, reproduction, or unrestricted autonomy.

## 21. Release Decomposition

Implementation plans are produced separately for:

1. Constitutional contracts and project foundation.
2. Epistemic-kernel vertical slice.
3. Scientific records, planning, and memory.
4. Orchestration, providers, permissions, and budgets.
5. Reproducibility, evaluation, and the deterministic example.
6. QLoRA governance scaffolding.
7. Repository quality, security, documentation, packaging, and draft PR.

The epistemic-kernel vertical slice is implemented first because all later durable
behavior depends on its authority and integrity guarantees.

## 22. Acceptance

The first release is complete only when a clean clone builds wheel and source
distributions, installs without model or training dependencies, passes strict typing,
formatting, lint, tests, security checks, dependency checks, migrations, the quality
gate, and the deterministic example.

Acceptance also requires detection of audit tampering and evidence replacement,
rejection of invalid claim and run transitions, preservation of source links from
summaries, authority separation, adapter self-promotion rejection, tested rollback,
accurate documentation, captured verification output, code review, and a draft pull
request. The work is never merged directly into `main`.
