# Task 13 implementation report: handbook and protected harness-evaluation storage

## Outcome

Task 13 was implemented in `84ca6f7` (`feat: separate protected harness evaluation
storage`) on base `ca567ba`. Review hardening is delivered as a separate follow-up
commit with the required subject `fix: enforce protected storage capability
boundaries`.

The original feature adds strict append-only handbook and harness-evaluation history
to the main database while placing held-out answer bytes and private metadata beneath
a separately supplied protected filesystem root. The review fix removes transitive
storage authority from every public role capability, uses strict non-pickle IPC, adds
deterministic worker lifecycle behavior, and accepts real Git SHA-1 or SHA-256 object
identifiers for handbook verification records.

## Review findings and test-driven evidence

The review regressions reproduced all reported authority leaks before implementation:

- the answer reader graph reached `._reader.__self__ -> ProtectedEvaluationStore`;
- the integrity auditor graph reached `._auditor.__self__ ->
  ProtectedEvaluationStore`;
- the result gateway graph reached `._repository -> HarnessMetricRepository` and its
  underlying SQLAlchemy connection; and
- the repository's actual 40-character `git rev-parse HEAD` value failed the former
  SHA-256-only handbook contract.

That focused RED run selected 5 tests and failed all 5 in 5.40 seconds. A second
worker-behavior RED run failed 5 of 5 tests in 6.02 seconds because role workers,
typed failures, close semantics, and operation allowlists did not yet exist.

While hardening IPC, an adversarial pickle probe proved that
`multiprocessing.Connection.send`/`recv` could deserialize attacker-controlled
objects before an operation allowlist ran. The RED probe caused its marker side
effect. IPC was then changed to size-limited `send_bytes`/`recv_bytes` frames with
strict Pydantic JSON request and response envelopes. The strict-JSON adversarial
slice passed 4 of 4 tests in 12.85 seconds, including proof that the same malicious
pickle bytes are rejected without execution and that the worker remains usable.

A final lifecycle RED test expected `CAPABILITY_WORKER_UNAVAILABLE` for a live worker
that never returns a frame but received `INVALID_WORKER_RESPONSE`. Finite 10-second
response polling made that test GREEN in 2.38 seconds and prevents an unbounded close
wait. Bandit then identified three low-severity production assertions; each was
replaced by an equivalent explicit fail-closed runtime guard. The focused post-change
slice passed 4 of 4 tests, and the complete protected-storage file passed 26 of 26.

## Delivered ordinary storage

Migration `0006_handbook_and_harness_evaluation` adds these authoritative append-only
main-database tables:

1. `behavior_rule_link_versions`
2. `handbook_verification_records`
3. `harness_campaigns`
4. `harness_partition_manifests`
5. `harness_budgets`
6. `harness_observations`
7. `harness_metrics`
8. `harness_confounds`
9. `harness_decisions`

Every authoritative table has update- and delete-rejecting triggers. The mutable
`harness_campaign_heads` table is a rebuildable projection whose campaign, decision,
and status identity is constrained to an exact retained decision.

The schema and strict frozen records also enforce:

- behavior links reference immutable behavioral-rule versions;
- every harness child references its campaign;
- observations reference a partition manifest from the same campaign;
- ordered tuple fields and relationship identities retain canonical JSON;
- content hashes are lowercase SHA-256 values;
- numeric metrics and budgets are finite before storage; and
- decisions admit a campaign exactly when their status is `ADMITTED`.

`HandbookVerificationRecord.repository_commit` now uses `GitObjectId`, a strict
lowercase hexadecimal contract accepting the two Git object formats: 40 characters
for SHA-1 repositories and 64 for SHA-256 repositories. Tests validate the actual
repository HEAD rather than a fabricated value. This is a canonical-JSON record
contract change only; no migration or relational column change is required.

## Protected capability boundary

`ProtectedEvaluationStore` owns `protected.sqlite3` plus a content-addressed artifact
directory beneath its private root. Its metadata table stores only task identity,
expected-output hash, byte count, and creation time and is append only. Artifact
reads revalidate byte length and SHA-256 content. Replaying identical task content is
idempotent; rebinding a task identity to different bytes is rejected.

Every public capability now owns only a duplex IPC endpoint and a spawned process
handle:

- the reader worker permits only `READ_EXPECTED_OUTPUT` and `CLOSE`;
- the auditor worker permits only `VERIFY_INTEGRITY` and `CLOSE`; and
- the main-database gateway worker permits only `APPEND_RESULT` and `CLOSE`.

