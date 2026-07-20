# Task 13 implementation report: handbook and protected harness-evaluation storage

## Outcome

Implemented migration 0006_handbook_and_harness_evaluation on base
ca567baeb6eb15217238c565259624bef8191729. The change adds strict ordinary
handbook and harness-evaluation history to the main database while placing expected
answer bytes and their private metadata in a separately supplied protected filesystem
root, SQLite database, and content-addressed artifact namespace.

The main repository graph never owns the protected engine, artifact store, answer
reader, or a reversible answer reference. The sole cross-store gateway accepts a
strict typed checker result and persists only identifiers, hashes, finite aggregates,
checker provenance, outcome, and timestamp. The required commit subject is
feat: separate protected harness evaluation storage.

## Test-driven implementation evidence

- The first exact Task 13 command was RED during collection: 4 tests were discovered
  and 2 collection errors reported the absent HarnessMetricRepository and
  BehaviorRuleLinkVersionRecord. The run exited 1 in 7.1 seconds before any
  production implementation existed.
- Adding migration 0006, runtime metadata, strict record/repository contracts, and
  the protected-store capability boundary made the initial focused slice GREEN at
  14 passed in 16.24 seconds.
- Additional replay, corruption, path-shape, strict-decoding, relationship, head,
  and failure-path cases were added through further RED/GREEN cycles.
- The final formatted Task 13 slice is GREEN at 20 passed in 25.58 seconds.
- The complete migration chain from 0001 through 0006, including upgrade,
  downgrade, legacy-row preservation, foreign-key, append-only, and exact-metadata
  checks, is GREEN at 49 passed in 59.04 seconds.

## Delivered storage

Migration 0006 adds these authoritative append-only main-database tables:

1. behavior_rule_link_versions
2. handbook_verification_records
3. harness_campaigns
4. harness_partition_manifests
5. harness_budgets
6. harness_observations
7. harness_metrics
8. harness_confounds
9. harness_decisions

Each authoritative table has update- and delete-rejecting triggers. The mutable
harness_campaign_heads table is a rebuildable projection whose campaign, decision,
and status identity is constrained to an exact retained decision.

The schema also enforces:

- behavior links reference an immutable behavioral-rule version;
- every harness child references its campaign;
- observations reference a partition manifest from the same campaign;
- ordered tuple fields and relationship identities retain canonical JSON;
- hashes are lowercase SHA-256 values;
- finite numeric metrics and budgets are validated before storage; and
- decisions admit a campaign exactly when their status is ADMITTED.

Strict frozen storage models expose the seven fixed harness variants, five fixed
partitions, ten fixed decision statuses, ordinary append-only repositories for all
nine record families, and a fixed campaign-head repository. The generic repository
implementation remains private and public constructors accept only an existing main
database connection.

## Protected-store boundary

ProtectedEvaluationStore receives a separate pathlib.Path root. Beneath that root it
owns protected.sqlite3 and a content-addressed artifacts directory. The protected
database stores only task identity, expected-output hash, byte count, and creation
time; answer bytes are stored only in the protected artifact namespace. Its metadata
table is append only.

The constructor fails closed for symlink or Windows reparse-point components and for
a non-regular protected database path. Artifact reads revalidate byte length and
SHA-256 content. Replaying identical task content is idempotent, while rebinding a
stable task identifier to different bytes is rejected.

Public role-specific capabilities are:

- ProtectedAnswerReader, which is the only capability that can return expected bytes;
- ProtectedIntegrityAuditor, which reports only task identity, expected hash,
  failure code, and aggregate counts; and
- ProtectedResultGateway, which writes a strict ProtectedCheckerResult to the
  ordinary metric repository without owning protected resources.

ProtectedCheckerResult is frozen, strict, and forbids extra fields. It cannot carry
expected bytes, answer references, artifact paths, or arbitrary payloads. Duplicate
metric identifiers and non-finite values reject. The ordinary RepositorySet exposes
neither protected storage nor expected-output capabilities.

## Verification inventory

Fresh non-overlapping full-suite runs completed before final test-only formatting:

- Unit, adversarial, end-to-end, and evaluation: 791 passed in 26.41 seconds.
- Application and CLI integration: 315 passed in 786.48 seconds.
- Storage integration: 119 passed, 3 skipped in 166.63 seconds.
- Property tests: 212 passed in 482.76 seconds.
- Combined result: 1,437 passed, 3 skipped.

A second fresh inventory collected branch coverage:

- Unit, adversarial, end-to-end, and evaluation: 791 passed in 29.90 seconds.
- Storage integration: 119 passed, 3 skipped in 175.59 seconds.
- Property tests: 212 passed in 582.37 seconds.
- Application and CLI integration: 315 passed in 1,034.71 seconds.
- Combined branch coverage: 90.615363% over 11,404 statements and 2,864 branches.
- coverage report --fail-under=90 passed.
- The protected_evaluation.py module reports 93% branch-aware coverage.

Final release gates:

- Repository-wide Ruff lint: passed.
- All 9 changed Python files: Ruff-format clean.
- Strict mypy: passed across 81 source files.
- git diff --check: passed, with only configured LF-to-CRLF notices.
- Bandit: passed with only the pre-existing B105 enum-value false positive
  suppressed.
- Dependency audit: no known vulnerabilities; the unpublished local package was
  skipped as expected. Python UTF-8 mode was enabled because pip-audit's dependency
  helper otherwise misdecoded the non-ASCII virtualenv path on Windows.
- Isolated sdist and wheel build: passed.
- Twine checks: passed for both artifacts.
- Wheel contents: include migration 0006 and protected_evaluation.py.
- Fresh short-path wheel installation: package import and installed
  scientist-harness --help both passed.

The first combined wheel-smoke wrapper was rejected by the shell safety policy before
execution because it combined a generated cleanup target with recursive deletion. The
successful smoke used an explicit prechecked short path, and that disposable
environment was then independently verified and removed.

## Files and scope

Added:

- alembic/versions/0006_handbook_and_harness_evaluation.py
- src/super_scientist/providers/storage/protected_evaluation.py
- tests/integration/storage/test_migration_0006.py
- tests/integration/storage/test_protected_evaluation_store.py
- tests/property/test_harness_eval_append_only.py
- .superpowers/sdd/task-13-report.md

Extended:

- src/super_scientist/providers/storage/schema.py
- src/super_scientist/providers/storage/domain_records.py
- tests/integration/storage/test_migrations.py
- tests/integration/storage/test_migration_0005.py

The two pre-0006 tests were adjusted only to recognize 0006 as the current migration
head and to compare a database pinned at revision 0005 against revision-scoped 0005
metadata.

## Residual boundaries

- This is a storage and capability-boundary task. It does not implement Task 14
  candidate/evaluator object graphs, campaign execution, fairness comparisons,
  admission policy, promotion, rollback orchestration, or new CLI behavior.
- A process with operating-system authority over the protected root remains inside
  the local-process trust boundary and can access those files. Deployments must grant
  that root only to the separately privileged evaluation role.
- Ordinary storage intentionally retains protected-content hashes. Those hashes can
  disclose equality across records but cannot reconstruct answer bytes.
- No dependency, CI, network, subprocess, dynamic-import, or runtime plugin surface
  was added.
