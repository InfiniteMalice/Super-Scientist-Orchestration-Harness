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
- Every retained `EvidenceRecord` must declare `external_grounding=PRIMARY_SOURCE` in immutable
  provenance and carry a strict `structured_observation.source_structure` index. A node's complete
  structural location must equal one indexed location; its exact span must be contained by it.
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

The version also retains canonical source-first stage events for source ingestion, node proposal,
relation proposal, and claim formation. Event IDs bind the exact subjects, hashes, full actors,
and UTC timestamps. All records are strict, frozen, and extra-field-forbidden. Storage is
append-only. Editing never
mutates children from an earlier version: a successor contains a complete new snapshot, has a new
`trail_version_id`, points to the exact current head, increments the version by one, and receives
new version-scoped child identifiers.

## Source-first construction

Submit a trail only after the resulting claim version and all evidence records have been admitted.
The retained construction history must prove this source-first sequence:

1. Ingest primary sources, retain artifact bytes, and preserve source structure.
2. Select exact byte-backed UTF-8 spans and indexed structural locations, then propose nodes.
3. Propose typed relations and explicit ordering constraints.
4. Formulate the exact atomic claim version from that graph.
5. Run every deterministic trail check against the complete immutable version scope.
6. Collect fresh independent assessments with their actor, configuration, evidence, checks,
   limitations, result, timestamp, and governing policy.
7. Submit the complete version through `RecordEvidenceTrailVersion`.

The timestamps must be strictly ordered from source retrieval through node and relation proposal,
claim formation, checks, assessments, and final version creation. Missing, reordered, stale, or
content-mismatched stages fail closed.

`EvidenceTrailVersionBuilder.create()` packages a complete version-one snapshot. Its `add_node()`
and `add_relation()` helpers produce an unsubmittable `EvidenceTrailDraft`, derive successor
geometry and causal layers, and never copy prior checks or assessments. `finalize()` accepts only
fresh, successor-bound checks, assessments, and source-first provenance. The transaction handler
still validates source fidelity, policy, lineage, independence, and uniqueness.

## Deterministic validation

`validate_trail(snapshot, inputs)` recomputes the trail from the retained claim, evidence records,
and artifact bytes. It fails closed on, among other conditions:

- missing, duplicated, cross-version, or out-of-scope identifiers;
- artifact size/hash changes, invalid UTF-8, inexact spans, or invalid structural bounds;
- missing/non-primary grounding, invalid structure indices, or fabricated source-first stages;
- incomplete node-role partitions or check/assessment sets;
- unknown relation endpoints, self-relations, noncanonical endpoint-evidence tuples, forged graph
  geometry, invalid relation-specific roles/modalities, cycles, or inconsistent ordering;
- stronger relation modality than the retained claim permits;
- any of the three causal relation types without exact endpoint-span support, deterministic DAG
  layers, strict temporal precedence, and a fresh passed causal-overclaim assessment;
- assessment or approval actors that alias the builder, claim author, any ingestor/source-stage
  actor, model identity, or non-null configuration;
- stored check results or a declared status that differ from recomputation.

The output vocabulary preserves epistemic state: `SUFFICIENT`, `PARTIALLY_SUPPORTING`,
`CONFLICTED`, `INSUFFICIENT`, and `UNANSWERABLE` remain distinct. Structural or provenance failure
produces `INVALID_TRAIL`; it is never converted into success. Opposing evidence is retained rather
than discarded.

`validate_report_binding()` first revalidates the referenced trail, then requires the exact
canonical nonredundant node tuple and the same-order exact span tuple. Contradiction IDs must name
every actual `CONTRADICTS` participant, opposing IDs must name the participating opposing nodes,
and conflicted prose cannot use asserted modality. A report binding cannot transition a claim or
substitute for claim admission.

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
