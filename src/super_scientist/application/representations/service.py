from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING, Protocol

from super_scientist.application.representations.records import primitive_version_from_storage
from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
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
from super_scientist.domain.representations.models import (
    ConceptOverlap,
    PrimitiveStatus,
    PrimitiveUse,
    PrimitiveVersion,
    classify_concept_overlap,
)
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    AdmitPrimitiveVersion,
    ProposePrimitiveVersion,
    RecordPrimitiveEvaluation,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.domain_records import PrimitiveVersionRecord
from super_scientist.providers.storage.repositories import StorageIntegrityError

if TYPE_CHECKING:
    from super_scientist.application.transactions.coordinator import TransactionCoordinator

FIXED_PRIMITIVE_CLASSIFICATION = ChangeClassification(
    target=ChangeTarget.SKILL,
    loop_closure=LoopClosure.HUMAN_IN_LOOP,
    persistence=PersistenceScope.PERSISTENT_SKILL,
    verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    grounding=ExternalGrounding.CONTROLLED_EXPERIMENT,
    signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
)

_PROMOTABLE_STATUSES = frozenset(
    {
        PrimitiveStatus.LOCALLY_USEFUL,
        PrimitiveStatus.REPLICATED,
        PrimitiveStatus.STABILIZED,
    }
)

type PrimitiveMutationProposal = (
    ProposePrimitiveVersion | RecordPrimitiveEvaluation | AdmitPrimitiveVersion
)


class PrimitiveRetentionResolver(Protocol):
    def get_stored_version(self, version_id: str) -> PrimitiveVersionRecord | None: ...

    def get_head(self, primitive_id: str) -> tuple[str, str, PrimitiveStatus] | None: ...


def primitive_use_rejection(
    candidate_version_id: str,
    *,
    resolver: PrimitiveRetentionResolver,
    use: PrimitiveUse,
) -> RejectionCode | None:
    del use
    try:
        stored = resolver.get_stored_version(candidate_version_id)
        if stored is None:
            return RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED
        retained = primitive_version_from_storage(stored)
        head = resolver.get_head(retained.primitive_id)
        stored_status = PrimitiveStatus(stored.status.value)
    except (StorageIntegrityError, TypeError, ValueError):
        return RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED
    exact_head = (stored.primitive_version_id, stored.semantic_version, stored_status)
    if (
        retained.primitive_version_id != candidate_version_id
        or retained.semantic_version != stored.semantic_version
        or retained.status is not stored_status
        or head != exact_head
        or retained.status not in _PROMOTABLE_STATUSES
    ):
        return RejectionCode.EXPERIMENTAL_PRIMITIVE_QUARANTINED
    return None


def evaluator_independence_rejection(
    *,
    primitive_author: ActorIdentity,
    evaluator: ActorIdentity,
    check_actors: tuple[ActorIdentity, ...],
    approver: ActorIdentity,
) -> RejectionCode | None:
    actors = (primitive_author, evaluator, *check_actors, approver)
    if any(not _fully_independent(left, right) for left, right in combinations(actors, 2)):
        return RejectionCode.CIRCULAR_EVALUATOR_APPROVAL
    return None


def primitive_mutation_authority_rejection(
    proposal: PrimitiveMutationProposal,
    snapshot: PolicySnapshot,
    *,
    authority_actors: tuple[ActorIdentity, ...] = (),
    promotion: bool = False,
    protected_evaluation: bool = False,
    rollback_present: bool = False,
) -> TransactionDecision | None:
    if proposal.classification != FIXED_PRIMITIVE_CLASSIFICATION:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "representational primitive proposals require the exact fixed classification",
        )
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "representational primitive proposals require an active governance policy V2",
        )
    requirement = next(
        (
            item
            for item in policy.adaptation_requirements
            if item.change_target is ChangeTarget.SKILL
            and item.persistence is PersistenceScope.PERSISTENT_SKILL
        ),
        None,
    )
    if requirement is None:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "active policy does not govern persistent representational primitives",
        )
    if (
        requirement.minimum_verification is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        or ExternalGrounding.CONTROLLED_EXPERIMENT not in requirement.permitted_grounding
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "active policy does not permit the fixed primitive verification and grounding",
        )
    approval = proposal.approval
    if (
        approval is None
        or requirement.required_approver_kind is not ActorKind.HUMAN
        or approval.approver.kind is not ActorKind.HUMAN
        or not _fully_independent(proposal.proposer, approval.approver)
        or any(
            not _fully_independent(approval.approver, authority_actor)
            for authority_actor in authority_actors
        )
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "primitive mutation requires independent human approval",
        )
    if promotion and requirement.protected_evaluation_required and not protected_evaluation:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "protected evaluation required by active primitive policy",
        )
    if promotion and requirement.rollback_required and not rollback_present:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "rollback target required by active primitive policy",
        )
    return None


def projected_primitive_status(
    candidate: PrimitiveVersion,
    retained: tuple[PrimitiveVersion, ...],
) -> PrimitiveStatus:
    overlap = classify_concept_overlap(candidate, retained)
    if overlap in {ConceptOverlap.EXACT_DUPLICATE, ConceptOverlap.SEMANTIC_DUPLICATE}:
        return PrimitiveStatus.DUPLICATE_SUSPECTED
    return candidate.status


def status_is_promotable(status: PrimitiveStatus) -> bool:
    return status in _PROMOTABLE_STATUSES


def _fully_independent(left: ActorIdentity, right: ActorIdentity) -> bool:
    if left.actor_id == right.actor_id:
        return False
    if (
        left.kind is ActorKind.MODEL
        and right.kind is ActorKind.MODEL
        and (left.configuration_hash is None or right.configuration_hash is None)
    ):
        return False
    optional_identity_fields = (
        "provider_id",
        "model_id",
        "adapter_id",
        "configuration_hash",
    )
    return all(
        getattr(left, field) is None
        or getattr(right, field) is None
        or getattr(left, field) != getattr(right, field)
        for field in optional_identity_fields
    )


class RepresentationService:
    """Thin public facade; the shared coordinator retains mutation authority."""

    def __init__(self, coordinator: TransactionCoordinator) -> None:
        self._coordinator = coordinator

    def propose(self, proposal: ProposePrimitiveVersion) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def evaluate(self, proposal: RecordPrimitiveEvaluation) -> TransactionDecision:
        return self._coordinator.submit(proposal)

    def admit(self, proposal: AdmitPrimitiveVersion) -> TransactionDecision:
        return self._coordinator.submit(proposal)


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)
