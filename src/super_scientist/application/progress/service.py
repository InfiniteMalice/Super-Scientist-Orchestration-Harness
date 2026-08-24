from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, ConfigDict

from super_scientist.config.models import GovernancePolicyV2, PolicySnapshot
from super_scientist.domain.identity import ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
    is_authoritative_verification,
)
from super_scientist.domain.improvement.models import ActorRelationship, AssessmentOutcome
from super_scientist.domain.progress.calculations import (
    calculate_progress,
    current_progress_plan,
    detect_false_finish,
    event_advances_progress_head,
    has_unused_budget,
    is_canonical_artifact_ref,
    remaining_budget,
    replay_pending_dependency_ids,
    select_checkpoint_budget,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    CompletionDecision,
    FalseFinishResult,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressValidationEvent,
    RunCheckpoint,
    TerminationReason,
    progress_actors_are_independent,
)
from super_scientist.domain.research_runs.models import ResearchRun
from super_scientist.kernel.admission.engine import AdmissionEngine
from super_scientist.kernel.transactions.models import (
    AppendProgressEvent,
    DecideCompletion,
    RecordProgressPlan,
    RecordRunBudget,
    RecordRunCheckpoint,
    RejectionCode,
    TransactionDecision,
)

type ProgressMutationProposal = (
    RecordProgressPlan
    | AppendProgressEvent
    | RecordRunBudget
    | RecordRunCheckpoint
    | DecideCompletion
)

if TYPE_CHECKING:
    from super_scientist.application.transactions.contracts import (
        HandlerReadCapability,
        HandlerWriteCapability,
    )


class ProgressPlanReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_run(self, run_id: str) -> ResearchRun | None: ...

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None: ...

    def list_plans(self, run_id: str) -> tuple[ProgressPlan, ...]: ...

    def list_subtasks(self, subtask_ids: tuple[str, ...]) -> tuple[ProgressSubtask, ...]: ...


class ProgressEventReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_run(self, run_id: str) -> ResearchRun | None: ...

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None: ...

    def get_subtask(self, subtask_id: str) -> ProgressSubtask | None: ...

    def get_event(self, event_id: str) -> ProgressValidationEvent | None: ...

    def list_plans(self, run_id: str) -> tuple[ProgressPlan, ...]: ...

    def get_progress_head_event(self, run_id: str) -> ProgressValidationEvent | None: ...


class RunBudgetReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_run(self, run_id: str) -> ResearchRun | None: ...

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None: ...

    def get_budget(self, budget_id: str) -> BudgetAllocation | None: ...


class RunCheckpointReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_run(self, run_id: str) -> ResearchRun | None: ...

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None: ...

    def get_checkpoint(self, checkpoint_id: str) -> RunCheckpoint | None: ...

    def list_events(self, plan_version_id: str) -> tuple[ProgressValidationEvent, ...]: ...

    def list_plans(self, run_id: str) -> tuple[ProgressPlan, ...]: ...

    def list_budgets(self, plan_version_id: str) -> tuple[BudgetAllocation, ...]: ...


class CompletionReadCapability(Protocol):
    def policy_snapshot(self) -> PolicySnapshot: ...

    def get_run(self, run_id: str) -> ResearchRun | None: ...

    def get_plan(self, plan_version_id: str) -> ProgressPlan | None: ...

    def get_completion_decision(
        self,
        completion_decision_id: str,
    ) -> CompletionDecision | None: ...

    def list_events(self, plan_version_id: str) -> tuple[ProgressValidationEvent, ...]: ...

    def list_budgets(self, plan_version_id: str) -> tuple[BudgetAllocation, ...]: ...

    def has_retained_evidence(self, evidence_id: str) -> bool: ...


class _ProgressPlanContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    run: ResearchRun | None
    existing_plan: ProgressPlan | None
    prior_plans: tuple[ProgressPlan, ...]
    stored_subtask_ids: frozenset[str]


class _ProgressEventContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    run: ResearchRun | None
    plan: ProgressPlan | None
    subtask: ProgressSubtask | None
    existing_event: ProgressValidationEvent | None
    plans: tuple[ProgressPlan, ...]
    head_event: ProgressValidationEvent | None


class _RunBudgetContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    run: ResearchRun | None
    plan: ProgressPlan | None
    existing_budget: BudgetAllocation | None


