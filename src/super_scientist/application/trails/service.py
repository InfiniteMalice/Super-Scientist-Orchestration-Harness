from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from super_scientist.application.trails.receipts import AcceptedProposalReceipt
from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.evidence.models import VerificationState
from super_scientist.domain.evidence_trails.authority import (
    CAUSAL_RELATION_TYPES,
    canonical_node_set_hash,
    derive_causal_positions_from_graph,
    derive_geometry_from_graph,
    derive_trail_outcome,
    parse_external_grounding,
    required_causal_support,
    trail_actors_are_independent,
    trusted_assessment_id,
    trusted_check_id,
)
from super_scientist.domain.evidence_trails.models import (
    AssessmentCategory,
    ConstructionMethod,
    EvidenceTrailNode,
    EvidenceTrailRelation,
    EvidenceTrailSnapshot,
    EvidenceTrailVersion,
    ReportSentenceBinding,
    SourceFirstProvenance,
    TrailAssessment,
    TrailCheckCategory,
    TrailCheckResult,
    TrailGeometry,
    TrailOrderingConstraint,
    TrailOutcome,
    TrailReceiptRef,
    TrailValidationInputs,
    TransitionClaimReceiptRef,
)
from super_scientist.domain.evidence_trails.validation import (
    validate_report_binding,
    validate_trail,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import ChangeClassification
from super_scientist.domain.primitives import UtcTimestamp, sha256_hex
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    BindReportSentence,
    ProposeClaim,
    ProposeEvidenceTrailNodes,
    ProposeEvidenceTrailRelations,
    RecordEvidenceTrailVersion,
    RejectionCode,
    TransactionDecision,
    TransitionClaim,
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
    )

type TrailMutationProposal = (
    ProposeEvidenceTrailNodes
    | ProposeEvidenceTrailRelations
    | RecordEvidenceTrailVersion
    | BindReportSentence
)

FIXED_TRAIL_CLASSIFICATION = ChangeClassification(
    target=ChangeTarget.RESEARCH_PROCESS,
    loop_closure=LoopClosure.HUMAN_IN_LOOP,
    persistence=PersistenceScope.RUN_LOCAL,
    verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    grounding=ExternalGrounding.PRIMARY_SOURCE,
    signal=ImprovementSignal.EXTRINSIC_GROUNDED_EXPERIENCE,
)


