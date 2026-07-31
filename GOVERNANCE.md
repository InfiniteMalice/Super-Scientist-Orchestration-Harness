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

## Quality Policy Protection

`scientist-harness quality-gate` has a source-controlled registry of exactly eight
checks: format, lint, types, tests, security, dependencies, build, and package. The
runner accepts no arbitrary command, path, check selection, skip, or threshold. JSON is
an output mode only. The test command fixes branch coverage at 90 percent, and CI invokes
the same installed command.

Changes to the registry, `pyproject.toml`, or the CI workflow are protected as reviewed
source changes. This slice does not yet persist a runtime quality-policy hash or provide
a governance transaction for quality-policy changes; repository review and CI are the
implemented boundary.

The local RepoQualityGate skill informed the development-time discipline [S20]. It is
not vendored or imported, has unknown redistribution rights, and is not a runtime
dependency. The repository quality command is an independent project implementation,
not a reproduction of RepoQualityGate. Source metadata for [S20] is maintained in
`docs/sources/source-register.yaml`.
