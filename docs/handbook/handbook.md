# Verified Behavior Handbook

> This is a deterministic derived index of human-authored behavior declarations.
> Python syntax verifies locations only and does not infer behavioral truth.

- Repository: `InfiniteMalice/Super-Scientist-Orchestration-Harness`
- Repository commit: `d2d4a5d64ea44d9e1d3dc65cbf1e44aac5907450`
- Manifest SHA-256: `645f32ec32a6702a92d28faf79c359830d0b8464a7d7aa006f505d2cb73a4e7a`
- Source-tree SHA-256: `b2185ba3a4b08ec8ff3409d581da8ad788e855c24e9596e08dc515e4de2e0430`

## Level 1: Summary

### `artifact-containment`

Persist and retrieve immutable artifacts without allowing static path escapes\.

### `protected-evaluation-separation`

Keep held\-out expected outputs physically and capability\-wise separate from candidates and ordinary storage\.

### `transactional-admission-coordination`

Coordinate governed domain proposals while preserving exact replay\, audit\, and atomicity\.

### `workspace-integrity-verification`

Reconcile the complete workspace against deterministic replay before durable operations\.

## Level 2: Contracts, dependencies, and governing rules

### `artifact-containment`

Contracts:

- Content\-addressed artifacts remain beneath the configured trusted root\.
- Static symlink and Windows reparse\-point escapes fail closed\.

Dependencies:

- None

Governing rule versions:

- `rule-version-path-containment-v1`

### `protected-evaluation-separation`

Contracts:

- Only strict hashes\, aggregates\, and checker outcomes cross the evaluator boundary\.
- Protected expected outputs remain outside the ordinary repository set and main database\.

Dependencies:

- `artifact-containment`

Governing rule versions:

- `rule-version-protected-holdout-separation-v1`

### `transactional-admission-coordination`

Contracts:

- Every durable proposal is normalized\, governed\, projected\, decided\, and audited in one transaction\.
- Exact replay resolves before new proposal execution and retains original authority\.

Dependencies:

- `workspace-integrity-verification`

Governing rule versions:

- `rule-version-transaction-authority-v1`

### `workspace-integrity-verification`

Contracts:

- Reconstruct authoritative state and compare every governed projection before mutation or replay\.
- Reject tampered\, orphaned\, causally impossible\, or unsupported durable records\.

Dependencies:

- None

Governing rule versions:

- `rule-version-workspace-integrity-v1`

## Level 3: Modules and symbols

### `artifact-containment`

- `src.super_scientist.providers.storage.artifacts` — `FileArtifactStore` (CLASS)

### `protected-evaluation-separation`

- `src.super_scientist.providers.storage.protected_evaluation` — `ProtectedEvaluationStore` (CLASS)

### `transactional-admission-coordination`

- `src.super_scientist.application.transactions.coordinator` — `TransactionCoordinator` (CLASS)

### `workspace-integrity-verification`

- `src.super_scientist.application.workspace_integrity` — `verify_workspace` (FUNCTION)

## Level 4: Exact commit, path, lines, and hashes

### `artifact-containment`

- Commit: `d2d4a5d64ea44d9e1d3dc65cbf1e44aac5907450`
  - Source: `src/super_scientist/providers/storage/artifacts.py:19` through line 150
  - Symbol: `FileArtifactStore`
  - File SHA-256: `da73930e63bef136ae522675f292249cfaf057d939983b3aecc8a39de6f0c0f5`
  - Symbol SHA-256: `7d3822ffd53432792c4e0be3bf3ec721ea23c3ec0a3f9f2c7c1b28fe5793deca`

Tests:

- `tests/integration/storage/test_artifacts.py`

### `protected-evaluation-separation`

- Commit: `d2d4a5d64ea44d9e1d3dc65cbf1e44aac5907450`
  - Source: `src/super_scientist/providers/storage/protected_evaluation.py:218` through line 349
  - Symbol: `ProtectedEvaluationStore`
  - File SHA-256: `00217e95f7a613167d8793427189c979c8ea1c5dae38e4bc4c62a2ed3db7bd0c`
  - Symbol SHA-256: `9ef472459f9f3e87fd1e5a8da2e0c5e5bf7af426de8224db50099723a93fddc2`

Tests:

- `tests/integration/storage/test_protected_evaluation_store.py`

### `transactional-admission-coordination`

- Commit: `d2d4a5d64ea44d9e1d3dc65cbf1e44aac5907450`
  - Source: `src/super_scientist/application/transactions/coordinator.py:249` through line 915
  - Symbol: `TransactionCoordinator`
  - File SHA-256: `a4baa66932953217d883ee3e4c6c5eeb3f1dc66219865eab8bbb501b63a11ae2`
  - Symbol SHA-256: `26a1ef9a1a7d19d151fb61ef28ba8a4c4619f891f33846d8edcaf1e40f4c9ce1`

Tests:

- `tests/integration/application/test_transaction_coordinator.py`

### `workspace-integrity-verification`

- Commit: `d2d4a5d64ea44d9e1d3dc65cbf1e44aac5907450`
  - Source: `src/super_scientist/application/workspace_integrity.py:655` through line 758
  - Symbol: `verify_workspace`
  - File SHA-256: `4bcfe06ea9d98b4b5d36a53b44a9afd3e6fc6daf539cd3ea6fdb7060ea56179f`
  - Symbol SHA-256: `4ed132831963ad8d400fbb3949db800999a421b6b4a92ae963681b6538941e77`

Tests:

- `tests/integration/application/test_workspace_integrity.py`
