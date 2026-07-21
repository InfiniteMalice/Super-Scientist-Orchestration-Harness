# Verified Behavior Handbook

> This is a deterministic derived index of human-authored behavior declarations.
> Python syntax verifies locations only and does not infer behavioral truth.

- Repository: `InfiniteMalice/Super-Scientist-Orchestration-Harness`
- Repository commit: `c5de7d14f530e172216f35d8a5453057aa257f61`
- Manifest SHA-256: `8b10bd7fade65c8b1e81644924f71834bd941af9d2c281361a8cee3adc9b265b`
- Source-tree SHA-256: `584c319923319f08ec2e1dfff0b0007d80901f30d46de7b1ff2be04348ec5614`

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

- Commit: `c5de7d14f530e172216f35d8a5453057aa257f61`
  - Source: `src/super_scientist/providers/storage/artifacts.py:19` through line 124
  - Symbol: `FileArtifactStore`
  - File SHA-256: `5502058494d8cf902bcb2b5964e2800c8ac952f57a5b39e3fba964732f35d2ca`
  - Symbol SHA-256: `ea47ec2c15b6b3c23d605840f3121e9dc0143c1a6cc4bff8a0e47ef75089ad3b`

Tests:

- `tests/integration/storage/test_artifacts.py`

### `protected-evaluation-separation`

- Commit: `c5de7d14f530e172216f35d8a5453057aa257f61`
  - Source: `src/super_scientist/providers/storage/protected_evaluation.py:218` through line 325
  - Symbol: `ProtectedEvaluationStore`
  - File SHA-256: `5991f6326016034769f1dc33216c1ab47f53b3d787e00aba7559e8ecb6ab1dfe`
  - Symbol SHA-256: `a6d23256b3baeda6d547c41f12d182ee6d1ef55289690e59d7823774cde4f788`

Tests:

- `tests/integration/storage/test_protected_evaluation_store.py`

### `transactional-admission-coordination`

- Commit: `c5de7d14f530e172216f35d8a5453057aa257f61`
  - Source: `src/super_scientist/application/transactions/coordinator.py:175` through line 590
  - Symbol: `TransactionCoordinator`
  - File SHA-256: `75fa7325ad84cbff7db9effc5d001e4c33c140cee23e1b99ccdad0db444d9b7c`
  - Symbol SHA-256: `7adb09cac8b17a32a516a04dd838502361a407b6d2168053224d2ade681cac9a`

Tests:

- `tests/integration/application/test_transaction_coordinator.py`

### `workspace-integrity-verification`

- Commit: `c5de7d14f530e172216f35d8a5453057aa257f61`
  - Source: `src/super_scientist/application/workspace_integrity.py:598` through line 647
  - Symbol: `verify_workspace`
  - File SHA-256: `b351259e05306dca2f7a1d76749707f1f90e39f018833be3d5607fd36c491f1d`
  - Symbol SHA-256: `19eaca47de650a42e2a66fe54a14983d39003b425f705450f83b8811a4a96876`

Tests:

- `tests/integration/application/test_workspace_integrity.py`
