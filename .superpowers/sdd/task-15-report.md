# Task 15 implementation report

## Scope

Implemented fair, protected harness-evolution evaluation for the approved governed
adaptation design. The change adds immutable campaign and partition models, exact
multi-dimensional budgets, output-only protected evaluation, transaction-coordinator
handlers, append-only reporting and decision records, and report-only evaluator-collapse
diagnostics.

No Task 16 behavior is included.

## Capability boundaries

- Candidate execution receives only public task input and an immutable budget.
- The coordinator receives public manifests and hashes, never protected answers.
- The evaluator receives an answer-reader capability, already-produced candidate bytes,
  and a fixed checker. It exposes no candidate invocation API.
- Task 13 `ProtectedCheckerResult` is the single strict DTO at the protected boundary.
- The transaction handler creates a coordinator-local protected gateway from the active
  database connection, appends within that unit of work, and closes the gateway before
  returning.
- The decision authority consumes only safe durable records and cannot use a collapse
  diagnostic as promotion authority.

These boundaries keep protected data and authority out of the candidate, service, ordinary
database, audit events, and exports.

## TDD evidence

Initial RED:

```text
4 collection errors in 2.20s
```

The errors were the expected missing Task 15 application package and collapse model.

Initial GREEN sequence:

```text
48 passed in 2.40s
7 passed in 34.14s
55 passed in 29.75s
55 passed in 29.20s
```

Evaluator-succession hardening RED:

```text
test_evaluator_change_is_retained_as_a_confound_and_public_lineage
AssertionError: assert False is True
```

The old lineage rule rejected a changed evaluator even when the change had a durable
`EVALUATOR_CHANGED` confound. The minimal fix binds the protected result to the evaluator
recorded on its public observation. Targeted GREEN was `1 passed in 6.60s`; the complete
Task 15 slice then passed `56 passed in 34.32s` and, after replacing the coverage-hostile
spawned validator in that orchestration fixture with a strict in-process test double,
`56 passed in 15.50s`.

## Compatibility evidence

The Task 13 protected store, migration 0006, append-only property, and evaluator-succession
suite first printed `79 passed in 118.56s`, but an outer 120-second wrapper returned 124
after pytest had completed. A fresh controlled-temp rerun exited zero:

```text
79 passed in 147.37s
```

The managed sandbox cannot access pytest's default user temp directory, so subsequent gates
use a workspace-owned `--basetemp` and disable pytest's cache provider.

## Coverage and quality gates

The first focused ownership measurement was 77.59%. Coverage-guided tests then exercised
substantive invalid-state, authority, lineage, rollback, and evaluator-succession behavior
without changing frozen production source. The final focused gate combines two
non-overlapping shards:

```text
79 Task 15 domain/application/adversarial/collapse tests
16 evaluator-succession tests
95 total
90.1628664495% branch-aware coverage
coverage report --fail-under=90: exit 0
```

Static and security gates:

```text
Ruff check: PASS
Ruff format (owned files): PASS
mypy: Success, no issues in 104 source files
Bandit medium/high severity recursive scan: PASS
pip-audit: No known vulnerabilities found
```

`pip-audit` skipped the unpublished local distribution as expected. Python UTF-8 mode and
a workspace cache were required because the virtualenv path contains `ö` and the managed
sandbox blocks the default user cache. Its network retry completed successfully.

Adjacency:

```text
93 passed in 108.15s
```

That suite covered the transaction coordinator, adaptation foundation and authority,
adaptation append-only properties, and migrations 0004 through 0006.

Fresh non-overlapping inventory:

```text
A2 unit/adversarial/e2e/evaluation: 1008 passed, 3 skipped in 162.25s
B application/CLI/handbook integration: 333 passed, 3 expected stale-handbook failures
C storage integration: 161 passed, 3 skipped in 621.40s
D property: 213 passed in 774.54s
combined branch-aware coverage: 90.8715431581%
coverage report --fail-under=90: exit 0
```

The three B failures are provenance guards: the repository handbook is intentionally not
rebound to dirty source or an old commit. After this implementation is committed, the
handbook will be regenerated against that real commit in a separate documentation commit
and B plus the affected provenance/artifact gates will be rerun on final `HEAD`.

Packaging:

```text
isolated sdist and wheel build: PASS
Twine check (sdist and wheel): PASS
fresh wheel install with declared dependencies: PASS
installed-wheel role/UoW/non-retention smoke: PASS
```

The smoke imported from fresh `site-packages`, verified candidate/evaluator role
separation and non-leaking output, released a closed answer-reader capability, and proved
that a protected gateway append visible inside the active unit of work disappears after a
forced rollback.

## Risks and operational notes

- Budget mismatches and evaluator changes make a campaign incomparable unless independently
  resolved; they are not normalized away.
- Discovery results do not count as transfer evidence.
- Collapse reports are non-authoritative and cannot promote a candidate.
- Protected worker behavior is covered by the unchanged Task 13 compatibility suite; the
  Task 15 orchestration fixture uses a strict in-process validator only to avoid Windows
  coverage tracing interfering with spawned worker startup.
- Existing campaign records remain decodable because the new 0006 JSON fields are optional
  with strict defaults; no database schema migration is added.