class EvidenceTrailDraft(BaseModel):
    """A changed graph without validation artifacts; it is not a transaction proposal."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proposal_id: str
    idempotency_key: str
    proposer: ActorIdentity
    approval: Approval | None
    trail_version_id: str
    trail_id: str
    parent_claim_version_id: str
    version: int = Field(strict=True, ge=2)
    parent_trail_version_id: str
    parent_created_at: UtcTimestamp
    source_ids: tuple[str, ...]
    required_node_ids: tuple[str, ...]
    supporting_node_ids: tuple[str, ...]
    opposing_node_ids: tuple[str, ...]
    redundant_node_ids: tuple[str, ...]
    ordering_constraints: tuple[TrailOrderingConstraint, ...]
    geometry: TrailGeometry
    construction_method: ConstructionMethod
    constructed_by: ActorIdentity
    created_at: UtcTimestamp
    governing_policy_hash: str
    nodes: tuple[EvidenceTrailNode, ...]
    relations: tuple[EvidenceTrailRelation, ...]


class EvidenceTrailVersionBuilder:
    """Build complete immutable successor snapshots from the caller's retained head."""

    @staticmethod
    def create(
        *,
        snapshot: EvidenceTrailSnapshot,
        proposal_id: str,
        idempotency_key: str,
        proposer: ActorIdentity,
        approval: Approval | None,
    ) -> RecordEvidenceTrailVersion:
        if snapshot.version.version != 1 or snapshot.version.parent_trail_version_id is not None:
            raise ValueError("a created trail snapshot must be version 1 without a parent")
        return RecordEvidenceTrailVersion(
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            proposer=proposer,
            approval=approval,
            trail_version=snapshot.version,
            nodes=snapshot.nodes,
            relations=snapshot.relations,
            checks=snapshot.checks,
            assessments=snapshot.assessments,
        )

    @staticmethod
    def add_node(
        *,
        current_head: EvidenceTrailSnapshot,
        node: EvidenceTrailNode,
        trail_version_id: str,
        proposal_id: str,
        idempotency_key: str,
        proposer: ActorIdentity,
        approval: Approval | None,
        created_at: UtcTimestamp,
        governing_policy_hash: str,
    ) -> EvidenceTrailDraft:
        return EvidenceTrailVersionBuilder._successor(
            current_head=current_head,
            trail_version_id=trail_version_id,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            proposer=proposer,
            approval=approval,
            created_at=created_at,
            governing_policy_hash=governing_policy_hash,
            added_node=node,
            added_relation=None,
        )

    @staticmethod
    def add_relation(
        *,
        current_head: EvidenceTrailSnapshot,
        relation: EvidenceTrailRelation,
        trail_version_id: str,
        proposal_id: str,
        idempotency_key: str,
        proposer: ActorIdentity,
        approval: Approval | None,
        created_at: UtcTimestamp,
        governing_policy_hash: str,
    ) -> EvidenceTrailDraft:
        return EvidenceTrailVersionBuilder._successor(
            current_head=current_head,
            trail_version_id=trail_version_id,
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            proposer=proposer,
            approval=approval,
            created_at=created_at,
            governing_policy_hash=governing_policy_hash,
            added_node=None,
            added_relation=relation,
        )

    @staticmethod
    def _successor(
        *,
        current_head: EvidenceTrailSnapshot,
        trail_version_id: str,
        proposal_id: str,
        idempotency_key: str,
        proposer: ActorIdentity,
        approval: Approval | None,
        created_at: UtcTimestamp,
        governing_policy_hash: str,
        added_node: EvidenceTrailNode | None,
        added_relation: EvidenceTrailRelation | None,
    ) -> EvidenceTrailDraft:
        prior = current_head.version
        if trail_version_id == prior.trail_version_id:
            raise ValueError("successor trail_version_id must be new")
        node_id_map = {
            node.node_id: _derived_child_id(trail_version_id, "node", node.node_id)
            for node in current_head.nodes
        }
        nodes = tuple(
            node.model_copy(
                update={
                    "node_id": node_id_map[node.node_id],
                    "trail_version_id": trail_version_id,
                }
            )
            for node in current_head.nodes
        )
        if added_node is not None:
            if added_node.node_id in node_id_map.values():
                raise ValueError("added node identifier collides with copied successor nodes")
            nodes = (
                *nodes,
                added_node.model_copy(update={"trail_version_id": trail_version_id}),
            )

        relation_id_map = {
            relation.relation_id: _derived_child_id(
                trail_version_id,
                "relation",
                relation.relation_id,
            )
            for relation in current_head.relations
        }
        relations = tuple(
            relation.model_copy(
                update={
                    "relation_id": relation_id_map[relation.relation_id],
                    "trail_version_id": trail_version_id,
                    "source_node_id": node_id_map[relation.source_node_id],
                    "target_node_id": node_id_map[relation.target_node_id],
                }
            )
            for relation in current_head.relations
        )
        if added_relation is not None:
            if added_relation.relation_id in relation_id_map.values():
                raise ValueError(
                    "added relation identifier collides with copied successor relations"
                )
            if (
                added_relation.source_node_id not in node_id_map
                or added_relation.target_node_id not in node_id_map
            ):
                raise ValueError("added relation endpoints must name nodes in the current head")
            relations = (
                *relations,
                added_relation.model_copy(
                    update={
                        "trail_version_id": trail_version_id,
                        "source_node_id": node_id_map[added_relation.source_node_id],
                        "target_node_id": node_id_map[added_relation.target_node_id],
                    }
                ),
            )

        causal_positions = derive_causal_positions_from_graph(nodes, relations)
        if causal_positions is not None:
            nodes = tuple(
                node.model_copy(update={"causal_position": causal_positions[node.node_id]})
                for node in nodes
            )
        relations = tuple(
            relation.model_copy(update={"causal_support": required_causal_support(relation, nodes)})
            if relation.relation_type in CAUSAL_RELATION_TYPES
            else relation
            for relation in relations
        )

        ordering_constraints = tuple(
            constraint.model_copy(
                update={
                    "constraint_id": _derived_child_id(
                        trail_version_id,
                        "ordering",
                        constraint.constraint_id,
                    ),
                    "before_node_id": node_id_map[constraint.before_node_id],
                    "after_node_id": node_id_map[constraint.after_node_id],
                }
            )
            for constraint in prior.ordering_constraints
        )
        return EvidenceTrailDraft(
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            proposer=proposer,
            approval=approval,
            trail_version_id=trail_version_id,
            trail_id=prior.trail_id,
            parent_claim_version_id=prior.claim_version_id,
            version=prior.version + 1,
            parent_trail_version_id=prior.trail_version_id,
            parent_created_at=prior.created_at,
            source_ids=tuple(dict.fromkeys(node.source_id for node in nodes)),
            required_node_ids=tuple(
                node.node_id for node in nodes if node.role.value == "REQUIRED"
            ),
            supporting_node_ids=tuple(
                node.node_id for node in nodes if node.role.value == "SUPPORTING"
            ),
            opposing_node_ids=tuple(
                node.node_id for node in nodes if node.role.value == "OPPOSING"
            ),
            redundant_node_ids=tuple(
                node.node_id for node in nodes if node.role.value == "REDUNDANT"
            ),
            ordering_constraints=ordering_constraints,
            geometry=derive_geometry_from_graph(nodes, relations),
            construction_method=prior.construction_method,
            constructed_by=proposer,
            created_at=created_at,
            governing_policy_hash=governing_policy_hash,
            nodes=nodes,
            relations=relations,
        )

    @staticmethod
    def finalize(
        *,
        draft: EvidenceTrailDraft,
        claim: AtomicClaim,
        checks: tuple[TrailCheckResult, ...],
        assessments: tuple[TrailAssessment, ...],
        source_first_provenance: SourceFirstProvenance | None = None,
    ) -> RecordEvidenceTrailVersion:
        """Attach freshly produced validation artifacts to a changed graph."""

        claim_version_id = f"{claim.claim_id}:{claim.version}"
        if claim.parent_version_id != draft.parent_claim_version_id:
            raise ValueError("fresh claim must exactly succeed the draft parent claim")
        if not isinstance(
            source_first_provenance.claim_stage_receipt
            if source_first_provenance is not None
            else None,
            TransitionClaimReceiptRef,
        ):
            raise ValueError("successor graph requires a transition-claim receipt")
        expected_check_ids = tuple(
            trusted_check_id(draft.trail_version_id, category) for category in TrailCheckCategory
        )
        expected_assessment_ids = tuple(
            trusted_assessment_id(draft.trail_version_id, category)
            for category in AssessmentCategory
        )
        if tuple(check.check_id for check in checks) != expected_check_ids:
            raise ValueError("fresh checks are required for the successor graph")
        if tuple(assessment.assessment_id for assessment in assessments) != expected_assessment_ids:
            raise ValueError("fresh assessments are required for the successor graph")
        if any(
            check.trail_version_id != draft.trail_version_id
            or check.claim_version_id != claim_version_id
            or check.governing_policy_hash != draft.governing_policy_hash
            for check in checks
        ):
            raise ValueError("fresh checks must bind the exact successor graph and policy")
        if any(
            assessment.trail_version_id != draft.trail_version_id
            or assessment.claim_version_id != claim_version_id
            or assessment.governing_policy_hash != draft.governing_policy_hash
            for assessment in assessments
        ):
            raise ValueError("fresh assessments must bind the exact successor graph and policy")
        if not checks or not assessments:
            raise ValueError("fresh checks and assessments are required")
        if source_first_provenance is None:
            raise ValueError("fresh source-first provenance is required")
        latest_check = max(check.checked_at for check in checks)
        earliest_check = min(check.checked_at for check in checks)
        earliest_assessment = min(assessment.provenance.assessed_at for assessment in assessments)
        latest_assessment = max(assessment.provenance.assessed_at for assessment in assessments)
        if not (
            draft.parent_created_at < earliest_check
            and latest_check < earliest_assessment
            and latest_assessment < draft.created_at
        ):
            raise ValueError(
                "fresh checks and assessments must postdate the parent and predate the successor"
            )
        status = derive_trail_outcome(
            assessments,
            conflicted=bool(draft.opposing_node_ids),
        )
        if status is TrailOutcome.INVALID_TRAIL:
            raise ValueError("fresh assessments must form the complete outcome matrix")
        version = EvidenceTrailVersion(
            trail_version_id=draft.trail_version_id,
            trail_id=draft.trail_id,
            claim_version_id=claim_version_id,
            version=draft.version,
            parent_trail_version_id=draft.parent_trail_version_id,
            source_ids=draft.source_ids,
            required_node_ids=draft.required_node_ids,
            supporting_node_ids=draft.supporting_node_ids,
            opposing_node_ids=draft.opposing_node_ids,
            redundant_node_ids=draft.redundant_node_ids,
            ordering_constraints=draft.ordering_constraints,
            geometry=draft.geometry,
            status=status,
            construction_method=draft.construction_method,
            source_first_provenance=source_first_provenance,
            check_ids=expected_check_ids,
            assessment_ids=expected_assessment_ids,
            constructed_by=draft.constructed_by,
            created_at=draft.created_at,
            governing_policy_hash=draft.governing_policy_hash,
        )
        return RecordEvidenceTrailVersion(
            proposal_id=draft.proposal_id,
            idempotency_key=draft.idempotency_key,
            proposer=draft.proposer,
            approval=draft.approval,
            trail_version=version,
            nodes=draft.nodes,
            relations=draft.relations,
            checks=checks,
            assessments=assessments,
        )


