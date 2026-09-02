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

## Round 5: opaque proposal transport for untrusted compilation results

### Problem

The Task 8 proposal sketch typed `RecordProcedureCompilation.compilation` as a
`ProcedureCompilationRecord`. `PROPOSAL_ADAPTER.validate_json()` therefore attempted to
construct the nested `ProcedureCompilationResult` before the Task 12 handler could call
`parse_untrusted_procedure_compilation_result()`. A schema-invalid result could expose a
private marker through Pydantic diagnostics, and the documented safe handler boundary
was unreachable.

### Design and expected behavior

- Add `OpaqueProcedureCompilationEnvelope` as a pure domain transport contract. The
  envelope carries compilation metadata plus base64-encoded canonical JSON bytes and
  their SHA-256 hash. It does not contain or validate a nested
  `ProcedureCompilationResult`.
- Bound decoded result JSON at 4 MiB. Before JSON decoding, scan decoded bytes with the
  existing iterative depth limit. Require valid UTF-8, exact canonical JSON bytes,
  canonical base64, and an exact byte hash. Base64 is transport encoding, not a
  confidentiality control.
- Extend `parse_untrusted_procedure_compilation_result()` to unwrap the opaque envelope
  through the same fixed safe error boundary. Add an exact
  `ProcedureCompilationRecord.build_from_untrusted_envelope()` factory for durable
  typed-record construction.
- In Task 8, type `RecordProcedureCompilation.compilation` as
  `OpaqueProcedureCompilationEnvelope`. The proposal adapter must never parse a nested
  `ProcedureCompilationResult`.
- In Task 8, expose `parse_untrusted_proposal_json()` as the only public serialized-
  proposal parser. The boundary applies fixed byte/depth limits and suppresses raw
  Pydantic diagnostics, including any rejected base64 payload.
- In Task 12, the handler must call
  `parse_untrusted_procedure_compilation_result(proposal.compilation)` before compiler
  recomputation, authority decisions, or record construction. Only the handler or a
  projection reached after that decision may use the safe record factory.
- Integrity reconstruction must normalize accepted envelopes through the safe factory;
  it must not treat an accepted proposal envelope as an already typed durable record.
- Preserve the request/receipt integrity rules, unknown-as-`INCONCLUSIVE`, deterministic
  progress recomputation, all sixteen checks, and later Task 12 ownership of repository
  resolution.

### Tests

- Parse a proposal-shaped model containing a schema-invalid but syntactically canonical
  result as an opaque envelope. Verify that proposal parsing succeeds and the public
  result parser returns the fixed safe failure without exposing the marker.
- Verify valid envelope-to-result and envelope-to-record normalization.
- Reject non-canonical, over-byte-limit, over-depth, and hash-mismatched envelopes before
  nested result parsing.
- Add an executable-plan consistency regression that requires the Task 8 proposal to use
  the opaque envelope and requires Task 12 safe normalization before recomputation.
- Run plan/documentation, complete procedure, Phase A, adversarial, formatting, lint,
  typing, dependency, raw-parser-consumer, and diff gates.

## Round 6: context-free proposal failure and complete-envelope normalization

### Problem

The Task 8 `parse_untrusted_proposal_json()` sketch raised its fixed error inside the
`except` block. `raise ... from None` hid the direct cause but retained the caught raw
validation error in `__context__`. In the implemented procedure contract,
`ProcedureCompilationRecord.build_from_untrusted_envelope()` validated the nested result
but read `compilation_id`, `created_at`, and `governing_policy_hash` from the supplied
model without fresh complete-envelope validation. A `model_copy()` mutation could make
the record builder expose a marker through a raw Pydantic error.

### Design and expected behavior

- Add `parse_untrusted_procedure_compilation_envelope()` as the only public parser for an
  untrusted envelope model, mapping, or JSON value. The parser fresh-validates the
  complete envelope and raises one fixed `ProcedureBoundaryValidationError` only after
  the caught-exception scope exits.
- When the supplied value is already an envelope model, serialize and fresh-validate all
  fields. Require exact equality after validation so `model_copy()` cannot bypass
  canonical normalization.
