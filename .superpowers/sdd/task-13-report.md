# Task 13 implementation report: handbook and protected harness-evaluation storage

## Outcome

Task 13 was introduced in `84ca6f7` (`feat: separate protected harness evaluation
storage`) and first hardened in `287b796` (`fix: enforce protected storage capability
boundaries`). A second, separate review-reconciliation commit uses the required subject
`fix: reconcile protected worker transactions and lifecycle`.

The final design keeps protected answers in a physically separate store and sends only
strict typed hashes, aggregates, and checker outcomes into ordinary campaign storage.
It now also preserves the coordinator's exact transaction, serializes every worker
exchange, poisons desynchronized channels, releases process resources deterministically,
and converts expected protected-store failures into fixed non-leaking responses.

## Review reconciliation and RED evidence

The second review found four related defects in the first worker-based hardening:

1. The result gateway ignored its supplied SQLAlchemy connection, opened a competing
   SQLite writer in a child process, could not see a campaign created in the same unit
   of work, and made result persistence non-atomic with coordinator commit or rollback.
2. Shared capabilities incremented request IDs and performed send/poll/receive without
   one exchange-level lock. Concurrent callers could consume one another's responses,
   and a timed-out or mismatched response did not permanently invalidate the channel.
3. Close joined or terminated a worker without calling `BaseProcess.close()`, while the
   owner store strongly retained closed wrappers. Concurrent close could leave process
   handles and stale capability state behind.
4. Expected SQLAlchemy, SQLite, and filesystem failures could escape a role worker,
   produce an inherited child traceback, and collapse a typed integrity failure into
   `CAPABILITY_WORKER_UNAVAILABLE`.

The focused reviewer command selected 20 cases. Before implementation it reproduced
the report with **17 failed and 3 passed in 159.06 seconds**. Failures included real
`DatabaseUnitOfWork` commit and rollback cases, 32-thread reader and gateway races,
timeout and request-ID desynchronization reuse, retained/partially closed workers, and
a structurally corrupt protected SQLite database leaking a child traceback.

After implementation the identical selection passed **20 of 20** (18 other tests
deselected) in 63.10 seconds. The full protected-evaluation integration file then passed
38 of 38 in 94.95 seconds.

## Transaction and authority model

Evaluator validation and coordinator persistence are separate authorities:

- `ProtectedResultValidator` is a spawned evaluator-facing capability. It accepts
  strict JSON, reconstructs a `ProtectedCheckerResult`, and returns only that validated
  DTO. It owns no SQLAlchemy object, database URL, protected path, or repository.
- `ProtectedResultGateway` remains the coordinator-facing persistence protocol. Its
  implementation is a local adapter over the caller's supplied active SQLAlchemy
  connection. It uses repositories bound to that exact connection and never creates
  an engine, connection, transaction, or child process.
- The local gateway is intentionally transparent about its main-database authority and
  must never be inserted into evaluator or candidate dependency graphs. Recursive
  graph tests prove the evaluator validator has no database/gateway authority and the
  coordinator adapter contains the exact supplied connection.
- Gateway `close()` closes only the adapter. The coordinator continues to own commit,
  rollback, and connection lifecycle through `DatabaseUnitOfWork`.

Consequently a campaign created earlier in the same unit of work is visible to the
gateway, campaign/result writes commit together, and an exception rolls both back.
There is no second SQLite writer and no independently durable protected result.

## Worker concurrency and lifecycle

Each process capability owns a re-entrant lock around the complete request-number,
send, poll, and receive exchange. A 32-thread shared-reader regression proves response
correlation, while a 32-thread coordinator-gateway regression proves serialized use of
the single supplied connection.

Timeout, EOF, transport failure, malformed response, or request-ID mismatch permanently
poisons a channel. Later calls return `CAPABILITY_CHANNEL_UNUSABLE`; they cannot consume
a delayed frame as if it belonged to a newer request. Typed operation failures from a
healthy worker do not poison the channel.

Close is idempotent and protected by the same lock. It sends a cooperative close only
while the channel is usable, closes the transport, joins or terminates and joins the
child, and finally calls `BaseProcess.close()`. Portable tests check the closed process
state and weak capability registry; Windows additionally checks aggregate process-handle
counts. Concurrent 32-caller close is deterministic. Stores weakly track live role
capabilities so already closed or abandoned wrapper objects are not retained indefinitely.

## Protected worker error behavior