Recursive adversarial graph traversal covers ordinary attributes, all inherited
slots, bound-method `__self__`, closures, defaults, partials, mappings, sequences,
and nested repository state. Reader and auditor graphs expose no full store, writer,
factory, path, engine, connection, repository, or cross-role operation. The gateway
graph exposes no database URL/path, SQLAlchemy engine/connection, or unrestricted
repository. Raw IPC probes verify the same role allowlists inside each worker.

Messages are strict, frozen JSON models with exact fields, correlated request IDs,
fixed safe error codes/messages, a 64 MiB frame ceiling, and no pickle decoding.
Reader bytes cross as validated Base64. Auditor reports and checker results are
revalidated from canonical JSON in their receiving process. Corruption, missing
outputs, invalid checker results, duplicate appends, worker loss, malformed frames,
and startup failure return typed errors without paths, identifiers from rejected
payloads, answer data, or underlying exception text.

Capabilities have idempotent `close()`. The owner store closes all outstanding role
capabilities before disposing its engine. Workers receive a cooperative close,
responses have a finite timeout, processes are joined, and an unresponsive process
is terminated and joined. Calls after close fail with `CAPABILITY_CLOSED`.

The gateway uses a separate synchronous main-database worker and therefore sees only
committed state. A referenced campaign must be committed before `append_result`;
uncommitted caller state fails with `REFERENCED_CAMPAIGN_UNAVAILABLE`. This deliberate
tradeoff preserves the capability boundary and makes the append independently
durable, but it means callers cannot atomically create a campaign and append its
protected result inside one uncommitted caller transaction.

## Verification inventory

Focused and migration gates:

- initial post-review protected-storage file plus both real-repository Git regressions:
  18 passed in 38.89 seconds;
- final Task 13 focused slice: 39 passed in 72.18 seconds;
- complete migration chain from 0001 through 0006: 49 passed in 54.59 seconds; and
- final complete protected-storage file: 26 passed in 106.13 seconds.

Fresh non-overlapping inventory on the timeout-finalized source tree:

- unit, adversarial, end-to-end, and evaluation: 792 passed in 60.58 seconds;
- application and CLI integration: 315 passed in 1,188.54 seconds;
- storage integration: 137 passed and 3 skipped in 383.71 seconds; and
- property tests: 213 passed in 752.45 seconds.

Combined result: **1,457 passed, 3 skipped**. The only later production adjustment
was the assertion-to-explicit-guard security cleanup described above. Its directly
affected protected-storage file, focused regression slice, Ruff, mypy, Bandit, and
coverage gates were all rerun after that adjustment.

Branch coverage for `protected_evaluation.py` is 92.42% over 455 statements and 86
branches. The coverage command enforced and passed the repository's configured 90%
threshold.

## Release gates

- Changed-file Ruff lint and formatting: passed for all 6 changed Python files.
- Strict mypy: passed across 81 source files.
- Bandit recursive source scan: passed; only the repository's pre-existing B105
  enum-value false positive was suppressed.
- Dependency audit: no known vulnerabilities; the editable unpublished local
  distribution was skipped as expected. Python UTF-8 mode was enabled for the
  non-ASCII virtual-environment path on Windows.
- Isolated sdist and wheel build: passed.
- Twine checks: passed for both artifacts.
- Wheel contents: include migration 0006, `protected_evaluation.py`,
  `domain_records.py`, and the Git object-ID primitive.
- Fresh short-path wheel installation: import resolved from the installed wheel,
  `scientist-harness --help` passed, and a spawned installed-wheel answer-reader
  worker completed a strict-JSON round trip.

The repository-wide Ruff format check still identifies 18 pre-existing unrelated
files. They were not reformatted or included in this fix; every changed Python file
is format-clean.

## Files in the review fix

Modified:

- `src/super_scientist/domain/primitives.py`
- `src/super_scientist/providers/storage/domain_records.py`
- `src/super_scientist/providers/storage/protected_evaluation.py`
- `tests/integration/storage/test_protected_evaluation_store.py`
- `tests/property/test_harness_eval_append_only.py`
- `tests/unit/domain/test_primitives.py`
- `.superpowers/sdd/task-13-report.md`

No migration, dependency, CI, CLI, network, dynamic-import, or runtime plugin surface
was added by the review fix.

## Residual boundary

Spawned workers enforce object-capability and protocol separation inside the Python
application, but they run under the same operating-system account. Any process with
OS-level authority over the protected root can still read those files. Deployments
must place that root behind the separately privileged evaluation role; OS sandboxing
or separate service identities are outside Task 13.

Ordinary storage intentionally retains protected-content hashes. Those hashes can
reveal equality between records but cannot reconstruct answer bytes.

Task 13 remains a storage and capability-boundary change. It does not implement Task
14 campaign execution, evaluator object graphs, fairness comparisons, admission,
promotion, or rollback orchestration.
