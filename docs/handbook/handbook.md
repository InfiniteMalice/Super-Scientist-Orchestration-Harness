# Verified Behavior Handbook

> This is a deterministic derived index of human-authored behavior declarations.
> Python syntax verifies locations only and does not infer behavioral truth.

- Repository: `InfiniteMalice/Super-Scientist-Orchestration-Harness`
- Repository commit: `17934d0858471409233fa9d90525a782919b8830`
- Manifest SHA-256: `e303f1206278b590533f43d10c27229a1a98c7984278c6bf67ec3086dd0b5414`
- Source-tree SHA-256: `3f38dc3b153dd055318f3ed0393021db73d58fb7cac09d1cef56db31539304de`

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

- Commit: `17934d0858471409233fa9d90525a782919b8830`
  - Source: `src/super_scientist/providers/storage/artifacts.py:19` through line 150
  - Symbol: `FileArtifactStore`
  - File SHA-256: `da73930e63bef136ae522675f292249cfaf057d939983b3aecc8a39de6f0c0f5`
  - Symbol SHA-256: `7d3822ffd53432792c4e0be3bf3ec721ea23c3ec0a3f9f2c7c1b28fe5793deca`

Tests:

- `tests/integration/storage/test_artifacts.py`

### `protected-evaluation-separation`

- Commit: `17934d0858471409233fa9d90525a782919b8830`
  - Source: `src/super_scientist/providers/storage/protected_evaluation.py:218` through line 349
  - Symbol: `ProtectedEvaluationStore`
  - File SHA-256: `00217e95f7a613167d8793427189c979c8ea1c5dae38e4bc4c62a2ed3db7bd0c`
  - Symbol SHA-256: `9ef472459f9f3e87fd1e5a8da2e0c5e5bf7af426de8224db50099723a93fddc2`

Tests:

- `tests/integration/storage/test_protected_evaluation_store.py`

### `transactional-admission-coordination`

- Commit: `17934d0858471409233fa9d90525a782919b8830`
  - Source: `src/super_scientist/application/transactions/coordinator.py:182` through line 638
  - Symbol: `TransactionCoordinator`
  - File SHA-256: `5e040b3572d732658e7982e37bac39a0330ced5cb568d4d613e39e96e65c13c7`
  - Symbol SHA-256: `42d9a729a86ea7518168da907f669074709632b6412a9a9db2254fc50b35a9b0`

Tests:

- `tests/integration/application/test_transaction_coordinator.py`

### `workspace-integrity-verification`

- Commit: `17934d0858471409233fa9d90525a782919b8830`
  - Source: `src/super_scientist/application/workspace_integrity.py:621` through line 682
  - Symbol: `verify_workspace`
  - File SHA-256: `30431544df4d1c2983aa6320acb35e09aacbeec73640d3fa4e556d7f0cd85d84`
  - Symbol SHA-256: `fba431a2859944dc753e83b65da42e8a4a8ec6a00dc2ed719c24926b853d67bc`

Tests:

- `tests/integration/application/test_workspace_integrity.py`