class _RunCheckpointContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    run: ResearchRun | None
    plan: ProgressPlan | None
    existing_checkpoint: RunCheckpoint | None
    events: tuple[ProgressValidationEvent, ...]
    plans: tuple[ProgressPlan, ...]
    budgets: tuple[BudgetAllocation, ...]


class _CompletionContext(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_policy: PolicySnapshot
    run: ResearchRun | None
    plan: ProgressPlan | None
    existing_decision: CompletionDecision | None
    events: tuple[ProgressValidationEvent, ...]
    budgets: tuple[BudgetAllocation, ...]
    retained_evidence_ids: frozenset[str]


class RecordProgressPlanHandler:
    proposal_type = "record_progress_plan"

    def build_context(
        self,
        proposal: RecordProgressPlan,
        reads: HandlerReadCapability,
    ) -> _ProgressPlanContext:
        capability = cast(ProgressPlanReadCapability, reads)
        return _ProgressPlanContext(
            active_policy=capability.policy_snapshot(),
            run=capability.get_run(proposal.plan.run_id),
            existing_plan=capability.get_plan(proposal.plan.plan_version_id),
            prior_plans=capability.list_plans(proposal.plan.run_id),
            stored_subtask_ids=frozenset(
                subtask.subtask_id
                for subtask in capability.list_subtasks(
                    tuple(subtask.subtask_id for subtask in proposal.plan.subtasks)
                )
            ),
        )

    def decide(
        self,
        proposal: RecordProgressPlan,
        context: _ProgressPlanContext,
    ) -> TransactionDecision:
        authority_rejection = progress_authority_rejection(proposal, context.active_policy)
        if authority_rejection is not None:
            return authority_rejection
        plan = proposal.plan
        if plan.governing_policy_hash != context.active_policy.policy_hash:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.POLICY_HASH_MISMATCH,
                "progress plan must name the exact active governance policy",
            )
        if context.run is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "research run does not exist",
            )
        if context.existing_plan is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "progress plan already exists",
            )
        expected_version = max((item.version for item in context.prior_plans), default=0) + 1
        if plan.version != expected_version:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "progress plan version must exactly succeed durable run history",
            )
        if any(subtask.subtask_id in context.stored_subtask_ids for subtask in plan.subtasks):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "progress subtask identifier already exists",
            )
        try:
            calculate_progress(plan, ())
        except ValueError:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_DEPENDENCY,
                "progress plan dependency graph or weights are invalid",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordProgressPlan,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.plan)
        for subtask in proposal.plan.subtasks:
            writes.append_authoritative(subtask)


class AppendProgressEventHandler:
    proposal_type = "append_progress_event"

    def build_context(
        self,
        proposal: AppendProgressEvent,
        reads: HandlerReadCapability,
    ) -> _ProgressEventContext:
        capability = cast(ProgressEventReadCapability, reads)
        event = proposal.event
        return _ProgressEventContext(
            active_policy=capability.policy_snapshot(),
            run=capability.get_run(event.run_id),
            plan=capability.get_plan(event.plan_version_id),
            subtask=capability.get_subtask(event.subtask_id),
            existing_event=capability.get_event(event.event_id),
            plans=capability.list_plans(event.run_id),
            head_event=capability.get_progress_head_event(event.run_id),
        )

    def decide(
        self,
        proposal: AppendProgressEvent,
        context: _ProgressEventContext,
    ) -> TransactionDecision:
        authority_rejection = progress_authority_rejection(proposal, context.active_policy)
        if authority_rejection is not None:
            return authority_rejection
        event = proposal.event
        if event.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_hash_rejection(proposal.proposal_id, "progress event")
        lineage_rejection = _plan_lineage_rejection(
            proposal.proposal_id,
            event.run_id,
            event.plan_version_id,
            context.run,
            context.plan,
        )
        if lineage_rejection is not None:
            return lineage_rejection
        if context.subtask is None or context.subtask.plan_version_id != event.plan_version_id:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_DEPENDENCY,
                "progress event subtask does not belong to its plan",
            )
        if context.existing_event is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "progress event already exists",
            )
        if not event_advances_progress_head(event, context.plans, context.head_event):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "progress event must advance the current plan and event head",
            )
        if event.completion_proposer != proposal.proposer:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "progress event completion proposer must match proposal proposer",
            )
        if (
            event.validator != context.subtask.validator
            or event.validator_version != context.subtask.validator_version
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "progress event must use the subtask validator identity and version",
            )
        if event.requested_status is ProgressStatus.VALIDATED:
            run = context.run
            if run is None or (
                not progress_actors_are_independent(event.validator, run.creator)
                or not progress_actors_are_independent(
                    event.validator,
                    event.completion_proposer,
                )
                or event.relationship_to_run_creator is not ActorRelationship.INDEPENDENT
                or event.relationship_to_completion_proposer is not ActorRelationship.INDEPENDENT
                or not event.are_independent
                or not is_authoritative_verification(event.validator_category)
                or event.result is not AssessmentOutcome.PASSED
            ):
                return _rejected(
                    proposal.proposal_id,
                    RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                    "VALIDATED progress requires recomputed independent validation",
                )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: AppendProgressEvent,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.event)
        writes.update_projection(proposal.event)


