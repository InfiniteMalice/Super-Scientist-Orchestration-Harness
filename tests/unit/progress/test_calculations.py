from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import ActorRelationship, AssessmentOutcome
from super_scientist.domain.progress.calculations import calculate_progress
from super_scientist.domain.progress.models import (
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressValidationEvent,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
POLICY_HASH = "a" * 64


def _actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=NOW)


def _plan() -> ProgressPlan:
    validator = _actor("validator")
    return ProgressPlan(
        plan_version_id="plan-1",
        run_id="run-1",
        version=1,
        subtasks=(
            ProgressSubtask(
                subtask_id="collect",
                plan_version_id="plan-1",
                description="Collect primary evidence",
                dependency_ids=(),
                completion_criteria=("Sources retained",),
                validator=validator,
                validator_version="validator-v1",
                weight=Decimal("0.40"),
                evidence_requirements=("primary-source",),
                order=1,
            ),
            ProgressSubtask(
                subtask_id="analyze",
                plan_version_id="plan-1",
                description="Analyze retained evidence",
                dependency_ids=("collect",),
                completion_criteria=("Analysis independently checked",),
                validator=validator,
                validator_version="validator-v1",
                weight=Decimal("0.60"),
                evidence_requirements=("analysis-artifact",),
                order=2,
            ),
        ),
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


def _event(
    event_id: str,
    subtask_id: str,
    status: ProgressStatus,
    occurred_at: datetime,
) -> ProgressValidationEvent:
    proposer = _actor("proposer")
    return ProgressValidationEvent(
        event_id=event_id,
        run_id="run-1",
        plan_version_id="plan-1",
        subtask_id=subtask_id,
        requested_status=status,
        completion_proposer=proposer,
        validator=_actor("validator"),
        validator_version="validator-v1",
        validator_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        relationship_to_run_creator=ActorRelationship.INDEPENDENT,
        relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
        are_independent=True,
        evidence_ids=(f"evidence-{event_id}",),
        checks_run=("check-1",),
        assumptions=(),
        limitations=("Limited to retained artifacts",),
        result=(
            AssessmentOutcome.PASSED
            if status is ProgressStatus.VALIDATED
            else AssessmentOutcome.INCONCLUSIVE
        ),
        occurred_at=occurred_at,
        governing_policy_hash=POLICY_HASH,
    )


def test_only_independently_validated_subtasks_count() -> None:
    plan = _plan()
    events = (
        _event("event-1", "collect", ProgressStatus.PROVISIONALLY_COMPLETE, NOW),
        _event(
            "event-2",
            "analyze",
            ProgressStatus.VALIDATED,
            NOW + timedelta(seconds=1),
        ),
    )

    summary = calculate_progress(plan, events)

    assert summary.provisional_weight == Decimal("0.40")
    assert summary.official_weight == Decimal("0.00")


def test_dependency_invalidation_removes_dependent_weight() -> None:
    plan = _plan()
    events = (
        _event("event-1", "collect", ProgressStatus.VALIDATED, NOW),
        _event(
            "event-2",
            "analyze",
            ProgressStatus.VALIDATED,
            NOW + timedelta(seconds=1),
        ),
        _event(
            "event-3",
            "collect",
            ProgressStatus.INVALIDATED,
            NOW + timedelta(seconds=2),
        ),
    )

    summary = calculate_progress(plan, events)

    assert summary.validated_subtask_ids == ()
    assert summary.official_weight == Decimal("0.00")


def test_latest_event_state_is_deterministic_regardless_of_input_order() -> None:
    plan = _plan()
    validated = _event("event-1", "collect", ProgressStatus.VALIDATED, NOW)
    invalidated = _event(
        "event-2",
        "collect",
        ProgressStatus.INVALIDATED,
        NOW + timedelta(seconds=1),
    )

    first = calculate_progress(plan, (invalidated, validated))
    second = calculate_progress(plan, (validated, invalidated))

    assert first == second
    assert first.validated_subtask_ids == ()


def test_model_configuration_alias_cannot_claim_independent_validation() -> None:
    shared = {
        "kind": ActorKind.MODEL,
        "provider_id": "provider",
        "model_id": "model",
        "adapter_id": "adapter",
        "configuration_hash": "b" * 64,
        "created_at": NOW,
    }
    proposer = ActorIdentity(actor_id="proposer-alias", **shared)
    validator = ActorIdentity(actor_id="validator-alias", **shared)

    with pytest.raises(ValidationError, match="independent"):
        ProgressValidationEvent(
            event_id="event-alias",
            run_id="run-1",
            plan_version_id="plan-1",
            subtask_id="collect",
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
            limitations=("Limited to retained artifacts",),
            result=AssessmentOutcome.PASSED,
            occurred_at=NOW,
            governing_policy_hash=POLICY_HASH,
        )
