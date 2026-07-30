from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.identity import ActorKind, are_independent
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.research_runs.models import (
    ResearchRun,
    ResearchRunEvent,
    ResearchRunEventType,
)
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    AppendResearchRunEvent,
    CreateResearchRun,
    RejectionCode,
    TransactionDecision,
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
    )


class ResearchRunReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_run(self, run_id: str) -> ResearchRun | None: ...

    def list_run_events(self, run_id: str) -> tuple[ResearchRunEvent, ...]: ...


class _ResearchRunContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    existing_run: ResearchRun | None
    events: tuple[ResearchRunEvent, ...]


class CreateResearchRunHandler:
    proposal_type = "create_research_run"

    def build_context(
        self,
        proposal: CreateResearchRun,
        reads: HandlerReadCapability,
    ) -> _ResearchRunContext:
        capability = cast(ResearchRunReadCapability, reads)
        return _ResearchRunContext(
            active_policy=capability.policy_snapshot(),
            existing_run=capability.get_run(proposal.run.run_id),
            events=(),
        )

    def decide(
        self,
        proposal: CreateResearchRun,
        context: _ResearchRunContext,
    ) -> TransactionDecision:
        authority_rejection = _run_authority_rejection(proposal, context.active_policy)
        if authority_rejection is not None:
            return authority_rejection
        if context.existing_run is not None:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "research run already exists",
            )
        if proposal.run.creator != proposal.proposer:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "research run creator must match proposal proposer",
            )
        if not are_independent(proposal.proposer, proposal.run.final_validator):
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "research run final validator must be independent of the proposer",
            )
        if proposal.run.active_governance_policy_hash != context.active_policy.policy_hash:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "research run must name the active governance policy",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: CreateResearchRun,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.run)


class AppendResearchRunEventHandler:
    proposal_type = "append_research_run_event"

    def build_context(
        self,
        proposal: AppendResearchRunEvent,
        reads: HandlerReadCapability,
    ) -> _ResearchRunContext:
        capability = cast(ResearchRunReadCapability, reads)
        return _ResearchRunContext(
            active_policy=capability.policy_snapshot(),
            existing_run=capability.get_run(proposal.event.run_id),
            events=capability.list_run_events(proposal.event.run_id),
        )

    def decide(
        self,
        proposal: AppendResearchRunEvent,
        context: _ResearchRunContext,
    ) -> TransactionDecision:
        authority_rejection = _run_authority_rejection(proposal, context.active_policy)
        if authority_rejection is not None:
            return authority_rejection
        run = context.existing_run
        event = proposal.event
        if run is None:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "research run does not exist",
            )
        if event.governing_policy_hash != context.active_policy.policy_hash:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "research run event must name the active governance policy",
            )
        if event.actor != proposal.proposer:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "research run event actor must match proposal proposer",
            )
        if event.sequence != len(context.events) + 1:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "research run event sequence must exactly succeed durable history",
            )
        if not context.events and event.event_type is not ResearchRunEventType.STARTED:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                "first research run event must be STARTED",
            )
        if context.events and context.events[-1].event_type in {
            ResearchRunEventType.SUCCEEDED,
            ResearchRunEventType.FAILED,
            ResearchRunEventType.CANCELLED,
        }:
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_STATUS_TRANSITION,
                "terminal research runs cannot receive more events",
            )
        if event.event_type is ResearchRunEventType.FINAL_VALIDATION_ACCEPTED:
            final_validation = event.final_validation
            if (
                final_validation is None
                or final_validation.actor != run.final_validator
                or final_validation.actor_version != run.final_validator_version
            ):
                return AdmissionEngine.rejected(
                    proposal.proposal_id,
                    RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                    "final validation must come from the declared validator and version",
                )
        if event.event_type is ResearchRunEventType.SUCCEEDED and not any(
            prior.event_type is ResearchRunEventType.FINAL_VALIDATION_ACCEPTED
            for prior in context.events
        ):
            return AdmissionEngine.rejected(
                proposal.proposal_id,
                RejectionCode.FALSE_FINISH,
                "research run cannot succeed without accepted final validation",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: AppendResearchRunEvent,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.event)
        writes.update_projection(proposal.event)


def _run_authority_rejection(
    proposal: CreateResearchRun | AppendResearchRunEvent,
    snapshot: PolicySnapshot,
) -> TransactionDecision | None:
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return AdmissionEngine.rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "new persistent proposal kinds require an active governance policy V2",
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
        return AdmissionEngine.rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "active policy does not govern run-local research-process records",
        )
    if (
        requirement.minimum_verification is not VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK
        or ExternalGrounding.HUMAN_JUDGMENT not in requirement.permitted_grounding
    ):
        return AdmissionEngine.rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "research-run admission does not satisfy the active policy requirement",
        )
    approval = proposal.approval
    if (
        approval is None
        or approval.approver.kind is not requirement.required_approver_kind
        or not are_independent(proposal.proposer, approval.approver)
    ):
        return AdmissionEngine.rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "research-run mutation requires independent policy-matched approval",
        )
    if requirement.required_approver_kind is not ActorKind.HUMAN:
        return AdmissionEngine.rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "research-run durable authority is human only",
        )
    return None


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")
