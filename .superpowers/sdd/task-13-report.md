# Task 13 implementation report: handbook and protected harness-evaluation storage

## Outcome

Task 13 was introduced in `84ca6f7` (`feat: separate protected harness evaluation
storage`) and first hardened in `287b796` (`fix: enforce protected storage capability
boundaries`). A second, separate review-reconciliation commit uses the required subject
`fix: reconcile protected worker transactions and lifecycle` (`70bbc3b`). The closure
fix is a third separate commit with the required subject
`fix: seal protected response error paths`.

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

The closure review found two final response-path defects. Validator and gateway
prevalidation raised fixed errors *from* Pydantic validation errors, so a formatted
cause chain retained answer-bearing subclass or malformed-DTO input values. Separately,
reader, auditor, and validator role-payload decoding occurred after `_request` released
its lock; an invalid payload raised `INVALID_WORKER_RESPONSE` without poisoning the
channel, allowing a later call to consume the next response.

Strict RED evidence was recorded independently:

- result prevalidation: 2 selected, 2 failed in 10.47 seconds, with literal held-out
  bytes and protected-path material present in the formatted Pydantic cause chains; and
- role payload decoding: 3 selected, 3 failed in 3.08 seconds because every second call
  succeeded instead of raising `CAPABILITY_CHANNEL_UNUSABLE`.

The exact combined closure slice passes 5 of 5 in 6.46 seconds. Public validator and
gateway boundaries now require the exact `ProtectedCheckerResult` type and reject other
objects before invoking their methods. The complete exchange now includes role payload
decoding under the same lock and poison transition; invalid responses retain no
validation cause/context or sensitive formatted chain.

## Transaction and authority model

Evaluator validation and coordinator persistence are separate authorities:

- `ProtectedResultValidator` is a spawned evaluator-facing capability. It accepts
  strict JSON, reconstructs a `ProtectedCheckerResult`, and returns only that validated
  DTO. Its parent entry point requires that exact DTO type rather than accepting
  subclasses or duck-typed serializers. It owns no SQLAlchemy object, database URL,
  protected path, or repository.
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
send, poll, receive, and role-payload decode exchange. A malformed envelope or decoded
payload therefore poisons the channel before any competing or later request can send.
A 32-thread shared-reader regression proves response correlation, while a 32-thread
coordinator-gateway regression proves serialized use of the single supplied connection.

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
Answer-bearing checker-result subclasses, malformed DTOs, and malformed reader/auditor/
validator success payloads additionally prove fixed error args, absent causes/contexts,
non-leaking formatted chains, and no post-failure channel reuse.

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

Closure-source verification adds:

- combined closure regressions: 5 passed, 38 deselected in 6.46 seconds;
- complete protected-evaluation file: 43 passed in 140.46 seconds;
- expanded reviewer slice: 25 passed, 18 deselected in 99.77 seconds;
- leakage/lifecycle warnings-as-errors slice: 12 passed, 31 deselected in 51.30 seconds;
- protected coverage run: 43 passed in 142.94 seconds with 94.19% branch-aware
  coverage over 518 statements and 102 branches;
- complete storage integration: 154 passed, 3 skipped in 378.88 seconds; and
- complete property tests: 213 passed in 605.20 seconds.

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
  `C:\c13seal\Lib\site-packages`; answer-bearing subclass and malformed-DTO redaction,
  all three malformed role-payload poison paths, a 32-thread spawned answer reader,
  spawned result validator, real-unit-of-work gateway, and installed
  `scientist-harness --help` all passed.

## Files in the closure fix

Modified:

- `src/super_scientist/providers/storage/protected_evaluation.py`
- `tests/integration/storage/test_protected_evaluation_store.py`
- `docs/adr/0001-protected-evaluation-transaction-and-worker-lifecycle.md`
- `.superpowers/sdd/task-13-report.md`

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