def _derived_child_id(trail_version_id: str, kind: str, prior_id: str) -> str:
    digest = sha256_hex(prior_id.encode("utf-8"))[:16]
    return f"{trail_version_id}:{kind}:{digest}"


def _external_grounding(evidence: object) -> ExternalGrounding | None:
    try:
        return parse_external_grounding(evidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class TrailStageReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def resolve_receipt(
        self,
        reference: TrailReceiptRef,
    ) -> AcceptedProposalReceipt | None: ...


class TrailVersionReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_trail_head(self, trail_id: str) -> tuple[str, int] | None: ...

    def get_snapshot(self, trail_version_id: str) -> EvidenceTrailSnapshot | None: ...

    def validation_inputs(
        self,
        snapshot: EvidenceTrailSnapshot,
    ) -> TrailValidationInputs | None: ...

    def collision_ids(self, snapshot: EvidenceTrailSnapshot) -> tuple[str, ...]: ...

    def resolve_receipt(
        self,
        reference: TrailReceiptRef,
    ) -> AcceptedProposalReceipt | None: ...


class ReportBindingReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_snapshot(self, trail_version_id: str) -> EvidenceTrailSnapshot | None: ...

    def validation_inputs(
        self,
        snapshot: EvidenceTrailSnapshot,
    ) -> TrailValidationInputs | None: ...

    def get_binding(self, binding_id: str) -> ReportSentenceBinding | None: ...

    def resolve_receipt(
        self,
        reference: TrailReceiptRef,
    ) -> AcceptedProposalReceipt | None: ...


class _TrailVersionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    head: tuple[str, int] | None
    prior_snapshot: EvidenceTrailSnapshot | None
    validation_inputs: TrailValidationInputs | None
    collision_ids: tuple[str, ...]
    source_receipts: tuple[AcceptedProposalReceipt | None, ...]
    node_stage_receipt: AcceptedProposalReceipt | None
    relation_stage_receipt: AcceptedProposalReceipt | None
    claim_stage_receipt: AcceptedProposalReceipt | None


class _NodeStageContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    source_receipts: tuple[AcceptedProposalReceipt | None, ...]


class _RelationStageContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    node_stage_receipt: AcceptedProposalReceipt | None
    source_receipts: tuple[AcceptedProposalReceipt | None, ...]


class _ReportBindingContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    snapshot: EvidenceTrailSnapshot | None
    validation_inputs: TrailValidationInputs | None
    existing_binding: ReportSentenceBinding | None
    authority_actors: tuple[ActorIdentity, ...]


class ProposeEvidenceTrailNodesHandler:
    proposal_type = "propose_evidence_trail_nodes"

    def build_context(
        self,
        proposal: ProposeEvidenceTrailNodes,
        reads: HandlerReadCapability,
    ) -> _NodeStageContext:
        capability = cast(TrailStageReadCapability, reads)
        return _NodeStageContext(
            active_policy=capability.policy_snapshot(),
            source_receipts=tuple(
                capability.resolve_receipt(reference) for reference in proposal.source_receipts
            ),
        )

    def decide(
        self,
        proposal: ProposeEvidenceTrailNodes,
        context: _NodeStageContext,
    ) -> TransactionDecision:
        authority_rejection = trail_authority_rejection(
            proposal,
            context.active_policy,
        )
        if authority_rejection is not None:
            return authority_rejection
        if proposal.classification != FIXED_TRAIL_CLASSIFICATION:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.PERMISSION_DENIED,
                "evidence-trail stages require the fixed research-process classification",
            )
        if any(receipt is None for receipt in context.source_receipts):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "node stage requires exact accepted and audited evidence receipts",
            )
        receipts = tuple(receipt for receipt in context.source_receipts if receipt is not None)
        if any(
            not isinstance(receipt.proposal, AddEvidence)
            or receipt.governing_policy_hash != context.active_policy.policy_hash
            for receipt in receipts
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "node-stage evidence receipts must be accepted under the active policy",
            )
        evidence_proposals = tuple(
            receipt.proposal for receipt in receipts if isinstance(receipt.proposal, AddEvidence)
        )
        source_pairs = tuple(
            dict.fromkeys((node.source_id, node.evidence_id) for node in proposal.nodes)
        )
        if (
            len(evidence_proposals) != len(receipts)
            or len({receipt.reference.proposal_id for receipt in receipts}) != len(receipts)
            or len({node.node_id for node in proposal.nodes}) != len(proposal.nodes)
            or tuple(pair[1] for pair in source_pairs)
            != tuple(item.evidence.evidence_id for item in evidence_proposals)
            or any(node.trail_version_id != proposal.trail_version_id for node in proposal.nodes)
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "node stage does not exactly bind its future version and evidence receipts",
            )
        if any(
            _external_grounding(item.evidence) is not ExternalGrounding.PRIMARY_SOURCE
            for item in evidence_proposals
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INSUFFICIENT_GROUNDING,
                "node-stage evidence must prove primary-source grounding",
            )
        approval = proposal.approval
        if approval is None or any(
            approval.approver.actor_id == item.evidence.ingestion_actor_id
            or not trail_actors_are_independent(approval.approver, item.proposer)
            for item in evidence_proposals
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "node stage approval must be independent of every evidence ingestor",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: ProposeEvidenceTrailNodes,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        del proposal, writes
        _require_accepted(decision)


class ProposeEvidenceTrailRelationsHandler:
    proposal_type = "propose_evidence_trail_relations"

    def build_context(
        self,
        proposal: ProposeEvidenceTrailRelations,
        reads: HandlerReadCapability,
    ) -> _RelationStageContext:
        capability = cast(TrailStageReadCapability, reads)
        node_receipt = capability.resolve_receipt(proposal.node_stage_receipt)
        node_proposal = (
            None
            if node_receipt is None
            or not isinstance(node_receipt.proposal, ProposeEvidenceTrailNodes)
            else node_receipt.proposal
        )
        return _RelationStageContext(
            active_policy=capability.policy_snapshot(),
            node_stage_receipt=node_receipt,
            source_receipts=(
                ()
                if node_proposal is None
                else tuple(
                    capability.resolve_receipt(reference)
                    for reference in node_proposal.source_receipts
                )
            ),
        )

    def decide(
        self,
        proposal: ProposeEvidenceTrailRelations,
        context: _RelationStageContext,
    ) -> TransactionDecision:
        authority_rejection = trail_authority_rejection(
            proposal,
            context.active_policy,
        )
        if authority_rejection is not None:
            return authority_rejection
        node_receipt = context.node_stage_receipt
        if (
            proposal.classification != FIXED_TRAIL_CLASSIFICATION
            or node_receipt is None
            or not isinstance(node_receipt.proposal, ProposeEvidenceTrailNodes)
            or node_receipt.governing_policy_hash != context.active_policy.policy_hash
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "relation stage requires the exact accepted node stage and fixed classification",
            )
        node_stage = node_receipt.proposal
        node_ids = tuple(node.node_id for node in node_stage.nodes)
        node_id_set = set(node_ids)
        if (
            proposal.trail_id != node_stage.trail_id
            or proposal.trail_version_id != node_stage.trail_version_id
            or proposal.proposer != node_stage.proposer
            or proposal.node_ids != node_ids
            or proposal.nodes_hash != canonical_node_set_hash(node_stage.nodes)
            or len({relation.relation_id for relation in proposal.relations})
            != len(proposal.relations)
            or any(
                relation.trail_version_id != proposal.trail_version_id
                or relation.source_node_id not in node_id_set
                or relation.target_node_id not in node_id_set
                for relation in proposal.relations
            )
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "relation stage does not exactly bind its accepted node graph",
            )
        if any(receipt is None for receipt in context.source_receipts):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "relation stage cannot resolve every node-stage evidence receipt",
            )
        source_proposals = tuple(
            receipt.proposal
            for receipt in context.source_receipts
            if receipt is not None and isinstance(receipt.proposal, AddEvidence)
        )
        if len(source_proposals) != len(context.source_receipts) or any(
            _external_grounding(item.evidence) is not ExternalGrounding.PRIMARY_SOURCE
            for item in source_proposals
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INSUFFICIENT_GROUNDING,
                "relation stage requires every retained primary-source receipt",
            )
        approval = proposal.approval
        if approval is None or (
            not trail_actors_are_independent(
                approval.approver,
                node_stage.proposer,
            )
            or any(
                approval.approver.actor_id == item.evidence.ingestion_actor_id
                or not trail_actors_are_independent(approval.approver, item.proposer)
                for item in source_proposals
            )
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "relation stage approval must be independent of the node proposer",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: ProposeEvidenceTrailRelations,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        del proposal, writes
        _require_accepted(decision)


class RecordEvidenceTrailVersionHandler:
    proposal_type = "record_evidence_trail_version"

    def build_context(
        self,
        proposal: RecordEvidenceTrailVersion,
        reads: HandlerReadCapability,
    ) -> _TrailVersionContext:
        capability = cast(TrailVersionReadCapability, reads)
        snapshot = proposal.snapshot()
        head = capability.get_trail_head(snapshot.version.trail_id)
        prior_snapshot = None if head is None else capability.get_snapshot(head[0])
        provenance = snapshot.version.source_first_provenance
        return _TrailVersionContext(
            active_policy=capability.policy_snapshot(),
            head=head,
            prior_snapshot=prior_snapshot,
            validation_inputs=capability.validation_inputs(snapshot),
            collision_ids=capability.collision_ids(snapshot),
            source_receipts=tuple(
                capability.resolve_receipt(reference) for reference in provenance.source_receipts
            ),
            node_stage_receipt=capability.resolve_receipt(provenance.node_stage_receipt),
            relation_stage_receipt=capability.resolve_receipt(provenance.relation_stage_receipt),
            claim_stage_receipt=capability.resolve_receipt(provenance.claim_stage_receipt),
        )

    def decide(
        self,
        proposal: RecordEvidenceTrailVersion,
        context: _TrailVersionContext,
    ) -> TransactionDecision:
        authority_rejection = trail_authority_rejection(
            proposal,
            context.active_policy,
            trail=proposal.snapshot(),
            retained=context.validation_inputs,
            authority_actors=tuple(
                receipt.proposal.proposer
                for receipt in (
                    *context.source_receipts,
                    context.node_stage_receipt,
                    context.relation_stage_receipt,
                    context.claim_stage_receipt,
                )
                if receipt is not None
            ),
        )
        if authority_rejection is not None:
            return authority_rejection
        snapshot = proposal.snapshot()
        version = snapshot.version
        if version.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "evidence trail version must name the exact active governance policy",
            )
        if version.constructed_by != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "evidence trail constructor must match the proposal proposer",
            )
        if context.validation_inputs is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "atomic claim and every immutable evidence source must already exist",
            )
        receipt_rejection = trail_receipt_rejection(
            proposal,
            validation_inputs=context.validation_inputs,
            prior_snapshot=context.prior_snapshot,
            source_receipts=context.source_receipts,
            node_stage_receipt=context.node_stage_receipt,
            relation_stage_receipt=context.relation_stage_receipt,
            claim_stage_receipt=context.claim_stage_receipt,
        )
        if receipt_rejection is not None:
            return receipt_rejection
        if any(
            _external_grounding(source.evidence) is not ExternalGrounding.PRIMARY_SOURCE
            for source in context.validation_inputs.sources
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INSUFFICIENT_GROUNDING,
                "every retained evidence source must prove primary-source grounding",
            )
        if context.collision_ids:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "evidence trail record identifiers must be globally new",
            )
        lineage_rejection = _lineage_rejection(proposal.proposal_id, version, context)
        if lineage_rejection is not None:
            return lineage_rejection
        validation = validate_trail(snapshot, context.validation_inputs)
        if validation.outcome.value == "INVALID_TRAIL":
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "evidence trail failed deterministic source-bound validation",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordEvidenceTrailVersion,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        snapshot = proposal.snapshot()
        writes.append_authoritative(snapshot.version)
        for node in snapshot.nodes:
            writes.append_authoritative(node)
        for relation in snapshot.relations:
            writes.append_authoritative(relation)
        for check in snapshot.checks:
            writes.append_authoritative(check)
        for assessment in snapshot.assessments:
            writes.append_authoritative(assessment)
        writes.update_projection(snapshot.version)


