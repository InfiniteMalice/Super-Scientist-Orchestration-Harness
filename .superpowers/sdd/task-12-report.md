# Task 12 Delivery Report

## Outcome

Task 12 is complete on `feat/governed-adaptation-and-harness-evolution`, based on
`2504c038006fcaeab75dd3bd2cb11deb4078acf0`. It adds the domain-neutral, governed
hypothesis/model/checker/revision loop without adding arbitrary execution authority,
dependencies, a migration, CLI behavior, network access, or runtime plugin discovery.

The implementation contributes the eight fixed proposal kinds required by the task:

1. `propose_hypothesis_version`
2. `register_executable_model`
3. `register_verification_mechanism`
4. `record_simulation_result`
5. `record_verification_result`
6. `record_counterexample`
7. `revise_hypothesis`
8. `admit_hypothesis`

The required commit subject is `feat: add safe hypothesis revision loop`.

## Test-driven implementation evidence

- The first exact Task 12 run was RED during collection with four missing-module errors for the
  absent hypothesis domain and application packages. No production implementation existed at
  that point.
- Strict immutable contracts, discriminated checker/result unions, the fixed simulator registry,
  transfer fixtures, and the adversarial execution-boundary scan made the first non-integration
  slice GREEN at **52 passed**.
- The first application integration run was RED because the fixed transaction-handler module did
  not yet exist. After adding the coordinator handlers and projections, all six then-authored
  integration cases collected and the end-to-end loop became GREEN.
- Receipt, authority, chronology, storage-conflict, revision, admission, capability, and replay
  cases were then added through repeated RED/GREEN loops. The pre-coverage-hardened focused Task
  12 suite reached **66 passed**.
- Full repository collection exposed a real pytest module-name collision and a stale fixed
  `ProposalKind` assertion. A leaf test package and an assertion covering all eight new kinds made
  collection clean at **1,379 tests** and restored the full shard.
- The first combined full-inventory report was a real coverage failure at **89.065592%**. No
  coverage pragma, exclusion, dead test, or assertion-free filler was added. Meaningful contract,
  policy, runtime-boundary, coordinator, rejection, replay, projection, and storage assertions
  raised exact combined branch coverage to **90.406742%**.
- The final post-format affected Task 12 suite is **86 passed in 147.24s**.

## Implemented boundaries

### Domain contracts and safe execution

- Added strict frozen `HypothesisSpec`, `ExecutableModelSpec`, numeric input/output,
  `SimulationResult`, verification mechanism/result unions, `CounterexampleRecord`,
  `RevisionRecord`, receipt references, and `HypothesisAdmissionDecision` contracts.
- Formal verifiers, deterministic checkers, and learned judges retain distinct mechanism and
  provenance categories. A learned result cannot present itself as formal or deterministic.
- `METADATA_ONLY` requires complete inert artifact metadata and never executes.
- `BUILTIN_DETERMINISTIC_SIMULATOR` can resolve only `thermal-chamber-v1` and
  `exponential-decay-v1` from an immutable source-controlled registry.
- Strict schemas, deterministic seeds, positive bounded steps, bounded state bytes, and finite
  numeric records are revalidated at execution. Unknown IDs, invalid ranges, schema drift,
  non-reproducible output, and metadata-only execution reject.
- The runtime path contains no filesystem, network, subprocess, dynamic-import, reflection
  dispatch, `eval`, or `exec` authority.

### Transaction, receipt, and replay authority

- Added eight focused handlers to the fixed coordinator router. Each live handler receives one
  read facade and exactly one typed write capability.
- Downstream stages bind exact accepted proposal hashes plus audit event IDs and hashes. The
  database audit sequence, never caller-authored timestamps, establishes stage chronology.
- All mutation stages require the exact classification
  `RESEARCH_PROCESS / HUMAN_IN_LOOP / RUN_LOCAL /
  INDEPENDENT_DETERMINISTIC_CHECK / CONTROLLED_EXPERIMENT / EMPIRICAL_MEASUREMENT`, a matching V2
  policy requirement, active-policy binding, and independent human approval.
- Stable-key reuse with changed content is an audited conflict. Exact duplicate content is
  accepted without duplicating an authoritative projection.
