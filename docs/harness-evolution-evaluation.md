# Governed harness-evolution evaluation

Harness evolution is evaluated as a governed campaign. A campaign compares exactly two
immutable variants:

- `UNCHANGED_HARNESS_SINGLE_ATTEMPT`, the baseline harness under a single-attempt budget.
- `EVOLVED_HARNESS`, the candidate harness being considered for admission.

The campaign version fixes the harness versions, model and adapter identities, evaluator
identity, partition manifests, and per-variant budgets. Task membership is exclusive across
the five required partitions and cannot change within that campaign version:

1. harness-discovery tasks;
2. harness-validation tasks;
3. harness-regression tasks;
4. harness-transfer tasks; and
5. harness-safety tasks.

Discovery performance is evidence about discovery only. It is never treated as transfer
performance.

## Exact budget comparison

Every variant records an immutable budget over all of these dimensions: model identity,
model version, adapter identity, tasks, feedback mode, tools, attempts, token limit,
reasoning limit, evaluator-call limit, wall-clock limit, cost limit, and human-intervention
limit. A report preserves each comparison separately. Any mismatch is a confound and makes
the campaign incomparable until independently resolved.

## Protected role boundaries

The candidate producer receives only a `PublicTaskInput` and its immutable evaluation
budget. Its frozen execution context contains no answer reader, expected answer,
protected-store handle, database connection, evaluator callable, or candidate invocation
hook.

The coordinator receives public campaign manifests and hashes. It does not read protected
answers.

The evaluator is output-only. It receives already-produced candidate bytes, a protected
answer through the narrow answer-reader capability, and a fixed exact-bytes checker
configuration. It cannot invoke the candidate. Its strict result DTO contains hashes,
checker identity, outcome, and metric values, but no protected bytes, paths, URIs, database
handles, or arbitrary metadata.

The transaction handler validates that exact Task 13 DTO and appends it through a
coordinator-local protected-result gateway created from the active SQLAlchemy connection.
The gateway is closed before the transaction completes. Therefore the protected append and
the ordinary observation/metric/audit writes share one unit of work, while neither the
candidate nor the long-lived service owns protected storage authority.

## Reports and decisions

A campaign report is append-only evidence. It retains every iteration, negative result,
budget comparison, evaluator version, confound, rollback record, and the five partition
metric families. Evaluator changes must be recorded as explicit confounds; they are not
silently normalized away.

The decision authority sees only safe public records and strict hash-bound results.
Admission requires comparable budgets, no unresolved confounds, accepted independent
measurement and evaluator audit records, no catastrophic regression, transfer evidence,
and an executable rollback target. Otherwise the result is `REJECTED` or `INCONCLUSIVE`,
not a partial promotion.

## Collapse reporting

Evaluator collapse is reported across these fourteen independent metric dimensions:

- protected performance;
- external performance;
- calibration;
- response diversity;
- hypothesis diversity;
- source diversity;
- experiment diversity;
- adapter-output entropy;
- repeated-error rate;
- confidence/error coupling;
- evaluator disagreement;
- catastrophic regression;
- task-distribution narrowing; and
- externally grounded data proportion.

The evaluator records every metric dimension separately and emits explicit findings for
these nine prohibited patterns:

- score saturation;
- verdict monoculture;
- error-category collapse;
- rationale-template collapse;
- position bias;
- reference-answer leakage;
- candidate-identity bias;
- reward hacking; and
- catastrophic-regression masking.

Collapse findings are diagnostic only. They cannot authorize promotion and no aggregate
score can hide a catastrophic dimension.

## 0.3.0 guidance, trace, and reward evidence

A `GuidanceEvaluationProtocol` declares all four conditions: no guidance, descriptive
method direction, procedure without framework integration, and compiled procedure with
framework integration. A guidance cell is current only when its exact protocol version,
protocol hash, condition, output artifact, verifier result, trace, and reward assessment
resolve to one accepted evidence chain. Cross-protocol, later-only, missing, ambiguous,
or stale evidence is `UNMATCHED_EVALUATION`; the handler creates no cell projection.

A `ModelHarnessProtocol` declares an exact model by harness by partition grid and a
budget for each model. Analysis retains each expected cell and reports confounds rather
than imputing absent or invalid observations. The implementation bounds a protocol to
256 cells, loads one frozen resolution snapshot, and indexes trace/reward coordinates so
duplicate evidence remains an explicit ambiguity rather than a last-write-wins choice.

Trace metadata records availability separately from value. Metadata availability states:
`AVAILABLE`, `UNAVAILABLE`, and `NOT_APPLICABLE`. `AVAILABLE` binds an applicable value to
its exact retained evidence. `UNAVAILABLE` means the metadata applies but the provider or
workflow does not expose it. `NOT_APPLICABLE` means the field does not apply to that trace
or operation. The latter two states supply neither a value nor fabricated evidence. The
transaction coordinator uses one owned exact trace proposal snapshot for
admission, projection, transaction, and audit, preventing staged caller mutation.

A reward assessment cannot authorize promotion. Its validity is recomputed from the
exact accepted trace, reward observation, checker/verifier result, environment, budget,
termination, contamination, and every retained finding. Any invalidating finding makes
`promotion_evidence` false even when the numeric reward is high. Run
`python -m pytest tests/integration/application/test_harness_eval_extensions.py
tests/adversarial/test_trace_reward_tampering.py -q` to verify matching, availability,
single-snapshot admission, and reward validity.

The offline 0.3.0 example registers a deterministic toy validator as a `TOOL` actor. The
validator compares bounded artifact bytes with one declared SHA-256 digest and never
executes the artifact. The installed-wheel test runs that workflow from a fresh wheel
environment; it does not fall back to repository source.

## Limitations

The implementation proves capability separation and durable lineage, not that a particular
task set is representative. Campaign authors remain responsible for partition quality,
checker validity, independent review quality, and protected-store operations. A new task,
budget, evaluator, checker, or harness definition requires a new campaign version rather
than mutation of existing evidence.