class BindReportSentenceHandler:
    proposal_type = "bind_report_sentence"

    def build_context(
        self,
        proposal: BindReportSentence,
        reads: HandlerReadCapability,
    ) -> _ReportBindingContext:
        capability = cast(ReportBindingReadCapability, reads)
        snapshot = capability.get_snapshot(proposal.binding.trail_version_id)
        provenance = None if snapshot is None else snapshot.version.source_first_provenance
        receipt_references = (
            ()
            if provenance is None
            else (
                *provenance.source_receipts,
                provenance.node_stage_receipt,
                provenance.relation_stage_receipt,
                provenance.claim_stage_receipt,
            )
        )
        receipts = tuple(capability.resolve_receipt(reference) for reference in receipt_references)
        return _ReportBindingContext(
            active_policy=capability.policy_snapshot(),
            snapshot=snapshot,
            validation_inputs=(
                None if snapshot is None else capability.validation_inputs(snapshot)
            ),
            existing_binding=capability.get_binding(proposal.binding.binding_id),
            authority_actors=tuple(
                receipt.proposal.proposer for receipt in receipts if receipt is not None
            ),
        )

    def decide(
        self,
        proposal: BindReportSentence,
        context: _ReportBindingContext,
    ) -> TransactionDecision:
        authority_rejection = trail_authority_rejection(
            proposal,
            context.active_policy,
            trail=context.snapshot,
            retained=context.validation_inputs,
            authority_actors=context.authority_actors,
        )
        if authority_rejection is not None:
            return authority_rejection
        binding = proposal.binding
        if binding.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "report binding must name the exact active governance policy",
            )
        if context.existing_binding is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "report sentence binding already exists",
            )
        if context.snapshot is None or context.validation_inputs is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "report binding requires an exact retained trail version and claim",
            )
        if validate_report_binding(binding, context.snapshot, context.validation_inputs):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "report sentence binding failed exact retained-trail validation",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: BindReportSentence,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.binding)


