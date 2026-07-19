# Source-bound evidence trails

Evidence trails are immutable, source-first graph snapshots that explain how an atomic claim is
connected to retained primary-source bytes. A trail is traceability evidence, not proof and not an
independent authority to admit, promote, or rewrite a claim.

## What is retained

Each `EvidenceTrailVersion` names one exact `claim_version_id`, the active governance-policy hash,
its immutable parent, and complete sets of required, supporting, opposing, and redundant nodes.
Its children are all version-scoped:

- `EvidenceTrailNode` binds a source identifier and immutable `EvidenceRecord` to an exact UTF-8
  span, content hash, structural locator, temporal/causal position, role, necessity, and confidence.
- `EvidenceTrailRelation` binds two nodes using the closed relation vocabulary (`SUPPORTS`,
  `CONTRADICTS`, ordering relations, `CAUSES_CANDIDATE`, qualification/explanation, identity,
  dependency, and alternative-explanation relations).
- `TrailCheckResult` records one exact deterministic result for every required check category.
- `TrailAssessment` retains independent assessment provenance for necessity, groundedness,
  relation fidelity, counterevidence, causal-overclaim risk, rubric fidelity, contamination, and
  answerability.
- `ReportSentenceBinding` derives a report sentence from one already accepted trail version. It
  retains its exact source nodes and spans, contradictions, opposing evidence, uncertainty,
  modality, outcome, claim version, and policy hash.

All records are strict, frozen, and extra-field-forbidden. Storage is append-only. Editing never
mutates children from an earlier version: a successor contains a complete new snapshot, has a new
`trail_version_id`, points to the exact current head, increments the version by one, and receives
new version-scoped child identifiers.

## Source-first construction

Construct a trail only after the claim version and all evidence records have been admitted and the
artifact bytes have been retained. The normal application sequence is:

1. Read an exact atomic claim version and hash-verified evidence records.
2. Read artifact bytes through artifact verification; do not accept caller-supplied replacement
   text.
3. Select exact byte-backed UTF-8 spans and structural bounds, then create nodes.
4. Add typed relations, explicit ordering constraints, and explicit causal support where causality
   is proposed.
5. Run every deterministic trail check and retain the full check scope and findings.
6. Collect all independent assessments with their actor, configuration, evidence, checks,
   limitations, result, timestamp, and governing policy.
7. Submit the complete version through `RecordEvidenceTrailVersion`.

`EvidenceTrailVersionBuilder.create()` packages a complete version-one snapshot. Its `add_node()`
and `add_relation()` helpers clone the complete current head into an immutable successor and add
the requested record. The helpers do not read durable state or grant admission authority; the
transaction handler still validates source fidelity, policy, lineage, and uniqueness.

## Deterministic validation

`validate_trail(snapshot, inputs)` recomputes the trail from the retained claim, evidence records,
and artifact bytes. It fails closed on, among other conditions:

- missing, duplicated, cross-version, or out-of-scope identifiers;
- artifact size/hash changes, invalid UTF-8, inexact spans, or invalid structural bounds;
- incomplete node-role partitions or check/assessment sets;
- unknown relation endpoints, self-relations, invalid evidence scope, cycles, or inconsistent
  ordering and temporal assertions;
- stronger relation modality than the retained claim permits;
- causal assertions without explicit retained support and a passed independent causal-overclaim
  assessment;
- assessment actors/configurations that are repeated, dependent on the builder or source actor,
  incomplete, non-authoritative, or detached from exact checks/evidence;
- stored check results or a declared status that differ from recomputation.

The output vocabulary preserves epistemic state: `SUFFICIENT`, `PARTIALLY_SUPPORTING`,
`CONFLICTED`, `INSUFFICIENT`, and `UNANSWERABLE` remain distinct. Structural or provenance failure
produces `INVALID_TRAIL`; it is never converted into success. Opposing evidence is retained rather
than discarded.

`validate_report_binding()` first revalidates the referenced trail, then requires exact claim,
policy, outcome, node, span, contradiction, opposing-evidence, and modality bindings. A report
binding cannot transition a claim or substitute for claim admission.

## Governed admission and recovery

The fixed transaction router exposes only `record_evidence_trail_version` and
`bind_report_sentence`. Governance V1 rejects both proposal kinds. Under V2, admission requires the
exact run-local research-process requirement, independent deterministic verification, primary
source grounding, and independent human approval. Protected-evaluation or rollback requirements
fail closed because these handlers cannot satisfy those authorities.

Accepted trail-version projection writes the version, nodes, relations, checks, assessments, and
head in one database transaction. A failure rolls the whole projection back. Rejected proposals
retain their transaction and audit decision but project no trail records. Report bindings are
appended separately only after their referenced snapshot validates.

Whole-workspace verification reconstructs all six trail/binding projections and every trail head
from accepted audited transactions. It then rereads retained claims, evidence, and artifact bytes
and reruns semantic validation. Missing, extra, corrupt, cross-trail, reparented, rewound, or forged
state therefore fails before the next workspace mutation.
