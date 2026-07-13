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

Re-running `init` with the same active policy is idempotent. A migrated database is
initializable only when every kernel table is genuinely empty. If any registered policy,
transaction, audit event, evidence projection, claim version/head, or other durable
kernel state exists without the active pointer, `init` refuses both unchanged and
changed policy files. `audit verify` uses a storage-only path so it can report that
orphaned state even though normal runtime construction is unavailable. Re-running
`init` after changing an intact workspace policy is also rejected.

## Approval Boundary

A proposal may include an approval, but the admission engine rejects it with
`SELF_APPROVAL` when proposer and approver are not independent. Equal actor identities
are never independent. Two model actors are independent only when their
`(provider_id, model_id, adapter_id)` identities differ. A configuration-hash, prompt,
seed, decoding, or other configuration-only change on the same model and adapter is an
alias, not an independent approver. A distinct typed human or non-model actor remains
independent when its actor identity differs. Model agreement is not human approval.

The default policy names `governance_change` and `adapter_promotion` as requiring human
approval. Those proposal types and their approval workflow are not implemented in this
slice. The current CLI rejects active-policy changes instead of bypassing that future
human boundary. Operators must not mutate the SQLite policy tables directly.

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
