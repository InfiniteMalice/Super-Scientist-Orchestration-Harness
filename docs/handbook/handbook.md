# Verified Behavior Handbook

> This is a deterministic derived index of human-authored behavior declarations.
> Python syntax verifies locations only and does not infer behavioral truth.

- Repository: `InfiniteMalice/Super-Scientist-Orchestration-Harness`
- Repository commit: `0a75ee7edc3c5a6679a2e510d69ef5482358c709`
- Manifest SHA-256: `8e9f062d10a82a6d6070b6a1d84f540881581121bb391e81f90587506652346d`
- Source-tree SHA-256: `66322516c4e278bb21898a9ebcb85cb67a73238ad8aa083917bcf75f2b8e5ce3`

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

- Commit: `0a75ee7edc3c5a6679a2e510d69ef5482358c709`
  - Source: `src/super_scientist/providers/storage/artifacts.py:19` through line 124
  - Symbol: `FileArtifactStore`
  - File SHA-256: `5502058494d8cf902bcb2b5964e2800c8ac952f57a5b39e3fba964732f35d2ca`
  - Symbol SHA-256: `ea47ec2c15b6b3c23d605840f3121e9dc0143c1a6cc4bff8a0e47ef75089ad3b`

Tests:

- `tests/integration/storage/test_artifacts.py`

### `protected-evaluation-separation`

- Commit: `0a75ee7edc3c5a6679a2e510d69ef5482358c709`
  - Source: `src/super_scientist/providers/storage/protected_evaluation.py:179` through line 286
  - Symbol: `ProtectedEvaluationStore`
  - File SHA-256: `7d0176de4a80142bfb49eb5204a965923ff79c744abf82378427ddfdbd2e87e7`
  - Symbol SHA-256: `a6d23256b3baeda6d547c41f12d182ee6d1ef55289690e59d7823774cde4f788`

Tests:

- `tests/integration/storage/test_protected_evaluation_store.py`

### `transactional-admission-coordination`

- Commit: `0a75ee7edc3c5a6679a2e510d69ef5482358c709`
  - Source: `src/super_scientist/application/transactions/coordinator.py:182` through line 618
  - Symbol: `TransactionCoordinator`
  - File SHA-256: `54a8be0625eaa17d8230a06270647e0de58bee9b99acd78ad7f1b25ca67e0875`
  - Symbol SHA-256: `ebef267a8c6ee889f7e66b7d4cce4d483e6cb2f7ab459f22a2cba4604cf4c279`

Tests:

- `tests/integration/application/test_transaction_coordinator.py`

### `workspace-integrity-verification`

- Commit: `0a75ee7edc3c5a6679a2e510d69ef5482358c709`
  - Source: `src/super_scientist/application/workspace_integrity.py:598` through line 647
  - Symbol: `verify_workspace`
  - File SHA-256: `b351259e05306dca2f7a1d76749707f1f90e39f018833be3d5607fd36c491f1d`
  - Symbol SHA-256: `19eaca47de650a42e2a66fe54a14983d39003b425f705450f83b8811a4a96876`

Tests:

- `tests/integration/application/test_workspace_integrity.py`