Reader and auditor workers catch expected SQLAlchemy/database, storage-integrity, and
filesystem failures at their process boundary. The reader returns fixed typed errors;
the auditor returns a fixed integrity finding when it can safely continue. Neither
response includes exception text, SQL, identifiers from rejected payloads, filesystem
paths, protected bytes, or reversible answer references.

Regressions cover a structurally corrupt SQLite schema plus missing and non-regular
artifact paths. They assert no `Traceback`, SQLite diagnostic, protected-root path, or
secret reaches captured child output, exception messages, or serialized reports.

## Existing Task 13 storage retained

Migration `0006_handbook_and_harness_evaluation` still provides append-only behavior
links, handbook verification, campaigns, partition manifests, budgets, observations,
metrics, confounds, and decisions. `ProtectedEvaluationStore` still owns its separate
`protected.sqlite3` database and content-addressed artifact root. Protected metadata
contains task identity, SHA-256, byte count, and creation time, never answer bytes.

Strict size-limited JSON framing, fixed operation allowlists, base64 reader responses,
append-only triggers, canonical records, and Git SHA-1/SHA-256 object-ID support remain
unchanged. No migration or relational schema adjustment was needed for this review fix.

## Verification inventory

Focused and migration gates on the final behavior:

- reviewer regression selection: 20 passed, 18 deselected in 63.10 seconds;
- complete protected-evaluation file: 38 passed in 94.95 seconds;
- protected coverage run: 38 passed in 148.25 seconds;
- exact Task 13 slice: 51 passed in 142.10 seconds; and
- complete migration chain from 0001 through 0006: 49 passed in 62.67 seconds.

The coverage run measured 92.17% branch-aware coverage for
`protected_evaluation.py`: 508 statements and 92 branches, above the configured 90%
threshold. It also exposed three test-owned `sqlite3.Connection` resource warnings;
those connections now use explicit closing semantics. The five affected
corruption/authority tests pass with `ResourceWarning` promoted to an error.

Fresh non-overlapping repository inventory on the frozen implementation tree:

- unit, adversarial, end-to-end, and evaluation: 792 passed in 59.50 seconds;
- application and CLI integration: 315 passed in 1,206.86 seconds;
- storage integration: 149 passed and 3 skipped in 453.35 seconds; and
- property tests: 213 passed in 784.31 seconds.

Combined result: **1,469 passed, 3 skipped**.

## Release gates

- Repository Ruff lint: passed.
- Changed-file Ruff formatting: passed.
- Strict mypy: passed across 81 source files.
- Bandit recursive source scan: passed with only the repository's established B105
  enum-value suppression.
- Dependency audit: no known vulnerabilities; the unpublished local distribution was
  skipped as expected.
- Fresh isolated sdist and wheel build: passed.
- Twine checks: passed for both artifacts.
- Wheel inspection: 94 entries and the wheel contains migration 0006,
  `protected_evaluation.py`, and `domain_records.py`.
- Fresh short-path wheel install: import resolved from
  `C:\c13smoke\Lib\site-packages`; a 32-thread spawned answer reader, spawned result
  validator, real-unit-of-work commit and rollback, and installed
  `scientist-harness --help` all passed.

## Files in this reconciliation

Modified:

- `src/super_scientist/providers/storage/protected_evaluation.py`
- `tests/integration/storage/test_protected_evaluation_store.py`
- `docs/superpowers/plans/2026-07-18-governed-adaptation-and-harness-evolution.md`
- `docs/superpowers/specs/2026-07-18-governed-adaptation-and-harness-evolution-design.md`
- `.superpowers/sdd/task-13-report.md`

Added:

- `docs/adr/0001-protected-evaluation-transaction-and-worker-lifecycle.md`

No migration, dependency, CI, CLI, network, dynamic-import, or runtime plugin surface
was added by this reconciliation.

## Residual boundary

Spawned workers enforce object-capability and protocol separation inside the Python
application, but they run under the same operating-system account. A process with
OS-level authority over the protected root can still read those files. Deployment must
place that root behind the separately privileged evaluation role; OS sandboxing or
separate service identities remain outside Task 13.

Ordinary storage intentionally retains protected-content hashes. Those hashes can
reveal equality between records but cannot reconstruct answer bytes. Task 13 remains a
storage and capability-boundary change; campaign execution, fairness comparison,
admission, promotion, and rollback orchestration remain later tasks.
