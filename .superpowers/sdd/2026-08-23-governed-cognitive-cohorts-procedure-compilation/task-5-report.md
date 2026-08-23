# Task 5 Report: Statically Validated Procedure Compilation

## Status

Implemented Task 5 on `feat/governed-cognitive-cohorts-procedure-compilation` from base
`01bc7f0fd75b340e577305b8cf6718296ac8dd9f`.

## Implementation

- Added a closed procedure intermediate representation with five operations, bounded recovery
  directives, typed authorities, exact progress budget categories, catalog fact states, and
  `VALID`/`INVALID`/`INCONCLUSIVE` validation results.
- Added strict, frozen, extra-forbidden, versioned, bounded contracts and canonical hashes for
  candidate methods, declared artifacts, procedure steps, executable procedures, compilation
  results/records, method-direction outcomes, and compiled progress-plan bindings.
- Added direct-parse hash and cross-hash validation. A recomputed result hash cannot conceal a
  request-hash or compiler-identity contradiction with the retained procedure.
- Added the pure compiler and all sixteen static checks in fixed order. The compiler does not
  execute procedures and does not import application, storage, transaction, provider, network,
  subprocess, dynamic-import, model SDK, or training code.
- Added progress binding through the existing `ProgressSubtask`, `ProgressPlan`, and
  `calculate_progress()` contracts. Invalid and inconclusive compilation results retain their
  procedure/findings but raise `only a valid procedure can produce a progress plan`.

## Exact Files

- `src/super_scientist/domain/procedures/__init__.py`
- `src/super_scientist/domain/procedures/models.py`
- `src/super_scientist/domain/procedures/compiler.py`
- `src/super_scientist/domain/procedures/progress_binding.py`
- `tests/unit/procedures/test_compiler.py`
- `tests/unit/procedures/test_validation.py`
- `tests/unit/procedures/test_progress_binding.py`
- `tests/property/test_progress_dependencies.py`
- `.superpowers/sdd/2026-08-23-governed-cognitive-cohorts-procedure-compilation/task-5-report.md`

## RED, GREEN, and Final Verification

All commands used the required prefix:
`uv run --isolated --python C:\Users\evanh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe --extra dev`.

1. Baseline: `python -m pytest tests/unit/progress tests/property/test_progress_dependencies.py -v`
   passed: `15 passed`.
2. Required collection RED after adding Task 5 tests/imports:
   `python -m pytest tests/unit/procedures tests/property/test_progress_dependencies.py -v`
   failed during collection with four expected
   `ModuleNotFoundError: No module named 'super_scientist.domain.procedures'` errors.
3. First GREEN iteration of the same command collected `44` tests: `41 passed, 3 failed`.
   The failures identified per-step validator finding multiplicity and two invalid test fixtures;
   the fixtures were replaced with a valid recovery cycle and direct static-validator input.
4. Focused GREEN: the same command passed: `44 passed`.
5. Cross-hash adversarial RED:
   `python -m pytest tests/unit/procedures/test_validation.py::test_direct_parsing_rejects_a_rehashed_result_with_a_contradictory_request_hash -v`
   failed with `DID NOT RAISE ValidationError`.
6. Cross-hash GREEN: the same focused command passed: `1 passed`.
7. Final suite:
   `python -m pytest tests/unit/procedures tests/unit/progress tests/property/test_progress_dependencies.py -v`
   passed: `54 passed`.
8. Ruff: `python -m ruff check src/super_scientist/domain/procedures tests/unit/procedures`
   passed: `All checks passed!`.
9. Strict mypy: `python -m mypy src` passed: `Success: no issues found in 111 source files`.
10. `git diff --check` passed with no whitespace errors. Git emitted only its LF-to-CRLF
    working-copy warning for `tests/property/test_progress_dependencies.py`.

## Specification §9.5 Check Coverage

1. Schema/compiler support: unsupported request schema and compiler-version findings.
2. Unique IDs/order: duplicate step IDs, noncanonical step order, and noncanonical references.
3. Dependencies: unknown dependency and dependency-cycle findings.
4. Inputs: ancestor-produced, present-catalog, absent-catalog, and unknown-catalog behavior.
5. Producers: ambiguous artifact producer finding.
6. Tools: unavailable, unauthorized, and incomplete/unknown catalog findings.
7. Authority: governance/transaction/protected authority rejection.
8. Outputs: undefined declared output and missing referenced output findings.
9. Capabilities: only Task 3 `SATISFIED` plus `VERIFIED` assessments with verified assertion IDs
   pass; grounded failure is invalid and unknown evidence is inconclusive.
10. Validators: exact actor/version registration, absent registration, and unknown registration.
11. Completion: completion criteria, evidence requirements, and verifier requirements.
12. Resources: closed existing budget categories and exact aggregate reserve comparisons.
13. Termination: one target/outcome, bounded attempts, declared target, and acyclic recovery paths.
14. Operations: closed five-operation enum plus defensive unknown-operation finding.
15. Forbidden surfaces: extra-forbidden parsing and recursive forbidden-field check.
16. Progress mapping: generated canonical progress objects pass existing dependency/weight
    validation; cycle and weight failures remain existing progress-domain failures.

## Self-Review

- Correctness: error findings take precedence over unknown findings; no findings means `VALID`;
  findings and the complete `1..16` check ledger are deterministic.
- Safety/authority: compilation is pure, retains no executable command/import/URI/provider fields,
  and grants no write, transaction, governance, protected-evaluator, or protected-answer authority.
- Maintainability: models, static checks, and progress binding are separated; progress dependency,
  weight, and false-finish logic are not duplicated.
- Documentation precision gate: `PASS`; no unresolved `BLOCK` or `WARN` finding.

## Concerns

None. Durable transaction handling and current-compilation freshness enforcement remain the later
§9.6 handler's responsibility and were not added to this pure domain compiler task.
