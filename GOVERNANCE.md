# Kernel Governance

## Active Policy

`scientist-harness init` loads `governance-policy.json`, validates it as a frozen typed
policy, canonicalizes it, and computes a SHA-256 policy hash. The policy snapshot is an
append-only SQLite record; `governance_state` holds its active hash. Application
submissions compare their configured snapshot with that durable active hash before
admission. Missing, malformed, altered, or mismatched policy state fails closed.

Re-running `init` with the same policy is idempotent. Re-running it after changing the
policy file is rejected. A policy file edit therefore cannot silently alter active
rules.

## Approval Boundary

A proposal may include an approval, but the admission engine rejects it with
`SELF_APPROVAL` when proposer and approver are not independent. Equal actor identities
are never independent. Two model identities also require complete, differing provider,
model, adapter, and configuration identity; model agreement is not treated as human
approval.

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
