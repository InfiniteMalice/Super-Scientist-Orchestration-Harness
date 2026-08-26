# Kernel Governance

## Active Policy

`scientist-harness init` loads `governance-policy.json`, validates it as a frozen typed
schema-version-1 policy with no unknown fields, canonicalizes it, and computes a SHA-256
policy hash. Invalid UTF-8, malformed JSON, unsupported versions, and extras fail as
`INVALID_POLICY`. The policy snapshot is an
append-only SQLite record; `governance_state` holds its active hash. Application
submissions compare their configured snapshot with that durable active hash before
admission. Missing, malformed, altered, or mismatched policy state fails closed.

Any workspace with transactions, audit events, or projections must have an active
pointer to a registered snapshot. Every audit event names a registered governing
policy. Policy-mismatch events record configured and stored hashes separately and are
governed by the stored active policy; if no governing policy exists, the rejection
cannot be durably attributed and no transaction or audit event is added.
When a valid stored governing policy exists but the service's configured snapshot is
unregistered, the mismatch rejection is still persisted and audited under the stored
policy; the unregistered configured hash is omitted from authoritative attribution.

Re-running `init` with the same active policy is idempotent. A migrated database is
initializable only when every kernel table is genuinely empty. If any registered policy,
transaction, audit event, evidence projection, claim version/head, or other durable
kernel state exists without the active pointer, `init` refuses both unchanged and
changed policy files. `audit verify` uses a storage-only path so it can report that
orphaned state even though normal runtime construction is unavailable. Re-running
`init` after changing an intact workspace policy is also rejected.

Integrity verification enumerates and validates every registered policy, including
unreferenced rows. Governance state is cardinality-checked as a true singleton and the
database constrains its identifier to 1; extra or malformed governance rows fail the
workspace even when the active policy itself remains valid.
Policy schema version is an exact integer 1; JSON booleans, floats, strings, and other
coercible representations are rejected as `INVALID_POLICY`.

## Approval Boundary

A proposal may include an approval, but the admission engine rejects it with
`SELF_APPROVAL` when proposer and approver are not independent. Equal actor identities
are never independent. Two model actors are independent only when their
`(provider_id, model_id, adapter_id)` identities differ. A configuration-hash, prompt,
seed, decoding, or other configuration-only change on the same model and adapter is an
alias, not an independent approver. A distinct typed human or non-model actor remains
independent when its actor identity differs. Model agreement is not human approval.

The default policy names `governance_change` and `adapter_promotion` as requiring human
approval. Governed policy transitions are implemented through the application
coordinator; direct policy-table mutation remains outside authority. Adapter training
is a deterministic metadata fake and promotion is represented by governed records, not
a live training runtime. The CLI does not provide a direct policy-replacement shortcut.
Operators must not mutate the SQLite policy tables directly.

## Self-Improvement Authority

A candidate policy cannot authorize any part of its own activation. V1-to-V2, later
V2-to-V2 transitions, and rollback proposals are evaluated under the stored prior
policy plus the source-controlled constitutional floor. Admission requires an exact
change classification, dedicated research run, complete protected measurement, passed
independent evaluator audit, independent human approval, compatible candidate hash,
and registered rollback policy. A transition commits all projections, its decision,
and its prior-policy-attributed audit event atomically.

Progress scores, evidence-trail coherence, rule-review agreement, model confidence,
handbook mappings, discovery-set gains, and self-authored evaluator claims have no
promotion authority. Evaluator succession has no automatic promotion and requires
protected and external evaluation, independence, a canary, human review, and a rollback
target. A benchmark-specific improvement remains `BENCHMARK_SPECIFIC`; only held-out
transfer plus independent authority can produce `ADMITTED`, relative to the declared
campaign and policy.

## Workspace Import Authority

Workspace exchange is implemented reconstruction, not cross-workspace trust
delegation. Export verifies the source and carries canonical proposals, decisions,
policy snapshots, projection expectations, and content-addressed artifact metadata.
Import validates all hashes and schemas, transfers verified bytes out of band, and
submits each record through the ordinary coordinator under its recorded governing
policy. It cannot install a bootstrap policy over nonempty durable state.

Identical content under the same stable intent replays. Different content under an
existing identity is an audited `IDEMPOTENCY_CONFLICT`, not an overwrite or merge.
Projection expectations are verification inputs only and cannot authorize canonical
state. Protected answers, protected-store references, live paths, and executable
configuration are prohibited from the bundle.

## Hypothesis Mutation Boundary