- Make `parse_untrusted_procedure_compilation_result()` normalize an envelope through the
  complete-envelope parser before unwrapping result bytes.
- Make `ProcedureCompilationRecord.build_from_untrusted_envelope()` normalize the
  complete envelope before it reads compilation ID, time, or policy metadata. The
  resulting record's canonical content hash binds those normalized metadata fields and
  the validated result.
- Change the Task 8 proposal-parser sketch to store the parsed value inside a suppressed
  exception scope, leave that scope, and only then raise its fixed error. Require both
  `__cause__` and `__context__` to be `None`.
- Preserve the opaque transport shape, byte/depth limits, result/request integrity,
  unknown-as-`INCONCLUSIVE`, all sixteen checks, and Task 12 repository ownership.

### Tests

- Execute the Task 8 parser function from the plan with an adapter that raises a
  marker-bearing error. Assert the public error has the fixed message, no marker, no
  structured error surface, and no cause or context.
- Mutate `compilation_id`, `created_at`, and `governing_policy_hash` independently through
  `model_copy()`. Assert both safe result parsing and safe record construction reject each
  mutation without a raw Pydantic error, marker, cause, context, or structured input.
- Verify valid envelope parsing and record construction remain exact, then run all
  procedure, Phase A, adversarial, formatting, lint, typing, dependency, documentation,
  and diff gates.

## Round 7: strict typed-input normalization and accepted-source resolution

### Problem

Pydantic serializers warn by default when a copied or constructed nested model contains
an invalid value. `compile_method()` could therefore emit a private marker in a warning
and later expose a raw validation error. The safe result parser hid its exception but
could emit the same marker warning. The Task 12 sketch also trusted receipt-shaped data
without specifying the repository operations that prove acceptance, exact source
content, snapshot identity, and freshness.

### Design and expected behavior

- Every public procedure compilation or safe parsing boundary serializes typed model
  inputs with `warnings=False`, strictly reparses the complete canonical value, and
  requires exact equality before reading fields or deriving output. A structurally
  invalid `model_copy()` or `model_construct()` value raises the boundary's fixed
  `ProcedureBoundaryValidationError` after leaving the caught-exception scope.
- This round supersedes Round 2's compiler behavior for schema-invalid copied receipt
  or profile cross-links: full strict round-trip rejection now occurs before the sixteen
  checks. The defensive checks remain available to `validate_procedure()` for a
  caller-supplied procedure/request pair, while schema-valid semantic failures still
  receive a complete deterministic report.
- `compile_method()` rejects structurally invalid typed bypasses before all compilation
  work. Structurally valid semantic failures, including unsupported compiler identity,
  impossible authority, unavailable registered tools, and unknown catalog facts, still
  produce deterministic reports with all sixteen checks.
- Task 10 provides focused accepted-source, capability-profile, catalog-source, and
  source-snapshot readers over exact transaction, audit, evidence, artifact, and
  cognitive records. Task 12 resolves every profile and catalog
  `AcceptedSourceReceiptRef` through those readers before recomputation, policy checks,
  acceptance, projection, or progress binding.
- Exact resolution compares proposal and audit IDs/hashes, source record ID/schema/hash,
  snapshot ID/hash, decoded catalog contents/completeness, retained profile contents,
  and the single current snapshot head. Any missing, duplicate, stale, or mismatched
  value fails closed and causes no compilation, binding, or progress write. The existing
  coordinator still retains the rejection decision and its audit event atomically.

### Tests

- Inject a marker into copied and constructed nested resource/result models. Capture
  warnings and require the fixed error, no marker warning, no raw structured error, and
  no cause or context at compilation, result parsing, request parsing, and envelope
  construction boundaries.
- Require the executable Task 12 plan to resolve every source kind through the focused
  Task 10 readers and to reject unresolved or stale sources before recomputation or any
  accept path.
- Preserve valid compilation, semantic invalid/inconclusive reports, receipt and request
  integrity, opaque proposal transport, deterministic progress recomputation, and all
  sixteen checks.