- Workspace verification builds a receipt index from transaction/audit history, replays accepted
  hypothesis transactions through the same fixed handlers in audit order, and compares exact
  rebuilt append-only records and hypothesis heads with storage.
- Deleted heads, missing or forged receipts, altered records, invalid chronology, and projection
  disagreement fail closed.

### Verification, counterexamples, revision, and admission

- Verification binds the exact retained candidate, mechanism, optional model, simulations,
  evidence, actor, policy, category, search evidence, and committed chronology.
- A no-counterexample search is retained as verification evidence. A `CounterexampleRecord`
  represents a found counterexample, must bind failed retained search results, and blocks that
  version from admission.
- Revision preserves the predecessor, accepts only failed or abstained triggers, names considered
  counterexamples, advances one contiguous version, and explicitly changes predictions and
  falsification conditions.
- Admission alone writes the hypothesis head. It requires `TRANSFER_VALIDATED`, at least one
  built-in deterministic model, passing verification, retained deterministic counterexample
  search with none found, complete revision lineage, no candidate counterexample, exact evidence
  and policy provenance, exact admitted primitive heads, an accepted self-improvement measurement,
  a passed evaluator audit, independent human authority, and exact rollback binding.
- Confidence, self-consistency, learned agreement, proposer approval, or caller timestamps cannot
  substitute for admission authority.

### Transfer evaluation and documentation

- The offline matched-condition evaluation recomputes correctness, checker accuracy, false
  admission, diversity, revision utility, unsupported-model rate, abstention, cost, transfer, and
  regression from actual attempts.
- It covers independently authored thermal-chamber, exponential-decay, immutable synthetic
  equipment-incident, and in-memory software-maintenance fixtures without importing code or adding
  execution authority.
- Added `docs/hypothesis-model-checker-loop.md` and updated `ARCHITECTURE.md`, `GOVERNANCE.md`, and
  `SECURITY.md` to state the implemented authority, execution, admission, replay, and residual-risk
  boundaries accurately.

## Verification results

### Focused and adjacent

- Initial adjacent Task 10/11 baseline: **19 passed**.
- Final post-format Task 12 affected suite: **86 passed in 147.24s**.
- Adjacent representation, policy, migration, workspace-integrity, coordinator, and governance
  transition checkpoint before the full inventory: **171 passed in 153.14s**.

### Bounded full inventory

The complete non-overlapping inventory ran after the production implementation and before the
test-only coverage-hardening additions:

- Unit, adversarial, e2e, and evaluation: **773 passed in 30.95s**.
- Application integration: **262 passed in 720.21s**.
- CLI and storage integration: **136 passed, 3 skipped in 203.16s**.
- Property suite: **205 passed in 474.31s**.
- Combined result: **1,376 passed, 3 skipped**.

No production source changed after that full inventory. The subsequent changes were meaningful
test-only coverage hardening, followed by the complete 86-test affected-suite rerun above.

### Coverage and quality

- Combined full-inventory plus final affected-test branch coverage: **90.406742%** over **10,859
  statements and 2,786 branches**; `coverage report --fail-under=90` passed.
- `src/super_scientist/application/transactions/hypotheses.py` itself reports **90%** branch-aware
  coverage.
- Repository-wide Ruff lint: **all checks passed**.
- All **19 changed Python files** are Ruff-format clean. A repository-wide format claim is not
  made because the branch retains the previously documented untouched baseline format backlog.
- Strict mypy: **success, 80 source files**.
- `git diff --check`: passed, with only configured LF-to-CRLF notices.
- Bandit: passed with only the pre-existing B105 enum-value false positive suppressed.
- Dependency audit: **no known vulnerabilities**; the unpublished local package was skipped as
  expected.
- Isolated sdist and wheel build: passed.
- Twine checks: passed for both artifacts.

## Files and scope

Added the hypothesis domain and application packages, fixed transaction handler module, focused
documentation, unit/evaluation/integration/adversarial tests, and this report. Extended only the
central proposal union/coordinator, integrity snapshot, durable-state detection, workspace replay,
and the adjacent fixed-kind assertion needed to integrate the eight new proposal types.

No Task 13 CLI behavior, migration, schema column, dependency, CI, arbitrary simulator registration,
runtime source mutation, shell execution, network execution, live model provider, or automatic
promotion was added. No required Task 12 work remains deferred.
