# Auditable Evidence-Trail Stages Design

## Problem

Task 7 currently stores caller-constructed source-first events inside an evidence-trail version.
Their hashes and timestamps are internally consistent but self-attested, so they cannot prove that
source ingestion, node proposal, relation proposal, and claim formation were independently accepted
in that order. Successor construction also carries the prior trail status and retained claim rather
than deriving a fresh status from a newly accepted claim version.

## Constraints

- Keep migration `0003` and every existing table unchanged.
- Add no dependency and expose no generic repository or write authority.
- Preserve legacy `AddEvidence`, `ProposeClaim`, `TransitionClaim`, and `EvidenceRecord` JSON and
  canonical hashes.
- Preserve all existing Task 2/4/5/6/7 graph, policy, storage, and replay validation.
- Add only `propose_evidence_trail_nodes` and `propose_evidence_trail_relations` to the closed proposal
  union and fixed router.
- Commit the implementation separately from `bc59b77` as
  `fix: make evidence trail stages auditable`.

## Considered Approaches

1. **Resolve immutable receipt references from existing transactions and audit events.** Store only
   exact proposal/audit identifiers and hashes in Task 7 provenance. Resolve authoritative times,
   decisions, actors, content, policy, and audit order from durable storage. This is the selected
   approach because it is fail-closed and needs no schema change.
2. **Copy transaction times into trail provenance.** This avoids lookup work but leaves chronology
   caller-controlled and permits self-computed hashes, so it does not establish authority.
3. **Create dedicated stage tables.** This makes stages directly queryable but violates the explicit
   no-migration constraint and duplicates already authoritative transaction/audit records.

## Durable Proposal and Receipt Model

`ProposeEvidenceTrailNodes` is a strict transaction proposal. It names the future trail and trail
version, the fixed research-process classification, exact accepted `AddEvidence` receipt references,
and the exact proposed nodes. Every node belongs to that future version and its source/evidence pair
must correspond one-for-one with the retained accepted evidence proposals.

`ProposeEvidenceTrailRelations` is the matching strict V1 proposal. It names the same future trail
and trail version, the exact accepted node-stage receipt, the canonical ordered node identifiers and
node-set content hash, and the exact proposed relations. Every relation belongs to that future version
and has endpoints in the accepted node stage.

Both stage proposals are durable and audited through the existing coordinator. Accepted stage
proposals intentionally project no trail-table rows. Rejected stages remain durable/audited but never
produce an accepted receipt.

Task 7 provenance uses strict `AcceptedProposalReceiptRef` values containing proposal ID/hash and
audit event ID/hash. It contains ordered source receipts, one node-stage receipt, one relation-stage
receipt, and a typed claim receipt whose kind is `propose_claim` for claim version 1 or
`transition_claim` for every successor claim version. It contains no caller timestamp or derived
stage-event hash.

`StoredTransaction` exposes the existing database `created_at` column as a strict UTC timestamp. A
narrow read-only receipt resolver combines `TransactionRepository` and `AuditRepository`. It returns
a receipt only when the transaction is accepted and exactly one verified audit event binds the same
proposal, decision, policy hash, and persistence flag; exact receipt resolution also binds the stored
proposal hash and audit ID/hash. The resolver has no write, commit, rollback, generic repository, or
runtime authority.

## Ordering and Binding

Live stage and final handlers receive no current coordinator timestamp. They prove chronology only
by resolving referenced receipts that were already committed and audited before handler execution;
the coordinator then persists and audits the current proposal after its decision. Replay orders
stored transactions by `(created_at, proposal_id)`, matching the existing repository order, and uses
audit-chain sequence as the authoritative audit order. A receipt chain is valid only when:

1. every retained accepted `AddEvidence` transaction precedes the node-stage transaction;
2. every corresponding source audit precedes the node-stage audit;
3. the node stage precedes the relation stage in both transaction time and audit sequence;
4. the relation stage precedes the accepted claim proposal/transition in both orders;
5. the claim transaction precedes the final trail transaction; and
6. replay observes the final trail audit after every referenced stage audit.

Receipt proposal kinds, exact canonical proposal hashes, actors, trail/version IDs, retained
source/evidence IDs, nodes, relations, claim version/content hash, and governing policy must all match
the final snapshot. Missing, rejected, unaudited, backdated, reordered, or wrong-kind records fail
closed. The final transaction may retain human-readable `created_at` fields for immutable domain
records, but those fields never establish stage chronology.

## Successors and Status

A graph-changing successor remains an `EvidenceTrailDraft`. It must be finalized with newly accepted
node and relation stage receipts followed by a newly accepted `TransitionClaim` receipt for the next
contiguous AtomicClaim version. Its checks and assessments bind that fresh claim version. It cannot
reuse or backdate a parent stage. A report binding against an unchanged graph remains independent and
does not require a successor.

`finalize()` derives `EvidenceTrailVersion.status` from the same pure assessment outcome function used
by validation and replay. The caller supplies no status override. This permits legitimate transitions
to `CONFLICTED`, `INSUFFICIENT`, and `UNANSWERABLE` while rejecting forged status values.

## Identity Relations

Exact retained identity provenance is parsed from the existing string-only
`EvidenceRecord.provenance` mapping without changing the legacy evidence model. A strict typed parser
reads only the exact `entity_id` and `event_id` keys and requires present values to be nonblank.
`SAME_ENTITY` requires equal `entity_id` values from the two endpoint evidence records. `SAME_EVENT`
independently requires equal `event_id` values. Endpoint temporal equality is still required by its
relation schema but can never substitute for event identity. Missing, malformed, mismatched, or
wrong-endpoint identity fails live admission and replay.

## Shared Historical Authority

A pure trail/stage authority validator receives the exact proposal, its fixed classification, the
governing historical `PolicySnapshot`, retained evidence, and all authority actors. It verifies the
V2 `RESEARCH_PROCESS`/`RUN_LOCAL` requirement, independent deterministic verification, primary-source
grounding, protected/rollback fail-close flags, human approval, and complete actor independence.
The classification is exactly `RESEARCH_PROCESS`, `HUMAN_IN_LOOP`, `RUN_LOCAL`,
`INDEPENDENT_DETERMINISTIC_CHECK`, `PRIMARY_SOURCE`, and
`EXTRINSIC_GROUNDED_EXPERIENCE`; every field is source-controlled rather than merely checking the
target/persistence pair.

Live handlers call this validator with the active stored policy. Workspace replay first derives the
historically active policy at each audit event, then calls the same validator for every accepted node
stage, relation stage, final trail, and report binding. An audit event that uses a registered but
historically inactive policy, dependent approval, wrong fixed classification, duplicate/weak
requirement, non-primary source, or unsupported flag invalidates the workspace even when graph
validation succeeds. Merely registering an inactive policy is permitted until an accepted governance
transition activates it.

## Verification

Five TDD clusters cover durable stage absence/ordering, fresh successor claims, derived status,
typed entity/event identity, and shared live/replay policy authority. Focused proposal-union, policy,
storage, trail, workspace-integrity, and replay suites run before Ruff, strict mypy, diff validation,
and one complete pytest run. Exact RED/GREEN commands and results are appended to the Task 7 report.
