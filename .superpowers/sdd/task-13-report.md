# Task 13 implementation report: handbook and protected harness-evaluation storage

## Outcome

Task 13 was introduced in `84ca6f7` (`feat: separate protected harness evaluation
storage`) and first hardened in `287b796` (`fix: enforce protected storage capability
boundaries`). A second, separate review-reconciliation commit uses the required subject
`fix: reconcile protected worker transactions and lifecycle` (`70bbc3b`). The closure
fix is a third separate commit with the required subject
`fix: seal protected response error paths`. The final Pydantic normalization correction
is a fourth separate commit with the required subject
`fix: revalidate protected result instances`. The final type-check ordering correction
is a fifth separate commit with the required subject
`fix: order exact protected result type checks`.

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

A final DTO-correctness review found that exact-class instances created by Pydantic's
non-validating `model_copy(update=...)` and `model_construct(...)` paths still passed
the exact-type helper unchanged. At the validator, held-out non-UTF-8 bytes then reached
`model_dump(mode="json")` and escaped as a raw `UnicodeDecodeError`. At the coordinator
gateway, a held-out path reached `HarnessMetricRecord` validation and was retained in
the chained Pydantic cause and formatted traceback.

The exact final RED slice selected five cases and failed all five (43 deselected) in
12.22 seconds: both construction methods at both public boundaries plus a legitimate
instance freshness regression. A tightened gateway-only RED selected two and failed
both (46 deselected) in 5.53 seconds, explicitly displaying `held-out-answer.bin` in
each formatted cause chain. After the correction, the identical five-case slice passed
5 of 5 (43 deselected) in 10.69 seconds.

All protected strict frozen DTOs now opt into Pydantic instance revalidation. The
boundary helper still rejects subclasses and other objects, but it also revalidates an
exact instance into a fresh canonical `ProtectedCheckerResult` before either
serialization or storage-record construction. Validation failures are suppressed and
replaced outside their exception context with the existing fixed
`INVALID_CHECKER_RESULT` error. The coordinator's downstream record/repository failure
mapping is likewise cause-free as a defense in depth.

One final ordering review found that the exact-type helper still called `isinstance()`
before `type()`. Python may consult an ordinary object's dynamic `__class__` attribute
for `isinstance()`, so an object whose property raised could escape both public
boundaries as a raw attacker-controlled exception before the exact-type check ran.

The focused RED selected two boundary cases and failed both (48 deselected) in 8.77
seconds. Validator and gateway each displayed their distinctive held-out value in a raw
`RuntimeError` raised by the dynamic `__class__` property. After the correction, the
identical slice passed 2 of 2 (48 deselected) in 6.90 seconds. The helper's first and
sole type precondition is now built-in identity via
`type(result) is ProtectedCheckerResult`; it performs no `isinstance()`, serialization,
attribute access, or other input operation before rejecting a non-exact object. Exact
instances continue through the same fresh Pydantic revalidation path.

## Transaction and authority model

Evaluator validation and coordinator persistence are separate authorities:

- `ProtectedResultValidator` is a spawned evaluator-facing capability. It accepts
  strict JSON, reconstructs a `ProtectedCheckerResult`, and returns only that validated
  DTO. Its parent entry point requires that exact DTO type rather than accepting
  subclasses or duck-typed serializers, using built-in type identity before any dynamic
  object protocol, then revalidates even that exact instance into a fresh canonical
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

Exact checker results forged through Pydantic's non-validating copy and construct APIs
now receive the same fixed error before parent-side serialization or coordinator record
construction. A valid exact instance is rebuilt as an equal but distinct canonical DTO.
Duplicate repository writes additionally prove that defense-in-depth gateway mapping
retains no storage exception as a cause or context.

Ordinary objects with raising dynamic `__class__` properties are rejected before that
property can execute. Validator and gateway regressions prove the fixed error retains
no property exception, cause, context, traceback chain, or held-out value.

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

Final DTO-normalization verification adds:

- exact RED slice: 5 failed, 43 deselected in 12.22 seconds;
- exact gateway leakage RED: 2 failed, 46 deselected in 5.53 seconds;
- identical focused GREEN: 5 passed, 43 deselected in 10.69 seconds;
- final normalization and downstream-mapping regression slice: 6 passed, 42
  deselected in 21.66 seconds;
- complete protected-evaluation file: 48 passed in 121.46 seconds;
- protected coverage run: 48 passed in 162.27 seconds with 94.27% branch-aware
  coverage over 524 statements and 104 branches;
- adjacent migration 0006 and harness append-only properties: 13 passed in 19.35
  seconds;
- repository Ruff lint and strict mypy: passed, with mypy checking 81 source files;
- owned-file Ruff formatting/lint, recursive Bandit medium-or-higher security scan,
  dependency audit, and `git diff --check`: passed; and
- the default Bandit scan reported only three pre-existing low-severity B105 `PASS`
  enum false positives, while repository-wide Ruff formatting reported 18 unrelated
  pre-existing files after the owned file was formatted.

Final exact-type ordering verification adds:

- dynamic-`__class__` RED: 2 failed, 48 deselected in 8.77 seconds;
- identical focused GREEN: 2 passed, 48 deselected in 6.90 seconds;
- final ordering and normalization regression slice: 7 passed, 43 deselected in
  20.64 seconds;
- complete protected-evaluation file: 50 passed in 119.99 seconds;
- boundary/reviewer selection: 21 passed, 29 deselected in 29.77 seconds;
- protected coverage run: 50 passed in 161.39 seconds with 94.28% branch-aware
  coverage over 525 statements and 104 branches;
- adjacent migration 0006 and harness append-only properties: 13 passed in 14.18
  seconds; and
- repository Ruff lint, owned-file Ruff formatting, strict mypy across 81 source
  files, owned default-severity Bandit, recursive medium-or-higher Bandit, and
  `git diff --check`: passed.

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

The final DTO correction also produced fresh isolated sdist and wheel artifacts. Both
passed Twine; the wheel contains migration 0006, `protected_evaluation.py`, and
`domain_records.py`. A fresh short-path install resolved the protected module from
`C:\c13revalidate\Lib\site-packages`; installed-wheel smoke rejected `model_copy` and
`model_construct` attacks at both public boundaries without cause/context or held-out
value leakage, accepted a valid spawned-validator call, persisted a valid gateway
result, and ran installed `scientist-harness --help`.

The exact-type ordering correction produced another fresh isolated sdist and wheel;
both passed Twine and the wheel retained migration 0006 plus the protected storage
modules. A fresh short-path install resolved the protected module from
`C:\c13typeorder\Lib\site-packages`. Installed-wheel smoke rejected the raising
dynamic-`__class__` object through both public boundaries without evaluating its
property, retained valid exact-instance freshness, validated a legitimate spawned
result, persisted a legitimate gateway result, and ran installed
`scientist-harness --help`.

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
