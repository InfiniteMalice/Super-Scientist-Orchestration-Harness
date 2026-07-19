from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
    ResourceBudget,
    ResourceUsage,
)
from super_scientist.domain.progress.models import (
    BudgetAllocation,
    BudgetReserves,
    BudgetUsage,
    CompletionChecklistItem,
    CompletionChecklistStep,
    CompletionDecision,
    CompletionProposal,
    ExecutionTelemetry,
    FalseFinishFinding,
    FalseFinishResult,
    ProgressStatus,
    RunCheckpoint,
    TerminationReason,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
POLICY_HASH = "a" * 64


def _actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity.model(
        actor_id=actor_id,
        provider_id="provider-1",
        model_id="model-1",
        adapter_id="adapter-1",
        created_at=NOW,
    ).model_copy(update={"configuration_hash": "b" * 64})


def _resource_budget(tokens: int) -> ResourceBudget:
    return ResourceBudget(
        cost_usd=10.0,
        compute_units=10.0,
        tokens=tokens,
        elapsed_seconds=100.0,
        tool_calls=10,
        human_interventions=1,
    )


def _resource_usage(tokens: int) -> ResourceUsage:
    return ResourceUsage(
        cost_usd=1.0,
        compute_units=1.0,
        tokens=tokens,
        elapsed_seconds=10.0,
        tool_calls=1,
        human_interventions=0,
    )


def _reserves() -> BudgetReserves:
    budget = _resource_budget(100)
    return BudgetReserves(
        exploration=budget,
        implementation=budget,
        verification=budget,
        recovery=budget,
        finalization=budget,
    )


def _usage() -> BudgetUsage:
    usage = _resource_usage(10)
    return BudgetUsage(
        exploration=usage,
        implementation=usage,
        verification=usage,
        recovery=usage,
        finalization=usage,
    )


def _telemetry() -> ExecutionTelemetry:
    return ExecutionTelemetry(
        episodes=2,
        model_calls=3,
        input_tokens=40,
        output_tokens=20,
        tool_calls=4,
        operations=5,
        files_changed=2,
        elapsed_seconds=30.0,
        verification_seconds=5.0,
        repeated_actions=1,
        reverted_actions=1,
        checkpoints=1,
        timed_out=False,
        termination_reason=None,
        estimated_cost_usd=2.0,
    )


def _final_validation() -> AssessmentProvenance:
    return AssessmentProvenance(
        actor=_actor("final-validator"),
        actor_version="final-validator-v1",
        category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        deterministic_or_learned="DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=(),
        evidence_ids=("final-evidence",),
        checks_run=("final-check",),
        limitations=("Limited to retained artifacts",),
        result=AssessmentOutcome.PASSED,
        meaningful_confidence=None,
        assessed_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


def _checklist() -> tuple[CompletionChecklistItem, ...]:
    return tuple(
        CompletionChecklistItem(
            step=step,
            completed=True,
            detail=f"Completed {step.value}",
            evidence_ids=(f"evidence-{index}",),
        )
        for index, step in enumerate(CompletionChecklistStep, start=1)
    )


def test_status_and_termination_taxonomies_are_closed_and_exact() -> None:
    assert tuple(ProgressStatus) == (
        ProgressStatus.NOT_STARTED,
        ProgressStatus.IN_PROGRESS,
        ProgressStatus.BLOCKED,
        ProgressStatus.PROVISIONALLY_COMPLETE,
        ProgressStatus.VALIDATED,
        ProgressStatus.INVALIDATED,
        ProgressStatus.ABANDONED,
    )
    assert tuple(TerminationReason) == (
        TerminationReason.SUCCESS,
        TerminationReason.TIMEOUT,
        TerminationReason.BUDGET_EXHAUSTED,
        TerminationReason.EARLY_EXIT,
        TerminationReason.USER_CANCELLED,
        TerminationReason.HARNESS_ERROR,
        TerminationReason.ENVIRONMENT_ERROR,
        TerminationReason.VALIDATOR_ERROR,
        TerminationReason.SAFETY_BLOCK,
        TerminationReason.UNRECOVERABLE_STATE,
    )


def test_budget_allocation_keeps_five_reserves_and_typed_telemetry() -> None:
    allocation = BudgetAllocation(
        budget_id="budget-1",
        run_id="run-1",
        plan_version_id="plan-1",
        reserves=_reserves(),
        usage=_usage(),
        telemetry=_telemetry(),
        recorded_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    assert tuple(allocation.reserves.model_dump()) == (
        "exploration",
        "implementation",
        "verification",
        "recovery",
        "finalization",
    )
    assert allocation.telemetry.termination_reason is None
    with pytest.raises(ValidationError):
        BudgetAllocation.model_validate({**allocation.model_dump(), "unexpected": True})


def test_checkpoint_retains_raw_content_addressed_log_and_transaction_references() -> None:
    raw_log = ArtifactRef(
        sha256="b" * 64,
        size_bytes=12,
        media_type="application/jsonl",
        relative_path=f"sha256/bb/{'b' * 64}",
    )
    raw_transaction = ArtifactRef(
        sha256="c" * 64,
        size_bytes=13,
        media_type="application/json",
        relative_path=f"sha256/cc/{'c' * 64}",
    )
    checkpoint = RunCheckpoint(
        checkpoint_id="checkpoint-1",
        run_id="run-1",
        plan_version_id="plan-1",
        validated_subtask_ids=("collect",),
        pending_dependency_ids=("analyze",),
        hypothesis_ids=("hypothesis-1",),
        artifact_refs=(raw_log,),
        environment_snapshot_id="environment-1",
        attempted_operations=("operation-1",),
        failures=("validator unavailable once",),
        remaining_budget=_reserves(),
        next_recommended_action="Retry independent validation",
        raw_log_refs=(raw_log,),
        raw_transaction_refs=(raw_transaction,),
        telemetry=_telemetry(),
        occurred_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    assert checkpoint.raw_log_refs == (raw_log,)
    assert checkpoint.raw_transaction_refs == (raw_transaction,)


def test_completion_records_preserve_the_exact_ordered_checklist() -> None:
    proposal = CompletionProposal(
        completion_proposal_id="completion-proposal-1",
        run_id="run-1",
        plan_version_id="plan-1",
        proposer=_actor("completion-proposer"),
        voluntary_termination=False,
        claims_completion=True,
        termination_reason=TerminationReason.SUCCESS,
        checklist=_checklist(),
        final_validation=_final_validation(),
        relationship_to_run_creator=ActorRelationship.INDEPENDENT,
        relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
        are_independent=True,
        submitted_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    decision = CompletionDecision(
        completion_decision_id="completion-decision-1",
        run_id="run-1",
        plan_version_id="plan-1",
        completion_proposal_id=proposal.completion_proposal_id,
        decision_authority=_actor("final-validator"),
        accepted=True,
        checklist=proposal.checklist,
        final_validator_result=AssessmentOutcome.PASSED,
        false_finish=FalseFinishFinding(
            result=FalseFinishResult.NOT_FALSE_FINISH,
            voluntary_termination=False,
            claims_completion=True,
            final_validator_failed=False,
            meaningful_validated_progress=True,
            unused_budget=True,
            reasons=(),
        ),
        termination_reason=TerminationReason.SUCCESS,
        decided_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    assert tuple(item.step for item in decision.checklist) == tuple(CompletionChecklistStep)

    payload = proposal.model_dump(mode="python")
    payload["checklist"] = tuple(reversed(proposal.checklist))
    with pytest.raises(ValidationError, match="ordered"):
        CompletionProposal.model_validate(payload)


def test_completion_validator_configuration_alias_cannot_claim_independence() -> None:
    proposer = _model_actor("completion-proposer-alias")
    validator = _model_actor("final-validator-alias")
    final_validation = _final_validation().model_copy(update={"actor": validator})

    with pytest.raises(ValidationError, match="independent"):
        CompletionProposal(
            completion_proposal_id="completion-proposal-alias",
            run_id="run-1",
            plan_version_id="plan-1",
            proposer=proposer,
            voluntary_termination=False,
            claims_completion=True,
            termination_reason=TerminationReason.SUCCESS,
            checklist=_checklist(),
            final_validation=final_validation,
            relationship_to_run_creator=ActorRelationship.INDEPENDENT,
            relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
            are_independent=True,
            submitted_at=NOW,
            governing_policy_hash=POLICY_HASH,
        )
