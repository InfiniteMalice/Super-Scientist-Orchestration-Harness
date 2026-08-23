# Phase A Harness Evidence Hardening Implementation Plan

> **For agentic workers:** Execute inline with strict red-green-refactor TDD. Do not add application, persistence, transaction, provider, network, subprocess, protected-answer, or training authority.

**Goal:** Make trace freshness, reward validity, and model-by-harness analysis depend on independently grounded evidence receipts instead of caller-authored booleans or mutually authenticating records.

**Architecture:** Add one shared immutable receipt triple (`record_id`, `schema_version`, `content_hash`). Keep traces observable-only, pass expected trace receipts separately to freshness calculation, require evidence-backed verifier/checker outcomes and complete reward-hacking diagnostic coverage, and make matrix analysis resolve exact freshness/assessment receipts against supplied validated snapshots. Preserve canonical hashing and fail-closed confounds.

**Tech Stack:** Python 3.12, strict Pydantic v2, pytest, Ruff, strict mypy.

**Spec:** `docs/superpowers/specs/2026-08-23-governed-cognitive-cohorts-procedure-compilation-design.md` sections 11-14.

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
