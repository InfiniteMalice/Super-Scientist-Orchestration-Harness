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

## Round 2: recursive integrity and retention bounds

### Problem

Programmatic construction with `model_copy()` can retain a stale nested capability
profile hash while recomputing the enclosing grounded-assessment hash. The compiler
must not issue `VALID` for a request that would fail its strict JSON parse boundary.
The request contract also lacks a canonical UTF-8 byte limit, while the result stores
the request under a character limit. A large accepted request can therefore perform
compilation work and then fail during result construction.

### Design and expected behavior

- The compiler verifies that every retained capability profile hash canonically
  addresses the retained profile body.
- Before compilation work, the compiler serializes the request canonically and enforces
  the request byte limit. Before the compiler issues `VALID`, the compiler reparses the
  canonical bytes with strict validation and requires the parsed request to equal the
  supplied request. An otherwise-valid request that bypassed validation fails with one
  fixed `ValueError` instead of receiving `VALID`. A spoofed evidence binding that the
  sixteen checks detect continues to receive a deterministic `INVALID` report.
- `ProcedureCompilationRequest` and the retained `request_json` use the same 65,536-byte
  canonical UTF-8 limit. Every normally accepted request is therefore retainable and
  deterministically recompilable.
- Catalog receipt hashes remain recursively checked, and catalog source hashes remain
  bound to the complete serialized catalog entries and completeness flag.

### Tests and preserved behavior

- Reproduce the stale-profile exploit by promoting an `UNKNOWN` profile assertion to
  `VERIFIED` through `model_copy()` while retaining its stale profile hash.
- Reject an oversized multibyte request and accept the largest constructed multibyte
  request below the canonical byte limit.
- Reject an oversized request that bypasses model validation before compilation work.
- Preserve valid compilation, unknown-as-`INCONCLUSIVE`, sixteen checks, deterministic
  progress recomputation, and the Phase A repository-authority boundary.

## Round 3: sanitized procedure boundary failures

### Problem

Procedure request reparsing can fail with a Pydantic `ValidationError` that retains the
rejected input in `errors()`. Raising a fixed `ValueError` inside the corresponding
`except` block does not discard that object: explicit chaining stores it in `__cause__`,
and `raise ... from None` still stores it in `__context__`. A forged private marker can
therefore survive behind the fixed public error.

### Design and expected behavior

- `ProcedureCompilationResult.parse_request()`, compiler canonical revalidation, and
  progress deterministic revalidation convert parsing or validation failures to fixed
  `ValueError` messages only after leaving the `except` block.
- Boundary errors have no exception cause or context. Their string, representation, and
  any available structured-error surface do not contain rejected request data.
- Result JSON syntax validation raises its fixed validation message outside the JSON
  decoder's `except` block. Procedure models hide input values in formatted Pydantic
  validation errors where Pydantic supports that behavior.
- Valid requests, receipt integrity, deterministic recompilation, progress mapping,
  unknown-as-`INCONCLUSIVE`, and the sixteen procedure checks do not change.

### Tests

- Inject a private marker into schema-invalid and syntactically invalid retained request
  JSON, then inspect the direct request-parse error.
- Inject a private marker into compiler canonical revalidation and progress deterministic
  revalidation, then inspect `str()`, `repr()`, `__cause__`, `__context__`, and structured
  errors when the exception type exposes them.
- Run the complete procedure, Phase A, adversarial, formatting, lint, typing, dependency,
  documentation, and diff gates.

## Round 4: safe untrusted result parsing and JSON complexity limits

### Problem

Pydantic's native `ValidationError.errors()` retains rejected input by default even when
`hide_input_in_errors=True` hides that input in formatted messages. Direct callers of
`ProcedureCompilationResult.model_validate*()` can therefore recover private
`request_json`. A request with 10,000 nested JSON arrays also raises raw recursion or
serializer-depth errors before every procedure boundary can return its fixed failure.

### Design and expected behavior

- Export `parse_untrusted_procedure_compilation_result()` as the only public parser for
  untrusted compilation-result dictionaries, JSON, or model instances. The parser
  raises `ProcedureBoundaryValidationError` with one fixed message and no cause,
  context, Pydantic `errors()` method, or rejected input.
- Keep Pydantic constructors as internal trusted-construction and diagnostic contracts.
  Do not claim that Pydantic's default structured errors are redacted.
- Require progress mapping and the planned Task 12 handler to pass supplied compilation
  results through the safe parser before using them.
- Scan retained request JSON iteratively before Python or Pydantic decoding. Reject JSON
  above the fixed depth limit without recursively materializing the value.
- Convert `RecursionError`, `OverflowError`, `MemoryError`, and local serialization or
  validation failures to fixed domain errors after leaving the caught-exception scope.
- Preserve valid compilation, request/receipt integrity, deterministic recomputation,
  unknown-as-`INCONCLUSIVE`, and all sixteen checks.

### Tests

- Probe default `errors()` on the public boundary error and require that the error is not
  a Pydantic error and exposes no marker.
- Send 10,000 nested arrays containing a private marker through untrusted result parsing,
  retained-request parsing, progress mapping, and compiler canonicalization. Every path
  must return its fixed safe failure with no cause, context, or marker.
- Parse a valid compiled result from both a dictionary and canonical JSON through the
  public boundary.
- Run the complete procedure, Phase A, adversarial, formatting, lint, typing, dependency,
  documentation, and diff gates.
