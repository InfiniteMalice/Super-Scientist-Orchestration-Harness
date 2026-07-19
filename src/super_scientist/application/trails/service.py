from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.evidence_trails.models import (
    EvidenceTrailNode,
    EvidenceTrailRelation,
    EvidenceTrailSnapshot,
    EvidenceTrailVersion,
    ReportSentenceBinding,
    TrailValidationInputs,
)
from super_scientist.domain.evidence_trails.validation import (
    validate_report_binding,
    validate_trail,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.primitives import UtcTimestamp, sha256_hex
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    Approval,
    BindReportSentence,
    RecordEvidenceTrailVersion,
    RejectionCode,
    TransactionDecision,
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
    )

type TrailMutationProposal = RecordEvidenceTrailVersion | BindReportSentence


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
    ) -> RecordEvidenceTrailVersion:
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
    ) -> RecordEvidenceTrailVersion:
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
    ) -> RecordEvidenceTrailVersion:
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

        relation_ids = tuple(relation.relation_id for relation in relations)
        node_ids = tuple(node.node_id for node in nodes)
        evidence_ids = tuple(dict.fromkeys(node.evidence_id for node in nodes))
        check_id_map = {
            check.check_id: _derived_child_id(trail_version_id, "check", check.check_id)
            for check in current_head.checks
        }
        checks = tuple(
            check.model_copy(
                update={
                    "check_id": check_id_map[check.check_id],
                    "trail_version_id": trail_version_id,
                    "node_ids": node_ids,
                    "relation_ids": relation_ids,
                    "evidence_ids": evidence_ids,
                    "checked_at": created_at,
                }
            )
            for check in current_head.checks
        )
        check_ids = tuple(check.check_id for check in checks)
        assessments = tuple(
            assessment.model_copy(
                update={
                    "assessment_id": _derived_child_id(
                        trail_version_id,
                        "assessment",
                        assessment.assessment_id,
                    ),
                    "trail_version_id": trail_version_id,
                    "node_ids": node_ids,
                    "relation_ids": relation_ids,
                    "evidence_ids": evidence_ids,
                    "provenance": assessment.provenance.model_copy(
                        update={
                            "evidence_ids": evidence_ids,
                            "checks_run": check_ids,
                            "governing_policy_hash": governing_policy_hash,
                        }
                    ),
                }
            )
            for assessment in current_head.assessments
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
        version = prior.model_copy(
            update={
                "trail_version_id": trail_version_id,
                "version": prior.version + 1,
                "parent_trail_version_id": prior.trail_version_id,
                "source_ids": tuple(dict.fromkeys(node.source_id for node in nodes)),
                "required_node_ids": tuple(
                    node.node_id for node in nodes if node.role.value == "REQUIRED"
                ),
                "supporting_node_ids": tuple(
                    node.node_id for node in nodes if node.role.value == "SUPPORTING"
                ),
                "opposing_node_ids": tuple(
                    node.node_id for node in nodes if node.role.value == "OPPOSING"
                ),
                "redundant_node_ids": tuple(
                    node.node_id for node in nodes if node.role.value == "REDUNDANT"
                ),
                "ordering_constraints": ordering_constraints,
                "check_ids": check_ids,
                "assessment_ids": tuple(
                    assessment.assessment_id for assessment in assessments
                ),
                "constructed_by": proposer,
                "created_at": created_at,
                "governing_policy_hash": governing_policy_hash,
            }
        )
        return RecordEvidenceTrailVersion(
            proposal_id=proposal_id,
            idempotency_key=idempotency_key,
            proposer=proposer,
            approval=approval,
            trail_version=version,
            nodes=nodes,
            relations=relations,
            checks=checks,
            assessments=assessments,
        )


def _derived_child_id(trail_version_id: str, kind: str, prior_id: str) -> str:
    digest = sha256_hex(prior_id.encode("utf-8"))[:16]
    return f"{trail_version_id}:{kind}:{digest}"


class TrailVersionReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_trail_head(self, trail_id: str) -> tuple[str, int] | None: ...

    def get_snapshot(self, trail_version_id: str) -> EvidenceTrailSnapshot | None: ...

    def validation_inputs(
        self,
        snapshot: EvidenceTrailSnapshot,
    ) -> TrailValidationInputs | None: ...

    def collision_ids(self, snapshot: EvidenceTrailSnapshot) -> tuple[str, ...]: ...


class ReportBindingReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_snapshot(self, trail_version_id: str) -> EvidenceTrailSnapshot | None: ...

    def validation_inputs(
        self,
        snapshot: EvidenceTrailSnapshot,
    ) -> TrailValidationInputs | None: ...

    def get_binding(self, binding_id: str) -> ReportSentenceBinding | None: ...


class _TrailVersionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    head: tuple[str, int] | None
    prior_snapshot: EvidenceTrailSnapshot | None
    validation_inputs: TrailValidationInputs | None
    collision_ids: tuple[str, ...]


class _ReportBindingContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    snapshot: EvidenceTrailSnapshot | None
    validation_inputs: TrailValidationInputs | None
    existing_binding: ReportSentenceBinding | None


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
        return _TrailVersionContext(
            active_policy=capability.policy_snapshot(),
            head=head,
            prior_snapshot=prior_snapshot,
            validation_inputs=capability.validation_inputs(snapshot),
            collision_ids=capability.collision_ids(snapshot),
        )

    def decide(
        self,
        proposal: RecordEvidenceTrailVersion,
        context: _TrailVersionContext,
    ) -> TransactionDecision:
        authority_rejection = trail_authority_rejection(proposal, context.active_policy)
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
        return _ReportBindingContext(
            active_policy=capability.policy_snapshot(),
            snapshot=snapshot,
            validation_inputs=(
                None if snapshot is None else capability.validation_inputs(snapshot)
            ),
            existing_binding=capability.get_binding(proposal.binding.binding_id),
        )

    def decide(
        self,
        proposal: BindReportSentence,
        context: _ReportBindingContext,
    ) -> TransactionDecision:
        authority_rejection = trail_authority_rejection(proposal, context.active_policy)
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
) -> TransactionDecision | None:
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
        requirement.minimum_verification
        is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
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
    if (
        approval is None
        or approval.approver.kind is not requirement.required_approver_kind
        or not are_independent(proposal.proposer, approval.approver)
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
        or version.claim_version_id != prior.version.claim_version_id
    ):
        return _rejected(
            proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "evidence trail edit must exactly succeed its current head and atomic claim",
        )
    return None


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)
