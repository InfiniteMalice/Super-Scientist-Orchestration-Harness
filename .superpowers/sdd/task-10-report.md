# Task 10 implementation report: hypothesis and representation storage

## Outcome

Implemented migration `0005_hypotheses_and_representations` on base
`abba01a0b4830a3aa21991297c9d2215c1672bb1`. The change adds strict append-only
storage for representational primitives and the hypothesis/model/verification/revision
record graph, plus mutable primitive and hypothesis head projections. It remains a
storage-only task: Task 11 primitive lifecycle behavior and Task 12 hypothesis execution
and admission services are not implemented.

## Delivered behavior

- Ten authoritative append-only record tables retain primitive versions and evaluations,
  hypothesis versions, inert executable-model metadata, verification mechanism specs and
  results, bounded simulation-result metadata, counterexamples, contiguous revisions, and
  hypothesis admission decisions.
- Seventeen append-only normalized reference tables retain every ordered lineage and
  consumed-record tuple. Both the database and fixed decoders enforce retained targets,
  while repository reads require exact zero-based positions and canonical JSON equality.
- Primitive identities are unique by `(primitive_id, semantic_version)` and hypothesis
  identities by `(hypothesis_id, version)`. Revisions bind exact prior and immediately
  resulting versions of one retained hypothesis through composite foreign keys.
- Primitive and hypothesis heads are the only mutable 0005 tables. Their fixed repositories
  require an exact stored identity/version/status tuple, reject booleans as integer versions,
  and verify stored projections on every read.
- Verification mechanism and result categories are materialized separately and constrained
  as exact formal-verifier, independent-deterministic-checker, or learned-judge pairs.
  Model execution mode is materialized through exact composite model/result relationships.
- Model specifications carry either content hash, media type, size, and inert name metadata,
  or one of two closed source-controlled simulator names. The strict contract and relational
  shape reject source text, import paths, entry points, argv, commands, URLs, executable
  fields, arbitrary simulator names, and mixed artifact/simulator authority.
- Every record is strict, frozen, schema-versioned, canonically serialized, content hashed,
  and decoded with unknown-field rejection. All public repositories accept only the current
  SQLAlchemy connection; the existing generic repository machinery remains private.
- Update and delete triggers protect all 27 authoritative and normalized-reference history
  tables. SQLite foreign-key enforcement rejects orphan lineage and consumed references.
- Runtime SQLAlchemy metadata exactly matches revision 0005. Clean, genuine-0001, 0004-to-0005,
  0005-to-0004 downgrade, and re-upgrade paths retain prior records.
- The moving-head migration test now expects 0005; revision-specific 0002, 0003, 0004, and
  0005 tests remain explicitly pinned to their own revisions.
- No transaction proposal, admission handler, simulator dispatch, primitive business logic,
  hypothesis loop, dependency, CI, network, subprocess, dynamic-import, or generic runtime
  authority was added.

## TDD evidence

- Migration RED: the new clean-upgrade test failed with Alembic `CommandError: Can't locate
  revision identified by '0005_hypotheses_and_representations'`; **1 failed in 3.38s**.
- Contract/repository RED: the combined new test command failed collection because the strict
  0005 records and repositories were absent; **12 migration items collected, 1 collection
  error in 2.76s**.
- First complete contract run exposed incorrect parent-column assumptions in the ordered-FK
  helper, missing exact revision-parent uniqueness, and incomplete exports; **23 passed,
  10 failed in 29.41s**. Four root-cause probes then passed **4/4 in 5.84s**.
- The discriminator tamper test initially stopped at the stronger database category-pair
  check. It was changed to a database-valid but JSON-incoherent category pair, proving the
  decoder boundary; the exact test passed **1/1 in 4.54s**.
- Final focused Task 10 suite: **33 passed in 29.74s** after formatting, with the earlier
  complete GREEN run at **33 passed in 27.19s**.
- Moving-head RED: all revision-pinned cases passed while the two intentional head-tracking
  assertions still expected 0004; **86 passed, 2 failed in 83.79s**. Updating only those two
  moving-head assertions made their file GREEN at **5 passed in 11.66s**.

## Migration and quality evidence

- Full migration and append-only chain across revisions 0002 through 0005: **88 passed in
  84.59s**.
- Explicit Alembic probe: clean head `0005_hypotheses_and_representations`; metadata operations
  `0`; downgrade `0004_behavioral_rules`; re-upgrade `0005_hypotheses_and_representations`;
  separately seeded genuine-0001 evidence retained after upgrade to 0005.
- Entire storage integration suite: **104 passed, 3 skipped in 106.21s**.
- Repository-wide Ruff lint: **all checks passed**.
- Owned formatter surface: **6 files already formatted**.
- Strict mypy: **success, 68 source files**.
- `git diff --check`: **passed** with only the repository's configured LF-to-CRLF notice.
- Definitive full suite: **1,223 passed, 3 skipped in 1,020.69s**.

## Files and scope

- Added `alembic/versions/0005_hypotheses_and_representations.py`.
- Extended `src/super_scientist/providers/storage/schema.py` and
  `src/super_scientist/providers/storage/domain_records.py`.
- Added `tests/integration/storage/test_migration_0005.py` and
  `tests/property/test_hypothesis_primitive_append_only.py`.
- Updated only the two moving-head expectations in
  `tests/integration/storage/test_migrations.py`.

No blocker or deferred Task 10 requirement remains. Task 11 and Task 12 intentionally own the
domain/application behavior above these storage contracts.
