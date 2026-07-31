# Task 8 implementation report: behavioral-rule storage

## Outcome

Implemented behavioral-rule storage on base
`3ab066031a4dcab2cbe328db7b5400c57466b1f2`. Revision `0004_behavioral_rules` adds
immutable incidents, rule versions, reviewer assessments, consolidation decisions, regression
cases, normalized ordered reference history, and mutable rule heads. This task adds storage
contracts and repositories only; behavioral-rule admission, review orchestration, duplicate and
conflict classification, and governed consolidation remain Task 9 work.

## Delivered behavior

- Five strict, frozen, schema-versioned record contracts retain stable identifiers, semantic
  versions, actors, policy hashes, evidence, assessments, decisions, dissent, and regression
  lineage without granting runtime authority.
- Rule versions enforce unique `(rule_id, semantic_version)` identities and retain at least one
  concrete source incident. Reviewer assessment IDs are primary keys.
- Seven append-only ordered reference tables materialize rule-version supersession, reviewer,
  consolidation, and regression incident/assessment lineage with foreign keys, position checks,
  and per-owner reference uniqueness.
- Fixed connection-only repositories canonicalize JSON, recompute SHA-256 content hashes, validate
  strict decoders, and require materialized ordered references and their exact zero-based positions
  to equal the canonical JSON tuples on every read.
- Consolidation decisions materialize `resulting_rule_version_id` as a nullable foreign key and
  require it to exactly equal canonical JSON. Rule-producing actions require a result; reject and
  human-escalation outcomes prohibit one. Missing and tampered targets are rejected.
- Rule versions materialize `supersedes_rule_version_ids` in canonical order through an immutable
  self-referential lineage table. Both owner and predecessor IDs reference retained immutable rule
  versions; missing predecessors, reference drift, and position drift are rejected.
- `BehavioralRuleHeadRepository` is the only mutable rule projection. It accepts only an exact
  stored `(rule_id, rule_version_id, semantic_version, status)` target and detects incoherent stored
  heads.
- Update and delete triggers protect both authoritative record tables and their normalized
  reference history. Regression cases reference retained incidents instead of copying or replacing
  them.
- Alembic and runtime SQLAlchemy metadata match with no pending schema operations. Migration 0001
  remains unchanged, and clean, genuine-0001, 0003-to-0004, downgrade, and re-upgrade paths work.
- Core and test dependencies are unchanged. No CI configuration, runtime execution authority,
  network behavior, dynamic import, shell path, or Task 9 application service was added.

## TDD evidence

- Migration RED: `pytest
  tests/integration/storage/test_migration_0004.py::test_clean_upgrade_creates_behavioral_rule_storage
  -v` collected one test and failed because the 0004 tables/head did not exist; **1 failed in
  3.64s**.
- Repository-contract RED: `pytest tests/property/test_rule_append_only.py -v` failed collection
  with `ModuleNotFoundError: super_scientist.domain.behavioral_rules`; **1 collection error in
  1.36s**.
- Migration GREEN: `pytest tests/integration/storage/test_migration_0004.py -v` - **9 passed in
  8.65s**.
- Repository/property GREEN: `pytest tests/property/test_rule_append_only.py -v` - **8 passed in
  10.75s**.
- The first complete migration/storage chain exposed one historical test that called
  `upgrade_database()` while asserting exact revision 0003: **132 passed, 1 failed in 95.09s**.
  That revision-specific test now explicitly upgrades to its own `REVISION`; its focused rerun
  passed **1 test in 2.95s**.
- Fresh migration/storage chain: **133 passed in 105.99s**.
- Entire storage integration suite: **92 passed, 3 skipped in 58.90s**.

### Canonical-review fix loop

- Focused review RED: `pytest tests/integration/storage/test_migration_0004.py
  tests/property/test_rule_append_only.py -q` - **12 failed, 11 passed in 18.78s**. The failures
  demonstrated the missing supersession table/keys/triggers, missing nullable decision-result
  column/key, absent repository bindings, accepted missing references, undetected lineage/result
  drift, and missing strict action/result shape validation.
- Exact-position RED: the focused supersession-tamper probe passed reference drift but accepted a
  `(0, 2)` position gap, producing **1 failed, 1 passed in 3.52s**.
- Exact-position GREEN: after decoding exact `(position, reference)` pairs, both tamper cases passed
  - **2 passed in 3.30s**.
- Final focused migration/property GREEN: **24 passed in 18.35s**.
- Revision-specific clean migration coverage now targets `_upgrade_to(..., REVISION)`. The 0003
  preservation test seeds and verifies an actual progress-plan row and evidence-trail-version row,
  including their required research-run and claim-version ancestors.

## Migration and quality evidence

- Explicit Alembic round trip reported clean head `0004_behavioral_rules`, downgrade
  `0003_progress_and_evidence_trails`, no metadata upgrade operations at either 0004 head, and
  re-upgrade to 0004.
- A separately created genuine-0001 database upgraded to 0004 while preserving its seeded legacy
  evidence row.
- Full Ruff lint: `ruff check .` - **all checks passed**.
- Review-fix formatter surface: `ruff format --check` over revision 0004, behavioral-rule
  contracts, schema/repositories, and the two changed Task 8 tests - **6 files already formatted**.
- The configured repository-wide formatter check is not clean at this base: it reports 22
  pre-existing Task 7/legacy files. Task 8 did not reformat unrelated files or widen its diff; this
  inherited formatter debt should be reconciled before the release-wide quality gate.
- Strict mypy: `mypy src` - **success, 64 source files**.
- Focused migration chain: **30 passed in 23.83s**.
- Entire storage integration suite: **92 passed, 3 skipped in 62.69s**.
- Full suite: `pytest -q` - **1,105 passed, 3 skipped in 569.73s**.
- `git diff --check` - **passed** (Git emitted only the repository's configured LF-to-CRLF
  notices while staging).

## Commits

- Original implementation: `feat: add behavioral rule storage`
- Canonical-review fix: `fix: enforce behavioral rule lineage`