class RecordRunBudgetHandler:
    proposal_type = "record_run_budget"

    def build_context(
        self,
        proposal: RecordRunBudget,
        reads: HandlerReadCapability,
    ) -> _RunBudgetContext:
        capability = cast(RunBudgetReadCapability, reads)
        budget = proposal.budget
        return _RunBudgetContext(
            active_policy=capability.policy_snapshot(),
            run=capability.get_run(budget.run_id),
            plan=capability.get_plan(budget.plan_version_id),
            existing_budget=capability.get_budget(budget.budget_id),
        )

    def decide(
        self,
        proposal: RecordRunBudget,
        context: _RunBudgetContext,
    ) -> TransactionDecision:
        authority_rejection = progress_authority_rejection(proposal, context.active_policy)
        if authority_rejection is not None:
            return authority_rejection
        budget = proposal.budget
        if budget.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_hash_rejection(proposal.proposal_id, "run budget")
        lineage_rejection = _plan_lineage_rejection(
            proposal.proposal_id,
            budget.run_id,
            budget.plan_version_id,
            context.run,
            context.plan,
        )
        if lineage_rejection is not None:
            return lineage_rejection
        if context.existing_budget is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "run budget already exists",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordRunBudget,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.budget)


class RecordRunCheckpointHandler:
    proposal_type = "record_run_checkpoint"

    def build_context(
        self,
        proposal: RecordRunCheckpoint,
        reads: HandlerReadCapability,
    ) -> _RunCheckpointContext:
        capability = cast(RunCheckpointReadCapability, reads)
        checkpoint = proposal.checkpoint
        return _RunCheckpointContext(
            active_policy=capability.policy_snapshot(),
            run=capability.get_run(checkpoint.run_id),
            plan=capability.get_plan(checkpoint.plan_version_id),
            existing_checkpoint=capability.get_checkpoint(checkpoint.checkpoint_id),
            events=capability.list_events(checkpoint.plan_version_id),
            plans=capability.list_plans(checkpoint.run_id),
            budgets=capability.list_budgets(checkpoint.plan_version_id),
        )

    def decide(
        self,
        proposal: RecordRunCheckpoint,
        context: _RunCheckpointContext,
    ) -> TransactionDecision:
        authority_rejection = progress_authority_rejection(proposal, context.active_policy)
        if authority_rejection is not None:
            return authority_rejection
        checkpoint = proposal.checkpoint
        if checkpoint.governing_policy_hash != context.active_policy.policy_hash:
            return _policy_hash_rejection(proposal.proposal_id, "run checkpoint")
        lineage_rejection = _plan_lineage_rejection(
            proposal.proposal_id,
            checkpoint.run_id,
            checkpoint.plan_version_id,
            context.run,
            context.plan,
        )
        if lineage_rejection is not None:
            return lineage_rejection
        if context.existing_checkpoint is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "run checkpoint already exists",
            )
        plan = context.plan
        if plan is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "progress plan does not exist",
            )
        if current_progress_plan(context.plans, checkpoint.run_id) != plan:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_LINEAGE,
                "checkpoint must target the current highest progress plan",
            )
        try:
            summary = calculate_progress(plan, context.events)
        except ValueError:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_DEPENDENCY,
                "checkpoint progress history is invalid",
            )
        if checkpoint.validated_subtask_ids != summary.validated_subtask_ids:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_DEPENDENCY,
                "checkpoint validated progress does not match durable history",
            )
        expected_pending_dependencies = replay_pending_dependency_ids(
            plan,
            summary.validated_subtask_ids,
        )
        if checkpoint.pending_dependency_ids != expected_pending_dependencies:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_DEPENDENCY,
                "checkpoint pending dependencies do not match durable history",
            )
        budget = select_checkpoint_budget(checkpoint, context.budgets)
        if budget is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "checkpoint requires an applicable durable run budget",
            )
        if checkpoint.remaining_budget != remaining_budget(budget) or (
            checkpoint.telemetry != budget.telemetry
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.UNMATCHED_BUDGETS,
                "checkpoint budget and telemetry do not match the latest durable allocation",
            )
        if not all(
            is_canonical_artifact_ref(reference)
            for reference in (
                *checkpoint.artifact_refs,
                *checkpoint.raw_log_refs,
                *checkpoint.raw_transaction_refs,
            )
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "checkpoint artifact references are not canonical content addresses",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: RecordRunCheckpoint,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.checkpoint)


