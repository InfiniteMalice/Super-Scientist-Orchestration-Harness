# Phase A procedure contract hardening task spec

## Problem

The Phase A procedure compiler can currently treat caller-authored artifact, tool,
validator, and capability facts as authoritative. A caller can also replace an invalid
validation report with a rehashed `VALID` report and pass the result to
`procedure_to_progress_plan()`.

## Scope and design

- Add strict accepted-source receipt bindings. Each binding identifies the accepted
  receipt and source record by stable ID, schema version, canonical content hash, and
  source snapshot ID/hash.
- Bind artifact, tool, and validator catalog contents and completeness to one exact
  fixed snapshot. Bind each capability assessment to its retained capability profile,
  accepted profile receipt, and evidence snapshot; recompute the assessment from the
  retained profile.
- Keep repository resolution and proof that a receipt was actually accepted in the
  planned Task 12 application handler. Phase A validates the complete pure-data
  contract and all internal cross-links only.
- Retain the exact compilation request in `ProcedureCompilationResult` and make the
  progress mapper deterministically recompute the compiler result before mapping.
- Preserve the specification's sixteen checks. Check 15 scans both the request and a
  caller-supplied procedure; check 16 continues to delegate to the existing progress
  domain.
- Reject noncanonical or duplicate set-like procedure fields at strict parse boundaries
  and apply finite upper bounds to procedure-owned numeric/resource inputs.

## Non-goals and behavior preserved

- Do not add storage, repository, transaction, governance, provider, network, artifact
  write, or protected-answer authority to the procedure domain.
- Do not resolve receipts or freshness from persistence in Phase A.
- Preserve unknown catalog facts as `INCONCLUSIVE`; explicit spoofed or contradictory
  evidence bindings are `INVALID` or rejected during strict parsing.
- Preserve existing progress dependency, weight, completion, and false-finish behavior.

## Tests

- RED regressions for naked/spoofed catalog facts, forged capability assessments,
  cross-snapshot catalogs, and an impossible-governance procedure with a forged valid
  report.
- RED strict-parse tests for duplicate/noncanonical set-like fields and unbounded
  numeric/resource values.
- Focused cognition, procedures, progress, strict parsing, property, and adversarial
  authority suites; Ruff, strict mypy, forbidden dependency scan, and `git diff --check`.

## Risks

The procedure request/result schemas are unreleased Phase A contracts. The change is
intentionally breaking within that unreleased slice so later proposal and handler tasks
start from an authority-safe contract instead of carrying a compatibility alias for an
unsafe shape.