def trail_authority_rejection(
    proposal: TrailMutationProposal,
    snapshot: PolicySnapshot,
    *,
    trail: EvidenceTrailSnapshot | None = None,
    retained: TrailValidationInputs | None = None,
    authority_actors: tuple[ActorIdentity, ...] = (),
    authority_actor_ids: frozenset[str] = frozenset(),
) -> TransactionDecision | None:
    if (
        isinstance(
            proposal,
            (ProposeEvidenceTrailNodes, ProposeEvidenceTrailRelations),
        )
        and proposal.classification != FIXED_TRAIL_CLASSIFICATION
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "evidence-trail stages require the exact fixed classification",
        )
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "new evidence-trail proposal kinds require an active governance policy V2",
        )
    requirement = next(
        (
            item
            for item in policy.adaptation_requirements
            if item.change_target is ChangeTarget.RESEARCH_PROCESS
            and item.persistence is PersistenceScope.RUN_LOCAL
        ),
        None,
    )
    if requirement is None:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "active policy does not govern run-local research-process records",
        )
    if (
        requirement.minimum_verification is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        or ExternalGrounding.PRIMARY_SOURCE not in requirement.permitted_grounding
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "evidence-trail admission does not satisfy the active policy requirement",
        )
    if requirement.protected_evaluation_required or requirement.rollback_required:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "evidence-trail admission cannot satisfy protected-evaluation or rollback flags",
        )
    approval = proposal.approval
    if trail is not None and any(
        not trail_actors_are_independent(
            assessment.provenance.actor,
            authority_actor,
        )
        for assessment in trail.assessments
        for authority_actor in authority_actors
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "trail assessments must be independent of every durable stage actor",
        )
    exact_authority_actors = authority_actors
    exact_authority_actor_ids = set(authority_actor_ids)
    if trail is not None:
        exact_authority_actors = (
            *exact_authority_actors,
            trail.version.constructed_by,
            *(assessment.provenance.actor for assessment in trail.assessments),
        )
    if retained is not None:
        exact_authority_actor_ids.update(
            {
                retained.claim.created_by,
                *(source.evidence.ingestion_actor_id for source in retained.sources),
            }
        )
    if (
        approval is None
        or approval.approver.kind is not requirement.required_approver_kind
        or not trail_actors_are_independent(proposal.proposer, approval.approver)
        or approval.approver.actor_id in exact_authority_actor_ids
        or any(
            not trail_actors_are_independent(approval.approver, authority_actor)
            for authority_actor in exact_authority_actors
        )
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "evidence-trail mutation requires independent policy-matched approval",
        )
    if requirement.required_approver_kind is not ActorKind.HUMAN:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "evidence-trail durable authority is human only",
        )
    return None