Hypothesis, model, checker, simulation, counterexample, revision, and admission
proposals use one exact classification: `RESEARCH_PROCESS`, `HUMAN_IN_LOOP`,
`RUN_LOCAL`, `INDEPENDENT_DETERMINISTIC_CHECK`, `CONTROLLED_EXPERIMENT`, and
`EMPIRICAL_MEASUREMENT`. The active V2 policy must contain the matching adaptation
requirement and must not apply promotion-only protected-evaluation or rollback flags to
these run-local stages. Every stage requires an independent human approval and exact
active-policy attribution.

Stage approval does not admit a hypothesis. Admission separately requires a
`TRANSFER_VALIDATED` candidate, exact committed receipts and audit chronology, passing
deterministic verification with retained counterexample search, no candidate
counterexample, complete revision lineage, controlled-experiment evidence, exact
admitted primitive heads, a passed evaluator audit, an accepted self-improvement
measurement, rollback metadata for a successor, and an independent human decision
authority. Only the admission projector can advance a hypothesis head.

Learned judges may contribute explicitly learned records, but cannot claim formal or
deterministic provenance and cannot satisfy the deterministic counterexample-search
gate by confidence or agreement. Caller-provided timestamps are not governance
authority. They must nevertheless respect the trusted lower bound of committed
dependencies and the trusted upper bound of the current transaction persistence time;
audit sequence preserves durable order and breaks equal-time ties.

## Cognitive And Procedure Authority

When a cognitive actor submits a capability, cohort, peer, topology, procedure,
guidance, matrix, trace, or reward proposal, `TransactionCoordinator` applies the exact
fixed handler and active-policy requirement before appending any authoritative record.
An accepted cognitive record is evidence only: it cannot change a claim head, policy,
harness head, progress head, model weight, tool permission, or protected evaluator.
Run `python -m pytest tests/adversarial/test_cognitive_authority.py
tests/property/test_cognitive_append_only.py -q` to verify these unchanged-state and
append-only results.

Operational diversity describes differences in model, prompt, tools, evidence, method,
and topology. Reviewer independence describes distinct authority identities under the
policy. When the same model uses different prompts, the cohort analyzer may record
diversity, but the admission engine must not count the variants as independent
reviewers. When peers agree unanimously, their contributions remain evidence and cannot
transition an `AtomicClaim`. The same adversarial command above verifies both rules.

The procedure lifecycle has four control-plane steps. First, the compiler resolves only
declared, current, accepted capability/catalog/source-snapshot receipts. Second, the
compiler emits either an accepted immutable compilation or `INVALID_PROCEDURE`; invalid
history persists without an executable plan. Third, the validator checks the compiled
DAG, artifact flow, tools, validators, budgets, termination, and forbidden operations.
Fourth, the binding handler delegates the exact plan to the canonical progress handler
in the same database transaction. If any binding check or projection fails, the
coordinator rolls back the compilation binding and progress projection together. Run
`python -m pytest tests/unit/procedures tests/integration/application/test_procedure_service.py
tests/adversarial/test_procedure_escalation.py -q` to verify the lifecycle.

When an evaluation cell is retained, its handler must match the exact protocol,
condition, model, harness, budget, trace, output, verifier, and reward evidence. Missing,
stale, ambiguous, surplus, or cross-protocol evidence produces a fixed rejection and no
evaluation projection. Generation metadata records `AVAILABLE`, `UNAVAILABLE`, or
`UNKNOWN`; an unavailable value cannot carry fabricated token, log-probability, request,
or context data. A high numeric reward is valid promotion evidence only when its exact
trace is current and every invalidating reward-hacking finding is absent. Run
`python -m pytest tests/integration/application/test_harness_eval_extensions.py
tests/adversarial/test_trace_reward_tampering.py -q` to verify these results.

## Quality Policy Protection

`scientist-harness quality-gate` has a source-controlled registry of exactly nine
checks: `format`, `lint`, `types`, `tests`, `security`, `dependencies`, `build`,
`package`, and `wheel-install`. The runner accepts no arbitrary command, path, check
selection, skip, or threshold. JSON is an output mode only. The test command fixes
branch coverage at 90 percent. The final check installs the exact built wheel in a fresh
environment and executes the installed governed cognitive example without repository
import fallback. Run `python -m pytest tests/unit/quality/test_runner.py
tests/unit/quality/test_wheel_smoke.py -q` to verify the inventory and wheel boundary.

Changes to the registry, `pyproject.toml`, or the CI workflow are protected as reviewed
source changes. This slice does not yet persist a runtime quality-policy hash or provide
a governance transaction for quality-policy changes; repository review and CI are the
implemented boundary.

The local RepoQualityGate skill informed the development-time discipline [S20]. It is
not vendored or imported, has unknown redistribution rights, and is not a runtime
dependency. The repository quality command is an independent project implementation,
not a reproduction of RepoQualityGate. Source metadata for [S20] is maintained in
`docs/sources/source-register.yaml`.