class DecideCompletionHandler:
    proposal_type = "decide_completion"

    def build_context(
        self,
        proposal: DecideCompletion,
        reads: HandlerReadCapability,
    ) -> _CompletionContext:
        capability = cast(CompletionReadCapability, reads)
        completion = proposal.completion_proposal
        referenced_evidence_ids = {
            evidence_id
            for item in completion.checklist
            if item.completed
            for evidence_id in item.evidence_ids
        }
        referenced_evidence_ids.update(completion.final_validation.evidence_ids)
        return _CompletionContext(
            active_policy=capability.policy_snapshot(),
            run=capability.get_run(completion.run_id),
            plan=capability.get_plan(completion.plan_version_id),
            existing_decision=capability.get_completion_decision(
                proposal.completion_decision.completion_decision_id
            ),
            events=capability.list_events(completion.plan_version_id),
            budgets=capability.list_budgets(completion.plan_version_id),
            retained_evidence_ids=frozenset(
                evidence_id
                for evidence_id in referenced_evidence_ids
                if capability.has_retained_evidence(evidence_id)
            ),
        )

    def decide(
        self,
        proposal: DecideCompletion,
        context: _CompletionContext,
    ) -> TransactionDecision:
        authority_rejection = progress_authority_rejection(proposal, context.active_policy)
        if authority_rejection is not None:
            return authority_rejection
        completion = proposal.completion_proposal
        decision = proposal.completion_decision
        if (
            completion.governing_policy_hash != context.active_policy.policy_hash
            or decision.governing_policy_hash != context.active_policy.policy_hash
        ):
            return _policy_hash_rejection(proposal.proposal_id, "completion record")
        lineage_rejection = _plan_lineage_rejection(
            proposal.proposal_id,
            completion.run_id,
            completion.plan_version_id,
            context.run,
            context.plan,
        )
        if lineage_rejection is not None:
            return lineage_rejection
        if context.existing_decision is not None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ALREADY_EXISTS,
                "completion decision already exists",
            )
        if not context.budgets:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "completion decision requires a durable run budget",
            )
        if (
            completion.proposer != proposal.proposer
            or decision.run_id != completion.run_id
            or decision.plan_version_id != completion.plan_version_id
            or decision.completion_proposal_id != completion.completion_proposal_id
            or decision.checklist != completion.checklist
            or decision.final_validator_result != completion.final_validation.result
            or decision.termination_reason != completion.termination_reason
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.ENTITY_ID_MISMATCH,
                "completion proposal and decision bindings do not match",
            )
        run = context.run
        plan = context.plan
        if run is None or plan is None:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_ENTITY,
                "completion run or plan does not exist",
            )
        required_evidence_ids = (
            *(
                evidence_id
                for item in completion.checklist
                if item.completed
                for evidence_id in item.evidence_ids
            ),
            *completion.final_validation.evidence_ids,
        )
        if (
            not completion.final_validation.evidence_ids
            or any(item.completed and not item.evidence_ids for item in completion.checklist)
            or any(
                evidence_id not in context.retained_evidence_ids
                for evidence_id in required_evidence_ids
            )
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.MISSING_EVIDENCE,
                "completion gates require nonempty retained evidence",
            )
        final_validation = completion.final_validation
        if (
            final_validation.actor != run.final_validator
            or final_validation.actor_version != run.final_validator_version
            or decision.decision_authority != final_validation.actor
            or not progress_actors_are_independent(final_validation.actor, run.creator)
            or not progress_actors_are_independent(final_validation.actor, completion.proposer)
            or completion.relationship_to_run_creator is not ActorRelationship.INDEPENDENT
            or completion.relationship_to_completion_proposer is not ActorRelationship.INDEPENDENT
            or not completion.are_independent
            or not is_authoritative_verification(final_validation.category)
        ):
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "completion requires the declared independent final validator",
            )
        latest_budget = max(
            context.budgets,
            key=lambda item: (item.recorded_at, item.budget_id),
        )
        try:
            finding = detect_false_finish(
                voluntary_termination=completion.voluntary_termination,
                claims_completion=completion.claims_completion,
                final_validator_result=final_validation.result,
                plan=plan,
                events=context.events,
                unused_budget=has_unused_budget(latest_budget),
            )
        except ValueError:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_DEPENDENCY,
                "completion progress history is invalid",
            )
        if decision.false_finish != finding:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "completion decision false-finish finding was not recomputed exactly",
            )
        if finding.result is FalseFinishResult.FALSE_FINISH:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.FALSE_FINISH,
                "voluntary completion failed final validation with progress and budget remaining",
            )
        success = (
            completion.claims_completion
            and completion.termination_reason is TerminationReason.SUCCESS
            and all(item.completed for item in completion.checklist)
            and final_validation.result is AssessmentOutcome.PASSED
        )
        if decision.accepted is not success:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INVALID_PROPOSAL,
                "completion decision does not match the ordered finalization gates",
            )
        if completion.claims_completion and not success:
            return _rejected(
                proposal.proposal_id,
                RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
                "completion claim did not pass every finalization gate",
            )
        return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    def project(
        self,
        proposal: DecideCompletion,
        decision: TransactionDecision,
        writes: HandlerWriteCapability,
    ) -> None:
        _require_accepted(decision)
        writes.append_authoritative(proposal.completion_decision)