def _lineage_rejection(
    proposal_id: str,
    version: EvidenceTrailVersion,
    context: _TrailVersionContext,
) -> TransactionDecision | None:
    if context.head is None:
        if version.version != 1 or version.parent_trail_version_id is not None:
            return _rejected(
                proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "new evidence trail must begin at version 1 without a parent",
            )
        return None
    prior = context.prior_snapshot
    if prior is None:
        return _rejected(
            proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "evidence trail head does not resolve to a complete prior version",
        )
    if (
        version.version != context.head[1] + 1
        or version.parent_trail_version_id != context.head[0]
        or version.trail_id != prior.version.trail_id
        or context.validation_inputs is None
        or context.validation_inputs.claim.parent_version_id != prior.version.claim_version_id
        or version.claim_version_id
        != (f"{context.validation_inputs.claim.claim_id}:{context.validation_inputs.claim.version}")
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "evidence trail edit must exactly succeed its current head and claim lineage",
        )
    return None


def trail_receipt_rejection(
    proposal: RecordEvidenceTrailVersion,
    *,
    validation_inputs: TrailValidationInputs | None,
    prior_snapshot: EvidenceTrailSnapshot | None,
    source_receipts: tuple[AcceptedProposalReceipt | None, ...],
    node_stage_receipt: AcceptedProposalReceipt | None,
    relation_stage_receipt: AcceptedProposalReceipt | None,
    claim_stage_receipt: AcceptedProposalReceipt | None,
    final_transaction_key: tuple[UtcTimestamp, str] | None = None,
    final_audit_sequence: int | None = None,
) -> TransactionDecision | None:
    """Bind a final graph to already accepted, audited source-first stages."""

    snapshot = proposal.snapshot()
    version = snapshot.version
    retained = validation_inputs
    node_receipt = node_stage_receipt
    relation_receipt = relation_stage_receipt
    claim_receipt = claim_stage_receipt
    if (
        retained is None
        or any(receipt is None for receipt in source_receipts)
        or node_receipt is None
        or relation_receipt is None
        or claim_receipt is None
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.MISSING_EVIDENCE,
            "trail requires exact accepted and audited source-first receipts",
        )
    resolved_sources = tuple(receipt for receipt in source_receipts if receipt is not None)
    if (
        not isinstance(node_receipt.proposal, ProposeEvidenceTrailNodes)
        or not isinstance(
            relation_receipt.proposal,
            ProposeEvidenceTrailRelations,
        )
        or any(not isinstance(receipt.proposal, AddEvidence) for receipt in resolved_sources)
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_PROPOSAL,
            "trail receipts resolve to the wrong durable proposal kinds",
        )
    source_proposals = tuple(
        receipt.proposal
        for receipt in resolved_sources
        if isinstance(receipt.proposal, AddEvidence)
    )
    node_stage = node_receipt.proposal
    relation_stage = relation_receipt.proposal
    claim_proposal = claim_receipt.proposal
    receipt_policy_hashes = {
        *(receipt.governing_policy_hash for receipt in resolved_sources),
        node_receipt.governing_policy_hash,
        relation_receipt.governing_policy_hash,
        claim_receipt.governing_policy_hash,
    }
    if receipt_policy_hashes != {version.governing_policy_hash}:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.POLICY_HASH_MISMATCH,
            "every source-first receipt must use the trail's governing policy",
        )

    source_pairs = tuple(
        dict.fromkeys((node.source_id, node.evidence_id) for node in snapshot.nodes)
    )
    projected_evidence = tuple(
        item.evidence.model_copy(update={"verification_state": VerificationState.HASH_VERIFIED})
        for item in source_proposals
    )
    if (
        len(source_proposals) != len(resolved_sources)
        or version.source_ids != tuple(source_id for source_id, _ in source_pairs)
        or tuple(evidence_id for _, evidence_id in source_pairs)
        != tuple(item.evidence.evidence_id for item in source_proposals)
        or projected_evidence != tuple(source.evidence for source in retained.sources)
        or node_stage.trail_id != version.trail_id
        or node_stage.trail_version_id != version.trail_version_id
        or node_stage.classification != FIXED_TRAIL_CLASSIFICATION
        or node_stage.source_receipts != version.source_first_provenance.source_receipts
        or node_stage.nodes != snapshot.nodes
        or node_stage.proposer != version.constructed_by
        or relation_stage.trail_id != version.trail_id
        or relation_stage.trail_version_id != version.trail_version_id
        or relation_stage.classification != FIXED_TRAIL_CLASSIFICATION
        or relation_stage.node_stage_receipt != version.source_first_provenance.node_stage_receipt
        or relation_stage.node_ids != tuple(node.node_id for node in snapshot.nodes)
        or relation_stage.nodes_hash != canonical_node_set_hash(snapshot.nodes)
        or relation_stage.relations != snapshot.relations
        or relation_stage.proposer != version.constructed_by
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_PROPOSAL,
            "source-first receipts do not exactly bind the retained graph",
        )

    claim = retained.claim
    exact_claim_version_id = f"{claim.claim_id}:{claim.version}"
    if version.version == 1:
        claim_matches = (
            isinstance(claim_proposal, ProposeClaim)
            and claim.version == 1
            and claim_proposal.claim == claim
        )
    else:
        claim_matches = (
            isinstance(claim_proposal, TransitionClaim)
            and claim_proposal.next_claim == claim
            and prior_snapshot is not None
            and claim.parent_version_id == prior_snapshot.version.claim_version_id
        )
    if (
        not claim_matches
        or version.claim_version_id != exact_claim_version_id
        or claim_proposal.proposer.actor_id != claim.created_by
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "trail requires the exact fresh contiguous accepted claim receipt",
        )

    if (
        any(
            _receipt_transaction_key(source_receipt) >= _receipt_transaction_key(node_receipt)
            or source_receipt.audit_sequence >= node_receipt.audit_sequence
            for source_receipt in resolved_sources
        )
        or _receipt_transaction_key(node_receipt) >= _receipt_transaction_key(relation_receipt)
        or node_receipt.audit_sequence >= relation_receipt.audit_sequence
        or _receipt_transaction_key(relation_receipt) >= _receipt_transaction_key(claim_receipt)
        or relation_receipt.audit_sequence >= claim_receipt.audit_sequence
        or (
            final_transaction_key is not None
            and _receipt_transaction_key(claim_receipt) >= final_transaction_key
        )
        or (
            final_audit_sequence is not None
            and claim_receipt.audit_sequence >= final_audit_sequence
        )
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_PROPOSAL,
            "source-first receipts are not durably chronological",
        )

    approval = proposal.approval
    authority_actors = (
        *(item.proposer for item in source_proposals),
        node_stage.proposer,
        relation_stage.proposer,
        claim_proposal.proposer,
    )
    if approval is None or any(
        not trail_actors_are_independent(approval.approver, actor) for actor in authority_actors
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "trail approval must be independent of every durable stage actor",
        )
    return None


def _receipt_transaction_key(
    receipt: AcceptedProposalReceipt,
) -> tuple[UtcTimestamp, str]:
    return receipt.transaction_created_at, receipt.proposal.proposal_id


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)
