# Phase A Harness Evidence Hardening Implementation Plan

> **For agentic workers:** Execute inline with strict red-green-refactor TDD. Do not add application, persistence, transaction, provider, network, subprocess, protected-answer, or training authority.

**Goal:** Make trace freshness, reward validity, and model-by-harness analysis depend on independently grounded evidence receipts instead of caller-authored booleans or mutually authenticating records.

**Architecture:** Add one shared immutable receipt triple (`record_id`, `schema_version`, `content_hash`). Keep traces observable-only, pass expected trace receipts separately to freshness calculation, require evidence-backed verifier/checker outcomes and complete reward-hacking diagnostic coverage, and make matrix analysis resolve exact freshness/assessment receipts against supplied validated snapshots. Preserve canonical hashing and fail-closed confounds.

**Tech Stack:** Python 3.12, strict Pydantic v2, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-08-23-governed-cognitive-cohorts-procedure-compilation-design.md` sections 11-14.

## Round 2: Resolution Attestations and Exact Cell Evidence Chains

### Problem

Round 1 introduced exact receipts but did not prove that receipt contents came from an independent
resolver. A caller can reconstruct a `TraceExpectation` from one attacked trace, declare arbitrary
verifier/checker and diagnostic statuses beside matching receipts, and reuse one valid assessment
for unrelated matrix coordinates. `HarnessExecutionTrace` also embeds the released `ResourceUsage`
DTO, whose integer fields do not have Phase A upper bounds.

### Design

1. Add immutable, canonically hashed resolution snapshots. A trace expectation must bind an accepted
   expectation-source receipt, an independent resolver identity, and the complete resolved receipt
   set. Freshness rejects an expectation when the expectation source is the same record as the trace
   or when its attestation does not canonically bind the snapshot.
2. Derive verifier and checker outcome status from independently resolved result snapshots. Add a
   resolved observable-evidence inventory and a coverage attestation that exactly binds every reward
   hacking family and evidence receipt. Findings bind their status to that coverage entry.
3. Make matrix validation join each cell receipt to one exact assessment/freshness/trace chain. The
   trace protocol, task set, model, harness, partition, verifier, checker, and assessment freshness
   must match the cell coordinate and protocol fields. Each accepted cell must have a distinct trace.
4. Revalidate embedded `ResourceUsage` integer fields through a Phase A wrapper with explicit upper
   bounds, without changing the released improvement DTO.

### Tests and invariants

- RED probes cover unattested/reconstructed expectation sources, equal trace mutation, arbitrary
  verification status, diagnostic status/evidence substitution, unrelated cell evidence,
  freshness/assessment substitution, and 1001-bit resource integers.
- Existing protected-evaluation, campaign, reward-observation separation, canonical hashing,
  24,512-comparison capacity, and 8-by-8 complete analysis behavior remain unchanged.
- Domain models only compare resolved records and receipts. They do not read repositories or gain
  transaction, provider, network, execution, promotion, or governance authority.

## Round 3: Handler-Resolved Evidence Inventory Capability

### Problem

Canonical receipts and attestations remain constructible data. A direct caller can remint the
expectation source, resolver, provenance, verifier/checker result sources, and diagnostic sources
after changing an attacked snapshot. Labels such as “accepted” and “independent” do not create a
trust boundary.

### Design

1. Add a strict, canonically hashed `ResolvedEvidenceInventory`. A later application handler builds
   this inventory only from committed receipt/snapshot records that the handler resolved. No domain
   helper derives the inventory from a trace, expectation, finding, or assessment.
2. Require `trace_freshness()` to receive the inventory as a separate argument. The function checks
   exact source, resolver, provenance, snapshot-kind, and snapshot-hash membership. The resulting
   freshness record embeds and hashes the inventory capability so direct parsing revalidates it.
3. Require `assess_reward_validity()` to receive the inventory separately. The inventory must cover
   verifier/checker result source, result, resolver, and observable evidence receipts; every
   diagnostic source, resolver, and observable evidence receipt; coverage provenance; and the reward
   observation evidence record. The assessment stores accepted receipts instead of arbitrary IDs.
4. Treat the inventory parameter as a capability boundary. A direct caller can construct ordinary
   domain data, but a handler must supply the previously resolved inventory before a current
   freshness or valid reward result can exist. The pure domain does not resolve repositories.

### Tests and invariants

- RED probes omit the separately supplied inventory and coordinate an expectation remint against an
  original inventory.
- RED probes change an `INVALIDATING` diagnostic to `CLEARED`, remint all adjacent records, and reuse
  the original inventory; reward validity must fail closed.
- Direct parse rejects freshness or reward records whose inventory hash, membership, snapshot kind,
  or accepted receipts are substituted.
- Per-cell evidence chains, 24,512 comparison capacity, 1,232-comparison 8-by-8 behavior, resource
  bounds, protected evaluation, canonical order, and the Phase A authority boundary remain unchanged.

## Round 4: Composable Bounds and Shared Matrix Snapshot Indexes

### Problem

The accepted reward components can produce more than 256 distinct evidence receipts even though
`RewardValidityAssessment.evidence_receipts` permits at most 256. Decimal reward and generation
values bound finiteness but not coefficient digits, exponent, or serialized size. Matrix evidence
chains recursively embed the same trace, freshness, and assessment snapshots, and analysis
revalidates those nested snapshots repeatedly; the required 8-by-8 grid is therefore too slow and
the 256-cell maximum is not operationally usable.

### Design

1. Separate the per-component evidence bound from the exact aggregate accepted-receipt bound. The
   aggregate bound equals the mathematical worst case admitted by verification, diagnostic,
   provenance, and observation components, so every accepted component set can construct an
   assessment. Test the exact aggregate boundary and one component overflow.
2. Validate each numeric `Decimal` with explicit coefficient-digit, exponent, and canonical-byte
   limits. Validate the canonical byte length of each reward observation and harness trace before
   computing its content hash. Every overflow raises a fixed validation error without interpolating
   attacker-controlled values.
3. Make `HarnessCellEvidenceChain` a compact canonical join of protocol, trace, freshness, and
   assessment receipts plus one coordinate. A later handler validates each full snapshot chain
   once and supplies a shared canonical `HarnessEvidenceSnapshotIndex` projection;
   `analyze_model_harness()` performs deterministic one-pass receipt joins. No chain or cell
   recursively embeds snapshots.
4. Preserve exact protocol/task/model/harness/partition/validator/checker joins, trace uniqueness,
   inventory-grounded freshness and reward validity, all declared comparisons, and fail-closed
   confounds. Add an 8-by-8 regression and a 256-cell/24,512-comparison performance gate with a
   documented generous wall-clock threshold.

### TDD and verification

- [x] Witness RED for 24 evidence receipts per diagnostic exceeding the old assessment bound.
- [x] Witness RED for oversized decimal coefficient, exponent, canonical bytes, reward bytes, and
  trace bytes.
- [x] Witness RED for compact receipt-only chain construction and the shared snapshot arguments.
- [x] Record the current 8-by-8 baseline and witness the maximum-shape runtime test fail or time out
  against recursive chains.
- [x] Implement the minimal aggregate, numeric, byte, and shared-index contracts.
- [x] Run focused harness, campaign, protected leakage, strict parsing, Phase A, Ruff, strict mypy,
  authority scan, `git diff --check`, and both performance probes.

## Round 5: Strict Compact-Projection Revalidation

### Problem and invariant

Pydantic preserves the concrete class when `model_copy()` or `model_construct()` bypasses model
validators. The round-4 projection methods treated an exact Python class as proof of validation,
so a copied freshness or assessment status, or a nested copied trace mutation, could retain its old
canonical hash and enter the compact index.

At each trusted full-to-compact projection boundary, the domain must serialize every supplied full
record to Python data and strictly parse that data back into its declared domain type. Canonical
model validators must recompute IDs and content hashes before the projection copies any receipt or
status. Projection failures must use fixed errors and must not include attacker-controlled values.
Matrix analysis must continue to consume compact records and must not recursively validate full
snapshots.

### TDD and verification

- [x] Witness RED for `model_copy()` changing `TraceFreshness.status` to `CURRENT` while retaining
  the old hash.
- [x] Witness RED for `model_construct()` changing `RewardValidityAssessment.status` to `VALID`
  while retaining the old hash.
- [x] Witness RED for a copied `HarnessExecutionTrace` with removed context artifacts and its old
  content hash.
- [x] Strictly round-trip protocol, coordinate, trace, freshness, and assessment at projection.
- [x] Preserve fixed safe projection errors, one-time analysis joins, and both performance limits.
- [x] Run focused harness/campaign/protected/strict parsing, the Phase A-compatible suite, Ruff,
  strict mypy, authority scan, public import probe, and `git diff --check`.

## Round 6: Guidance/Matrix Composition Bounds and Exact Evidence Sets

### Problem and invariant

Guidance metrics, evaluation budgets, resource usage, resource deltas, and count deltas retain
unbounded numeric or collection widths. Guidance and matrix hash-bearing records also lack outer
canonical-byte limits. A valid field count can therefore carry impractical decimal coefficients,
integers, tool inventories, or aggregate serialized payloads. Separately, matrix analysis bounds
the protocol grid but not its caller-supplied `evidence_chains` tuple and does not require exact
receipt-set equality across cells, chains, and the shared index.

Phase A wrappers must strictly reconstruct released `EvaluationBudget` and `ResourceUsage` DTOs
without modifying those released contracts. All Phase A decimals must share coefficient, exponent,
and canonical-byte bounds. Every Phase A resource/count integer and tool inventory must be bounded.
Every guidance/matrix hash-bearing outer record must reject an oversized canonical payload before
hashing. Matrix validation must inspect collection lengths before validating elements and must emit
no comparisons unless cell, chain, and index chain-receipt sets are exact.

### TDD and verification

- [x] Witness RED for 10,000-digit task/budget decimals, 10,001-bit budget/resource/delta integers,
  1,000 tools, and copied released DTOs that retain bypassed values.
- [x] Add exact accepted decimal, integer, tool-count, and aggregate-record near-boundary probes.
- [x] Witness RED for 300 surplus ignored evidence chains and bounded duplicate/missing/surplus
  chain-receipt set mismatches.
- [x] Centralize numeric/resource bounds and add a Phase A `EvaluationBudget` wrapper without
  changing released DTOs.
- [x] Bound guidance/matrix canonical records while preserving the 256-cell and 24,512-comparison
  valid maximum.
- [x] Run focused harness/campaign/protected/strict parsing, Phase A, static/authority gates, and
  near-limit maximum-shape performance.

## Round 7: Matrix Cartesian Preflight

### Problem and invariant

`ModelHarnessProtocol` currently constructs the declared model-by-harness-by-partition Cartesian
tuple in an after-validator. Pydantic can therefore deeply validate caller-supplied nested records,
and the after-validator can begin coordinate allocation, before the protocol rejects a grid whose
declared axis product exceeds the 256-cell limit.

Before either build or direct parsing validates nested protocol records, the protocol payload must
multiply the three raw outer collection lengths with Python integers. If the product exceeds 256,
validation must stop with the fixed error `model-harness Cartesian grid exceeds 256 cells`. The
valid 256-cell boundary and the complete 24,512-comparison maximum must remain unchanged.

### TDD and verification

- [x] Witness RED for both public construction paths with 256 models, 256 harnesses, five
  partitions, and a four-coordinate supplied grid.
- [x] Add an O(1) before-validator that does not iterate axes or construct coordinates.
- [x] Extend the same guard to strict-false list and JSON payloads before their coercive parsing
  path can materialize coordinates.
- [x] Preserve valid maximum-shape analysis and run focused, Phase A, static, and authority gates.

## Global Constraints

- Preserve protected-evaluation and `HarnessCampaign` behavior.
- A domain contract may compare evidence but may not resolve a repository receipt; later handlers own receipt resolution.
- A trace stores observed runtime state only. `trace_freshness()` receives a separate `TraceExpectation` made from independently resolved receipt triples.
- A valid reward requires current freshness, successful verifier and checker outcome evidence, and exactly one completed diagnostic for each closed reward-hacking family.
- A matrix cell stores exact freshness and reward-assessment receipts. Analysis receives validated snapshots and emits no comparison if a receipt is missing, mismatched, stale, invalid, or inconclusive.
- The 256-cell protocol bound implies at most 24,512 declared comparisons; analysis must accept that worst case and the required 8-by-8 probe.
- Canonical order and content hashes must bind every new receipt and evidence field.

### Task 1: Independent trace expectations and numeric bounds

**Files:**
- Create: `src/super_scientist/domain/harness_eval/receipts.py`
- Modify: `src/super_scientist/domain/harness_eval/traces.py`
- Modify: `src/super_scientist/domain/harness_eval/__init__.py`
- Test: `tests/unit/harness_eval/test_harness_security_contracts.py`
- Test: `tests/unit/harness_eval/test_traces.py`

**Interfaces:**
- Produce `EvidenceReceipt`, `TraceExpectation`, `trace_freshness(expectation, trace)`, and `trace_freshness_receipt()`.
- `HarnessExecutionTrace` contains `observed_binding` only.

- [ ] Write a regression that mutates the observed protocol/task/environment receipts consistently and proves the independently held expectation still returns `STALE`.
- [ ] Run the focused regression and verify RED because `EvidenceReceipt` and `TraceExpectation` do not exist.
- [ ] Implement the receipt and expectation contracts, remove `expected_binding` from the trace, and update freshness hashing.
- [ ] Add upper bounds for artifact sizes, transformation/tool/environment sequences, token metadata, and schema-version integers.
- [ ] Run trace and strict-parsing tests to GREEN.

### Task 2: Evidence-backed reward validity and complete diagnostics

**Files:**
- Modify: `src/super_scientist/domain/harness_eval/rewards.py`
- Modify: `src/super_scientist/domain/harness_eval/traces.py`
- Test: `tests/unit/harness_eval/test_harness_security_contracts.py`
- Test: `tests/unit/harness_eval/test_rewards.py`
- Test: `tests/adversarial/test_protected_holdout_leakage.py`

**Interfaces:**
- Produce `VerificationOutcomeEvidence`, `VerificationOutcomeStatus`, and `reward_validity_receipt()`.
- Change `assess_reward_validity()` to consume a separate `TraceExpectation` and exact verifier/checker outcome evidence; remove `verifier_succeeded`.

- [ ] Write regressions proving a bare verifier boolean is rejected and missing diagnostic families cannot yield `VALID`.
- [ ] Run those regressions and verify RED against the old signature and empty-finding behavior.
- [ ] Add exact verifier/checker identity/result receipts and require all ten diagnostic families once in enum order.
- [ ] Recompute assessment IDs, reasons, evidence IDs, and hashes from the new inputs.
- [ ] Run reward and leakage tests to GREEN.

### Task 3: Receipt-bound matrix evidence and complete bounded analysis

**Files:**
- Modify: `src/super_scientist/domain/harness_eval/matrix.py`
- Test: `tests/unit/harness_eval/test_harness_security_contracts.py`
- Test: `tests/unit/harness_eval/test_model_harness_matrix.py`

**Interfaces:**
- Replace cell booleans with `trace_freshness_receipt` and `reward_validity_receipt`.
- Change `analyze_model_harness()` and `validate_complete_matched_grid()` to consume validated `TraceFreshness` and `RewardValidityAssessment` snapshots.

- [ ] Write a receipt-spoof regression and an 8-by-8 analysis regression.
- [ ] Run both and verify RED because cells still trust booleans and analysis rejects 1,232 comparisons.
- [ ] Resolve exact receipts against validated snapshots and add typed mismatch/stale/invalid confounds.
- [ ] Set the comparison collection bound to the proven 24,512 worst case while retaining the 256-cell grid bound.
- [ ] Run matrix and campaign compatibility tests to GREEN.

### Task 4: Remaining bounds, verification, and audit report

**Files:**
- Modify: `src/super_scientist/domain/harness_eval/guidance.py`
- Test: `tests/unit/harness_eval/test_guidance.py`
- Create outside the worktree only as requested: `C:/Users/evanh/Documents/Codex/work/ss-oh-030/.superpowers/sdd/2026-08-23-governed-cognitive-cohorts-procedure-compilation/phase-a-harness-fix-report.md`

- [ ] Write boundary regressions for recovery attempt zero/maximum/overflow and trace numeric overflow.
- [ ] Run them and verify overflow is RED.
- [ ] Bound recovery attempts by `MAX_EVALUATION_ITEMS` and preserve event ordering/hash identity.
- [ ] Run focused harness, campaign, protected leakage, strict parsing, Phase A, Ruff, strict mypy, authority scan, and `git diff --check`.
- [ ] Self-review correctness, readability, safety, documentation precision, and mutation resistance.
- [ ] Write the requested report, commit the isolated branch, and return the SHA and exact checks.