def progress_authority_rejection(
    proposal: ProgressMutationProposal,
    snapshot: PolicySnapshot,
) -> TransactionDecision | None:
    policy = snapshot.policy
    if not isinstance(policy, GovernancePolicyV2):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "new progress proposal kinds require an active governance policy V2",
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
        or ExternalGrounding.HUMAN_JUDGMENT not in requirement.permitted_grounding
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "progress admission does not satisfy the active policy requirement",
        )
    if requirement.protected_evaluation_required or requirement.rollback_required:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INSUFFICIENT_GROUNDING,
            "progress admission cannot satisfy protected-evaluation or rollback requirements",
        )
    approval = proposal.approval
    if (
        approval is None
        or approval.approver.kind is not requirement.required_approver_kind
        or not progress_actors_are_independent(proposal.proposer, approval.approver)
    ):
        return _rejected(
            proposal.proposal_id,
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
            "progress mutation requires independent policy-matched approval",
        )
    if requirement.required_approver_kind is not ActorKind.HUMAN:
        return _rejected(
            proposal.proposal_id,
            RejectionCode.PERMISSION_DENIED,
            "progress durable authority is human only",
        )
    return None


def _plan_lineage_rejection(
    proposal_id: str,
    run_id: str,
    plan_version_id: str,
    run: ResearchRun | None,
    plan: ProgressPlan | None,
) -> TransactionDecision | None:
    if run is None or plan is None:
        return _rejected(
            proposal_id,
            RejectionCode.MISSING_ENTITY,
            "progress mutation requires an existing run and plan",
        )
    if plan.run_id != run_id or plan.plan_version_id != plan_version_id:
        return _rejected(
            proposal_id,
            RejectionCode.INVALID_LINEAGE,
            "progress plan does not belong to the named run",
        )
    return None


def _policy_hash_rejection(proposal_id: str, label: str) -> TransactionDecision:
    return _rejected(
        proposal_id,
        RejectionCode.POLICY_HASH_MISMATCH,
        f"{label} must name the exact active governance policy",
    )


def _require_accepted(decision: TransactionDecision) -> None:
    if not decision.accepted:
        raise ValueError("rejected proposals cannot be projected")


def _rejected(
    proposal_id: str,
    code: RejectionCode,
    message: str,
) -> TransactionDecision:
    return AdmissionEngine.rejected(proposal_id, code, message)
