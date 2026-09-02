from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
)
from super_scientist.domain.progress.calculations import detect_false_finish
from super_scientist.domain.progress.models import (
    FalseFinishResult,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressValidationEvent,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "a" * 64


def _progress_history() -> tuple[ProgressPlan, tuple[ProgressValidationEvent, ...]]:
    proposer = ActorIdentity(actor_id="proposer", kind=ActorKind.HUMAN, created_at=NOW)
    validator = ActorIdentity(actor_id="validator", kind=ActorKind.HUMAN, created_at=NOW)
    plan = ProgressPlan(
        plan_version_id="plan-1",
        run_id="run-1",
        version=1,
        subtasks=(
            ProgressSubtask(
                subtask_id="validated-work",
                plan_version_id="plan-1",
                description="Complete validated work",
                dependency_ids=(),
                completion_criteria=("Independent validator accepts",),
                validator=validator,
                validator_version="validator-v1",
                weight=Decimal("1.00"),
                evidence_requirements=("retained-evidence",),
                order=1,
            ),
        ),
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    event = ProgressValidationEvent(
        event_id="event-1",
        run_id=plan.run_id,
        plan_version_id=plan.plan_version_id,
        subtask_id=plan.subtasks[0].subtask_id,
        requested_status=ProgressStatus.VALIDATED,
        completion_proposer=proposer,
        validator=validator,
        validator_version="validator-v1",
        validator_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        relationship_to_run_creator=ActorRelationship.INDEPENDENT,
        relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
        are_independent=True,
        evidence_ids=("evidence-1",),
        checks_run=("check-1",),
        assumptions=(),
        limitations=("Limited to retained evidence",),
        result=AssessmentOutcome.PASSED,
        occurred_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    return plan, (event,)


def test_voluntary_failed_validation_with_progress_and_budget_is_false_finish() -> None:
    plan, events = _progress_history()
    finding = detect_false_finish(
        voluntary_termination=True,
        claims_completion=True,
        final_validator_result=AssessmentOutcome.FAILED,
        plan=plan,
        events=events,
        unused_budget=True,
    )

    assert finding.result is FalseFinishResult.FALSE_FINISH
    assert finding.final_validator_failed is True
    assert finding.meaningful_validated_progress is True


def test_false_finish_requires_every_conjunct() -> None:
    plan, events = _progress_history()
    baseline = {
        "voluntary_termination": True,
        "claims_completion": True,
        "final_validator_result": AssessmentOutcome.FAILED,
        "plan": plan,
        "events": events,
        "unused_budget": True,
    }
    counterexamples = (
        {"voluntary_termination": False},
        {"claims_completion": False},
        {"final_validator_result": AssessmentOutcome.PASSED},
        {"events": ()},
        {"unused_budget": False},
    )

    for override in counterexamples:
        finding = detect_false_finish(**(baseline | override))
        assert finding.result is FalseFinishResult.NOT_FALSE_FINISH
