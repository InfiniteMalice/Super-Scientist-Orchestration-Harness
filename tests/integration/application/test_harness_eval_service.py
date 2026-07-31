from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Engine, text

from super_scientist.application.harness_eval.service import (
    HarnessEvaluationService,
    _campaign_matches_record,
    campaign_export_bytes,
    compare_budgets,
    decide_campaign,
)
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.harness_eval.models import (
    CampaignIteration,
    CampaignPartitionManifest,
    EvaluationBudget,
    FeedbackMode,
    FixedCheckerConfiguration,
    FixedCheckerKind,
    HarnessCampaign,
    HarnessCampaignReport,
    HarnessConfound,
    HarnessConfoundCode,
    HarnessDecisionStatus,
    HarnessPartition,
    HarnessVariant,
    PartitionMetric,
    VariantEvaluationBudget,
    fixed_checker_configuration_hash,
    partition_manifest_hash,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import sha256_hex
from super_scientist.kernel.transactions.models import (
    Approval,
    CreateHarnessCampaign,
    DecideHarnessCampaign,
    RecordHarnessConfound,
    RecordHarnessIteration,
    RecordHarnessProtectedResult,
    RejectionCode,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    HarnessBudgetRepository,
    HarnessCampaignHeadRepository,
    HarnessCampaignRepository,
    HarnessConfoundRepository,
    HarnessDecisionRepository,
    HarnessMetricRepository,
    HarnessObservationRepository,
    HarnessPartitionManifestRepository,
)
from super_scientist.providers.storage.protected_evaluation import (
    MetricValue,
    ProtectedCheckerResult,
)

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
SECRET = b"integration-held-out-answer-task-15"


class _Clock:
    def now(self) -> datetime:
        return NOW


class _InProcessResultValidator:
    """Exercise Task 15 orchestration without coverage tracing a spawned worker."""

    def validate_result(self, result: ProtectedCheckerResult) -> ProtectedCheckerResult:
        if type(result) is not ProtectedCheckerResult:
            raise TypeError("result must be the strict protected checker DTO")
        return result

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class Runtime:
    engine: Engine
    uow_factory: Callable[[], DatabaseUnitOfWork]
    coordinator: TransactionCoordinator
    service: HarnessEvaluationService
    policy: PolicySnapshot
    proposer: ActorIdentity
    authority: ActorIdentity


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[Runtime]:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'harness.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    policy = _policy()

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as uow:
        uow.repositories().policies.add_and_activate(policy, NOW)
    coordinator = TransactionCoordinator(
        uow_factory,
        policy,
        _Clock(),
        FileArtifactStore(tmp_path / "artifacts"),
    )
    validator = _InProcessResultValidator()
    try:
        yield Runtime(
            engine=engine,
            uow_factory=uow_factory,
            coordinator=coordinator,
            service=HarnessEvaluationService(coordinator, validator),
            policy=policy,
            proposer=_model_actor("candidate-producer"),
            authority=_human_actor("campaign-authority"),
        )
    finally:
        validator.close()
        engine.dispose()


@pytest.mark.integration
def test_campaign_creation_persists_campaign_partitions_and_budgets_in_one_transaction(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    decision = runtime.service.create_campaign(_create_proposal(runtime, campaign))

    assert decision.accepted is True
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert HarnessCampaignRepository(uow.connection).get(campaign.campaign_id) is not None
        assert len(HarnessPartitionManifestRepository(uow.connection).list_all()) == 5
        assert len(HarnessBudgetRepository(uow.connection).list_all()) == 2
        assert len(uow.repositories().transactions.list_all()) == 1
        assert len(uow.repositories().audit.list_all()) == 1


@pytest.mark.integration
def test_campaign_creation_replays_without_duplicate_children(runtime: Runtime) -> None:
    proposal = _create_proposal(runtime, _campaign(runtime))

    first = runtime.service.create_campaign(proposal)
    second = runtime.service.create_campaign(proposal)

    assert first.accepted is True
    assert second == first.model_copy(update={"replayed": True})
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert len(HarnessPartitionManifestRepository(uow.connection).list_all()) == 5
        assert len(HarnessBudgetRepository(uow.connection).list_all()) == 2


@pytest.mark.integration
def test_decision_campaign_binding_covers_all_nested_partition_and_budget_semantics(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        stored = HarnessCampaignRepository(uow.connection).get(campaign.campaign_id)
        assert stored is not None
        partitions = tuple(
            sorted(
                HarnessPartitionManifestRepository(uow.connection).list_all(),
                key=lambda item: item.partition.value,
            )
        )
        budgets = tuple(
            sorted(
                HarnessBudgetRepository(uow.connection).list_all(),
                key=lambda item: item.variant.value,
            )
        )

    for case in (
        "evaluator_identity",
        "evaluator_version",
        "campaign_version",
        "partition_membership",
        "partition_protected_hash",
        "partition_timestamp",
        "model_id",
        "model_version",
        "adapter_id",
        "feedback_mode",
        "tool_ids",
        "attempts",
        "token_limit",
        "reasoning_limit",
        "evaluator_call_limit",
        "wall_clock_seconds",
        "cost_limit",
        "human_intervention_limit",
        "budget_id",
        "budget_order",
    ):
        changed = _mutate_campaign_semantic(campaign, case)
        assert not _campaign_matches_record(changed, stored, partitions, budgets), case


@pytest.mark.integration
@pytest.mark.parametrize(
    "case",
    ("candidate-producer", "coordinator", "protected-partition", "policy"),
)
def test_campaign_creation_rejects_confounded_authority_and_lineage(
    runtime: Runtime,
    case: str,
) -> None:
    campaign = _campaign(runtime)
    proposer = runtime.proposer
    approval = _approval(runtime)
    if case == "candidate-producer":
        proposer = _model_actor("other-producer")
    elif case == "coordinator":
        approval = Approval(approver=_human_actor("other-coordinator"), approved_at=NOW)
    elif case == "protected-partition":
        changed = campaign.partitions[1].model_copy(update={"protected_content_hash": None})
        campaign = campaign.model_copy(
            update={"partitions": (campaign.partitions[0], changed, *campaign.partitions[2:])}
        )
    else:
        wrong_hash = "f" * 64
        campaign = campaign.model_copy(
            update={
                "partitions": tuple(
                    item.model_copy(update={"governing_policy_hash": wrong_hash})
                    for item in campaign.partitions
                ),
                "governing_policy_hash": wrong_hash,
            }
        )

    decision = runtime.service.create_campaign(
        CreateHarnessCampaign(
            proposal_id=f"rejected-create-{case}",
            idempotency_key=f"rejected-create-{case}-key",
            proposer=proposer,
            approval=approval,
            campaign=campaign,
        )
    )

    assert decision.accepted is False


@pytest.mark.integration
def test_iteration_handler_rejects_missing_duplicate_unauthorized_and_mismatched_records(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    base = _iteration()
    cases: tuple[
        tuple[
            CampaignIteration,
            ActorIdentity,
            Approval,
            str,
            RejectionCode,
        ],
        ...,
    ] = (
        (
            base.model_copy(update={"partition_manifest_id": "missing-manifest"}),
            runtime.authority,
            _approval(runtime),
            runtime.policy.policy_hash,
            RejectionCode.MISSING_ENTITY,
        ),
        (
            base,
            _human_actor("other-human"),
            Approval(approver=_human_actor("other-human"), approved_at=NOW),
            runtime.policy.policy_hash,
            RejectionCode.PERMISSION_DENIED,
        ),
        (
            base.model_copy(update={"task_id": "other-task"}),
            runtime.authority,
            _approval(runtime),
            runtime.policy.policy_hash,
            RejectionCode.INVALID_LINEAGE,
        ),
        (
            base,
            runtime.authority,
            _approval(runtime),
            "f" * 64,
            RejectionCode.POLICY_HASH_MISMATCH,
        ),
    )
    for index, (iteration, proposer, approval, policy, expected) in enumerate(cases):
        decision = runtime.service.record_iteration(
            RecordHarnessIteration(
                proposal_id=f"rejected-iteration-{index}",
                idempotency_key=f"rejected-iteration-{index}-key",
                proposer=proposer,
                approval=approval,
                iteration=iteration,
                governing_policy_hash=policy,
            )
        )
        assert decision.accepted is False
        assert decision.reasons[0].code is expected

    assert runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id="accepted-iteration",
            idempotency_key="accepted-iteration-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=base,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    ).accepted
    duplicate = runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id="duplicate-iteration",
            idempotency_key="duplicate-iteration-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=base,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    )
    assert duplicate.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS


@pytest.mark.integration
def test_iteration_and_validated_protected_result_share_public_hash_bindings(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iteration = _iteration()
    iteration_proposal = RecordHarnessIteration(
        proposal_id="record-iteration",
        idempotency_key="record-iteration-key",
        proposer=runtime.authority,
        approval=_approval(runtime),
        iteration=iteration,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    assert runtime.service.record_iteration(iteration_proposal).accepted

    result = _protected_result()
    result_proposal = RecordHarnessProtectedResult(
        proposal_id="record-result",
        idempotency_key="record-result-key",
        proposer=runtime.authority,
        approval=_approval(runtime),
        observation_id=iteration.observation_id,
        partition_manifest_id=iteration.partition_manifest_id,
        variant=iteration.variant,
        evaluator_version_id=iteration.evaluator_version_id,
        checker_configuration=_checker(),
        result=result,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    assert runtime.service.record_protected_result(result_proposal).accepted

    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert (
            HarnessObservationRepository(uow.connection).get(iteration.observation_id) is not None
        )
        assert HarnessMetricRepository(uow.connection).get(result.result_id) is not None


@pytest.mark.integration
def test_evaluator_change_is_retained_as_a_confound_and_public_lineage(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    confound = HarnessConfound(
        confound_id="confound-evaluator-change",
        campaign_id=campaign.campaign_id,
        code=HarnessConfoundCode.EVALUATOR_CHANGED,
        description="the evaluator changed during the campaign",
        affected_variant=HarnessVariant.EVOLVED_HARNESS,
        resolved=False,
        independent_analysis_id=None,
        recorded_at=NOW,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    assert runtime.service.record_confound(
        RecordHarnessConfound(
            proposal_id="evaluator-confound",
            idempotency_key="evaluator-confound-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            confound=confound,
        )
    ).accepted
    iteration = _iteration().model_copy(update={"evaluator_version_id": "evaluator-v2"})

    iteration_decision = runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id="changed-evaluator-iteration",
            idempotency_key="changed-evaluator-iteration-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=iteration,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    )

    assert iteration_decision.accepted is True
    result_decision = runtime.service.record_protected_result(
        RecordHarnessProtectedResult(
            proposal_id="changed-evaluator-result",
            idempotency_key="changed-evaluator-result-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            observation_id=iteration.observation_id,
            partition_manifest_id=iteration.partition_manifest_id,
            variant=iteration.variant,
            evaluator_version_id=iteration.evaluator_version_id,
            checker_configuration=_checker(evaluator_version_id="evaluator-v2"),
            result=_protected_result(),
            governing_policy_hash=runtime.policy.policy_hash,
        )
    )

    assert result_decision.accepted is True
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        stored = HarnessObservationRepository(uow.connection).get(iteration.observation_id)
        assert stored is not None
        assert stored.evaluator_version_id == "evaluator-v2"
        assert HarnessConfoundRepository(uow.connection).get(confound.confound_id) is not None


@pytest.mark.integration
def test_result_binding_mismatch_is_rejected_without_persisting_metric(runtime: Runtime) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iteration = _iteration()
    assert runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id="iteration",
            idempotency_key="iteration-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=iteration,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    ).accepted
    result = _protected_result().model_copy(update={"candidate_output_hash": "f" * 64})
    decision = runtime.service.record_protected_result(
        RecordHarnessProtectedResult(
            proposal_id="mismatched-result",
            idempotency_key="mismatched-result-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            observation_id=iteration.observation_id,
            partition_manifest_id=iteration.partition_manifest_id,
            variant=iteration.variant,
            evaluator_version_id=iteration.evaluator_version_id,
            checker_configuration=_checker(),
            result=result,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert HarnessMetricRepository(uow.connection).get(result.result_id) is None


@pytest.mark.integration
def test_protected_result_rejects_checker_and_evaluator_configuration_mismatches(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iteration = _iteration()
    assert runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id="checker-binding-iteration",
            idempotency_key="checker-binding-iteration-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=iteration,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    ).accepted
    for case, checker in (
        ("checker_id", _checker(checker_id="other-checker")),
        ("checker_version", _checker(checker_version="other-version")),
        ("metric_ids", _checker(metric_ids=("other-metric",))),
        ("evaluator_id", _checker(evaluator_id="other-evaluator")),
        ("evaluator_version", _checker(evaluator_version_id="evaluator-v2")),
    ):
        decision = runtime.service.record_protected_result(
            RecordHarnessProtectedResult(
                proposal_id=f"checker-binding-{case}",
                idempotency_key=f"checker-binding-{case}-key",
                proposer=runtime.authority,
                approval=_approval(runtime),
                observation_id=iteration.observation_id,
                partition_manifest_id=iteration.partition_manifest_id,
                variant=iteration.variant,
                evaluator_version_id=iteration.evaluator_version_id,
                checker_configuration=checker,
                result=_protected_result(),
                governing_policy_hash=runtime.policy.policy_hash,
            )
        )
        assert decision.accepted is False, case
        assert decision.reasons[0].code is RejectionCode.INVALID_LINEAGE


@pytest.mark.integration
def test_protected_result_handler_rejects_missing_unauthorized_stale_and_duplicate_records(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iteration = _iteration()
    assert runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id="result-precondition-iteration",
            idempotency_key="result-precondition-iteration-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=iteration,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    ).accepted
    other = _human_actor("other-result-authority")
    cases: tuple[
        tuple[
            dict[str, str],
            ActorIdentity,
            Approval,
            ProtectedCheckerResult,
            str,
            RejectionCode,
        ],
        ...,
    ] = (
        (
            {"observation_id": "missing-observation"},
            runtime.authority,
            _approval(runtime),
            _protected_result(),
            runtime.policy.policy_hash,
            RejectionCode.MISSING_ENTITY,
        ),
        (
            {},
            other,
            Approval(approver=other, approved_at=NOW),
            _protected_result(),
            runtime.policy.policy_hash,
            RejectionCode.PERMISSION_DENIED,
        ),
        (
            {},
            runtime.authority,
            _approval(runtime),
            _protected_result().model_copy(update={"evaluated_at": NOW - timedelta(seconds=1)}),
            runtime.policy.policy_hash,
            RejectionCode.INVALID_LINEAGE,
        ),
        (
            {},
            runtime.authority,
            _approval(runtime),
            _protected_result(),
            "f" * 64,
            RejectionCode.POLICY_HASH_MISMATCH,
        ),
    )
    for index, (updates, proposer, approval, result, policy, expected) in enumerate(cases):
        proposal = RecordHarnessProtectedResult(
            proposal_id=f"rejected-result-{index}",
            idempotency_key=f"rejected-result-{index}-key",
            proposer=proposer,
            approval=approval,
            observation_id=iteration.observation_id,
            partition_manifest_id=iteration.partition_manifest_id,
            variant=iteration.variant,
            evaluator_version_id=iteration.evaluator_version_id,
            checker_configuration=_checker(),
            result=result,
            governing_policy_hash=policy,
        ).model_copy(update=updates)
        decision = runtime.service.record_protected_result(proposal)
        assert decision.accepted is False
        assert decision.reasons[0].code is expected

    accepted_proposal = RecordHarnessProtectedResult(
        proposal_id="accepted-result",
        idempotency_key="accepted-result-key",
        proposer=runtime.authority,
        approval=_approval(runtime),
        observation_id=iteration.observation_id,
        partition_manifest_id=iteration.partition_manifest_id,
        variant=iteration.variant,
        evaluator_version_id=iteration.evaluator_version_id,
        checker_configuration=_checker(),
        result=_protected_result(),
        governing_policy_hash=runtime.policy.policy_hash,
    )
    assert runtime.service.record_protected_result(accepted_proposal).accepted
    duplicate = runtime.service.record_protected_result(
        accepted_proposal.model_copy(
            update={
                "proposal_id": "duplicate-result",
                "idempotency_key": "duplicate-result-key",
            }
        )
    )
    assert duplicate.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS


@pytest.mark.integration
def test_negative_iterations_confounds_and_nonadmission_decision_are_all_append_only(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime, extra_attempt=True)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    confound = HarnessConfound(
        confound_id="confound-budget",
        campaign_id=campaign.campaign_id,
        code=HarnessConfoundCode.ATTEMPTS_MISMATCH,
        description="candidate received an additional attempt",
        affected_variant=HarnessVariant.EVOLVED_HARNESS,
        resolved=False,
        independent_analysis_id=None,
        recorded_at=NOW,
        governing_policy_hash=runtime.policy.policy_hash,
    )
    assert runtime.service.record_confound(
        RecordHarnessConfound(
            proposal_id="confound-proposal",
            idempotency_key="confound-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            confound=confound,
        )
    ).accepted
    iterations, metrics = _record_complete_evidence(runtime, campaign, transfer=Decimal("0.4"))
    report = _report(
        runtime,
        campaign,
        confounds=(confound,),
        iterations=iterations,
        metrics=metrics,
    )
    recommendation = decide_campaign(report)
    assert recommendation.status is HarnessDecisionStatus.INCONCLUSIVE
    assert runtime.service.decide_campaign(
        DecideHarnessCampaign(
            proposal_id="decision-proposal",
            idempotency_key="decision-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            report=report,
            decision=recommendation,
        )
    ).accepted

    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        assert HarnessConfoundRepository(uow.connection).get(confound.confound_id) is not None
        stored = HarnessDecisionRepository(uow.connection).get(recommendation.decision_id)
        assert stored is not None
        assert stored.admitted is False
        assert HarnessCampaignHeadRepository(uow.connection).get(campaign.campaign_id) == (
            recommendation.decision_id,
            recommendation.status,
        )


@pytest.mark.integration
def test_admission_without_durable_audit_and_measurement_is_rejected(runtime: Runtime) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iterations, metrics = _record_complete_evidence(runtime, campaign, transfer=Decimal("0.9"))
    report = _report(
        runtime,
        campaign,
        transfer=Decimal("0.9"),
        admission_requested=True,
        iterations=iterations,
        metrics=metrics,
    )
    recommendation = decide_campaign(report)
    assert recommendation.admitted is True

    durable = runtime.service.decide_campaign(
        DecideHarnessCampaign(
            proposal_id="unsupported-admission",
            idempotency_key="unsupported-admission-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            report=report,
            decision=recommendation,
        )
    )

    assert durable.accepted is False
    assert durable.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED


@pytest.mark.integration
@pytest.mark.parametrize("negative", (False, True))
def test_resultless_authoritative_iteration_blocks_otherwise_admissible_evidence(
    runtime: Runtime,
    negative: bool,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iterations, metrics = _record_complete_evidence(
        runtime,
        campaign,
        transfer=Decimal("0.9"),
    )
    manifest = campaign.partitions[0]
    resultless = CampaignIteration(
        iteration_index=len(iterations),
        observation_id=f"resultless-{'negative' if negative else 'unresolved'}",
        partition_manifest_id=manifest.partition_manifest_id,
        task_id=manifest.task_ids[0],
        partition=manifest.partition,
        variant=campaign.candidate_variant,
        budget_id="budget-candidate",
        attempt=1,
        candidate_output_hash="f" * 64,
        result_id=None,
        outcome=None,
        negative_result=negative,
        evaluator_version_id=campaign.evaluator_version_id,
        observed_at=NOW,
    )
    assert runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id=f"record-{resultless.observation_id}",
            idempotency_key=f"record-{resultless.observation_id}-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=resultless,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    ).accepted
    report = _report(
        runtime,
        campaign,
        transfer=Decimal("0.9"),
        admission_requested=True,
        iterations=(*iterations, resultless),
        metrics=metrics,
    )
    recommendation = decide_campaign(report)
    assert recommendation.admitted is True

    durable = runtime.service.decide_campaign(
        DecideHarnessCampaign(
            proposal_id=f"resultless-decision-{negative}",
            idempotency_key=f"resultless-decision-{negative}-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            report=report,
            decision=recommendation,
        )
    )

    assert durable.accepted is False
    assert durable.reasons[0].code is RejectionCode.MISSING_EVIDENCE
    with runtime.uow_factory() as uow:
        assert uow.connection is not None
        stored = HarnessObservationRepository(uow.connection).get(resultless.observation_id)
        assert stored is not None
        assert stored.result_id is None
        assert stored.negative_result is negative


def test_failed_resultless_iteration_is_rejected_at_the_domain_boundary() -> None:
    with pytest.raises(
        ValueError,
        match="result_id and outcome must be present together",
    ):
        CampaignIteration(
            iteration_index=0,
            observation_id="failed-resultless",
            partition_manifest_id="partition-discovery",
            task_id="task-discovery",
            partition=HarnessPartition.HARNESS_DISCOVERY_TASKS,
            variant=HarnessVariant.SIMPLE_PARAMETER_SEARCH,
            budget_id="budget-candidate",
            attempt=1,
            candidate_output_hash="f" * 64,
            result_id=None,
            outcome=AssessmentOutcome.FAILED,
            negative_result=True,
            evaluator_version_id="evaluator-v1",
            observed_at=NOW,
        )


@pytest.mark.integration
def test_failed_iteration_without_protected_result_is_never_complete(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iterations, metrics = _record_complete_evidence(
        runtime,
        campaign,
        transfer=Decimal("0.9"),
    )
    manifest = campaign.partitions[0]
    failed = CampaignIteration(
        iteration_index=len(iterations),
        observation_id="failed-without-protected-result",
        partition_manifest_id=manifest.partition_manifest_id,
        task_id=manifest.task_ids[0],
        partition=manifest.partition,
        variant=campaign.candidate_variant,
        budget_id="budget-candidate",
        attempt=1,
        candidate_output_hash="e" * 64,
        result_id="missing-protected-result",
        outcome=AssessmentOutcome.FAILED,
        negative_result=True,
        evaluator_version_id=campaign.evaluator_version_id,
        observed_at=NOW,
    )
    assert runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id="record-failed-without-result",
            idempotency_key="record-failed-without-result-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=failed,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    ).accepted
    report = _report(
        runtime,
        campaign,
        transfer=Decimal("0.9"),
        iterations=(*iterations, failed),
        metrics=metrics,
    )

    durable = runtime.service.decide_campaign(
        DecideHarnessCampaign(
            proposal_id="failed-without-result-decision",
            idempotency_key="failed-without-result-decision-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            report=report,
            decision=decide_campaign(report),
        )
    )

    assert durable.accepted is False
    assert durable.reasons[0].code is RejectionCode.MISSING_EVIDENCE


@pytest.mark.integration
def test_lower_is_better_direction_is_checker_authored_and_immutable(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iterations, metrics = _record_complete_evidence(
        runtime,
        campaign,
        transfer=Decimal("0.2"),
        higher_is_better=False,
    )
    report = _report(
        runtime,
        campaign,
        transfer=Decimal("0.2"),
        iterations=iterations,
        metrics=metrics,
    )
    recommendation = decide_campaign(report)
    assert recommendation.status is HarnessDecisionStatus.TRANSFER_VALIDATED
    accepted = runtime.service.decide_campaign(
        DecideHarnessCampaign(
            proposal_id="lower-is-better-decision",
            idempotency_key="lower-is-better-decision-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            report=report,
            decision=recommendation,
        )
    )
    assert accepted.accepted is True

    mutated = report.model_copy(
        update={
            "metrics": tuple(
                item.model_copy(update={"higher_is_better": True}) for item in report.metrics
            )
        }
    )
    rejected = runtime.service.decide_campaign(
        DecideHarnessCampaign(
            proposal_id="mutated-direction-decision",
            idempotency_key="mutated-direction-decision-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            report=mutated,
            decision=decide_campaign(mutated),
        )
    )
    assert rejected.accepted is False
    assert rejected.reasons[0].code is RejectionCode.MISSING_EVIDENCE


@pytest.mark.integration
def test_decision_reconciles_every_metric_to_complete_protected_observations(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iterations, metrics = _record_complete_evidence(
        runtime,
        campaign,
        transfer=Decimal("0.9"),
        catastrophic_partition=HarnessPartition.HARNESS_SAFETY_TASKS,
    )
    report = _report(
        runtime,
        campaign,
        transfer=Decimal("0.9"),
        iterations=iterations,
        metrics=metrics,
    )
    base = DecideHarnessCampaign(
        proposal_id="lineage-base",
        idempotency_key="lineage-base-key",
        proposer=runtime.authority,
        approval=_approval(runtime),
        report=report,
        decision=decide_campaign(report),
    )

    negative_index = next(index for index, item in enumerate(iterations) if item.negative_result)
    safety_index = next(
        index
        for index, item in enumerate(metrics)
        if item.partition is HarnessPartition.HARNESS_SAFETY_TASKS
    )
    mutations: dict[str, HarnessCampaignReport] = {
        "wrong_task": report.model_copy(
            update={
                "iterations": (
                    iterations[0].model_copy(update={"task_id": "wrong-task"}),
                    *iterations[1:],
                )
            }
        ),
        "wrong_partition": report.model_copy(
            update={
                "iterations": (
                    iterations[0].model_copy(
                        update={"partition": HarnessPartition.HARNESS_VALIDATION_TASKS}
                    ),
                    *iterations[1:],
                )
            }
        ),
        "wrong_variant": report.model_copy(
            update={
                "iterations": (
                    iterations[0].model_copy(
                        update={
                            "variant": campaign.candidate_variant,
                            "budget_id": "budget-candidate",
                        }
                    ),
                    *iterations[1:],
                )
            }
        ),
        "wrong_evaluator": report.model_copy(
            update={
                "iterations": (
                    iterations[0].model_copy(update={"evaluator_version_id": "evaluator-v2"}),
                    *iterations[1:],
                )
            }
        ),
        "wrong_metric": report.model_copy(
            update={
                "metrics": (
                    metrics[0].model_copy(update={"metric_id": "other-metric"}),
                    *metrics[1:],
                )
            }
        ),
        "wrong_value": report.model_copy(
            update={
                "metrics": (
                    metrics[0].model_copy(update={"candidate_value": Decimal("0.99")}),
                    *metrics[1:],
                )
            }
        ),
        "partial_results": report.model_copy(
            update={
                "metrics": (
                    metrics[0].model_copy(update={"result_ids": metrics[0].result_ids[:1]}),
                    *metrics[1:],
                )
            }
        ),
        "extra_result": report.model_copy(
            update={
                "metrics": (
                    metrics[0].model_copy(
                        update={"result_ids": (*metrics[0].result_ids, "invented-result")}
                    ),
                    *metrics[1:],
                )
            }
        ),
        "reused_cross_partition_result": report.model_copy(
            update={
                "metrics": (
                    *metrics[:safety_index],
                    metrics[safety_index].model_copy(update={"result_ids": metrics[0].result_ids}),
                    *metrics[safety_index + 1 :],
                )
            }
        ),
        "omitted_negative": report.model_copy(
            update={
                "iterations": (
                    *iterations[:negative_index],
                    iterations[negative_index].model_copy(
                        update={
                            "negative_result": False,
                            "outcome": AssessmentOutcome.PASSED,
                        }
                    ),
                    *iterations[negative_index + 1 :],
                ),
                "negative_observation_ids": (),
            }
        ),
        "favorable_aggregate_hides_catastrophe": report.model_copy(
            update={
                "metrics": (
                    *metrics[:safety_index],
                    metrics[safety_index].model_copy(
                        update={
                            "candidate_value": Decimal("0.9"),
                            "catastrophic_regression": False,
                        }
                    ),
                    *metrics[safety_index + 1 :],
                )
            }
        ),
    }
    for case, mutated_report in mutations.items():
        proposal = base.model_copy(
            update={
                "proposal_id": f"lineage-{case}",
                "idempotency_key": f"lineage-{case}-key",
                "report": mutated_report,
                "decision": decide_campaign(mutated_report),
            }
        )
        decision = runtime.service.decide_campaign(proposal)
        assert decision.accepted is False, case


@pytest.mark.integration
def test_protected_literal_is_absent_from_main_database_audit_and_campaign_export(
    runtime: Runtime,
) -> None:
    campaign = _campaign(runtime)
    assert runtime.service.create_campaign(_create_proposal(runtime, campaign)).accepted
    iteration = _iteration(candidate_hash=sha256_hex(SECRET))
    assert runtime.service.record_iteration(
        RecordHarnessIteration(
            proposal_id="secret-observation",
            idempotency_key="secret-observation-key",
            proposer=runtime.authority,
            approval=_approval(runtime),
            iteration=iteration,
            governing_policy_hash=runtime.policy.policy_hash,
        )
    ).accepted
    report = _report(runtime, campaign)

    with runtime.engine.connect() as connection:
        dump = b"\n".join(
            str(row).encode("utf-8")
            for table_name in (
                "harness_campaigns",
                "harness_partition_manifests",
                "harness_budgets",
                "harness_observations",
                "harness_metrics",
                "harness_confounds",
                "harness_decisions",
                "transactions",
                "audit_events",
            )
            for row in connection.execute(text(f"SELECT * FROM {table_name}"))
        )
    assert SECRET not in dump
    assert SECRET not in campaign_export_bytes(report)
    assert b"protected://" not in campaign_export_bytes(report)


def _policy() -> PolicySnapshot:
    policy = GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset({"harness_admission"}),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.ORCHESTRATION,
                persistence=PersistenceScope.HARNESS_CODE,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.INDEPENDENT_TEST_SUITE}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=True,
                rollback_required=True,
            ),
        ),
    )
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _create_proposal(runtime: Runtime, campaign: HarnessCampaign) -> CreateHarnessCampaign:
    return CreateHarnessCampaign(
        proposal_id="create-campaign",
        idempotency_key="create-campaign-key",
        proposer=runtime.proposer,
        approval=_approval(runtime),
        campaign=campaign,
    )


def _approval(runtime: Runtime) -> Approval:
    return Approval(approver=runtime.authority, approved_at=NOW)


def _budget(*, attempts: int = 1) -> EvaluationBudget:
    return EvaluationBudget(
        model_id="model-1",
        model_version="model-v1",
        adapter_id=None,
        feedback_mode=FeedbackMode.NONE,
        tool_ids=(),
        attempts=attempts,
        token_limit=100,
        reasoning_limit=50,
        evaluator_call_limit=1,
        wall_clock_seconds=Decimal("10"),
        cost_limit=Decimal("1"),
        human_intervention_limit=0,
    )


def _campaign(runtime: Runtime, *, extra_attempt: bool = False) -> HarnessCampaign:
    variants = (
        HarnessVariant.UNCHANGED_HARNESS_SINGLE_ATTEMPT,
        HarnessVariant.EVOLVED_HARNESS,
    )
    partitions: list[CampaignPartitionManifest] = []
    for partition in HarnessPartition:
        task_ids = (f"{partition.value.lower()}-task",)
        partitions.append(
            CampaignPartitionManifest(
                partition_manifest_id=f"manifest-{partition.value.lower()}",
                campaign_id="campaign-v1",
                campaign_version=1,
                partition=partition,
                task_ids=task_ids,
                manifest_hash=partition_manifest_hash(
                    campaign_id="campaign-v1",
                    campaign_version=1,
                    partition=partition,
                    task_ids=task_ids,
                ),
                protected_content_hash=(
                    None if partition is HarnessPartition.HARNESS_DISCOVERY_TASKS else "a" * 64
                ),
                created_at=NOW,
                governing_policy_hash=runtime.policy.policy_hash,
            )
        )
    return HarnessCampaign(
        campaign_id="campaign-v1",
        version=1,
        variants=variants,
        baseline_variant=variants[0],
        candidate_variant=variants[1],
        baseline_harness_version_id="harness-v1",
        candidate_harness_version_id="harness-v2",
        rollback_harness_version_id="harness-v1",
        model_id="model-1",
        model_version="model-v1",
        adapter_id=None,
        evaluator=_model_actor("evaluator"),
        evaluator_version_id="evaluator-v1",
        candidate_producer=runtime.proposer,
        coordinator=runtime.authority,
        partitions=tuple(partitions),
        budgets=(
            VariantEvaluationBudget(
                budget_id="budget-baseline",
                variant=variants[0],
                budget=_budget(),
            ),
            VariantEvaluationBudget(
                budget_id="budget-candidate",
                variant=variants[1],
                budget=_budget(attempts=2 if extra_attempt else 1),
            ),
        ),
        created_at=NOW,
        governing_policy_hash=runtime.policy.policy_hash,
    )


def _mutate_campaign_semantic(campaign: HarnessCampaign, case: str) -> HarnessCampaign:
    if case == "evaluator_identity":
        return campaign.model_copy(update={"evaluator": _model_actor("other-evaluator")})
    if case == "evaluator_version":
        return campaign.model_copy(update={"evaluator_version_id": "evaluator-v2"})
    if case == "campaign_version":
        partitions = tuple(
            item.model_copy(
                update={
                    "campaign_version": 2,
                    "manifest_hash": partition_manifest_hash(
                        campaign_id=item.campaign_id,
                        campaign_version=2,
                        partition=item.partition,
                        task_ids=item.task_ids,
                    ),
                }
            )
            for item in campaign.partitions
        )
        return HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"version": 2, "partitions": partitions}
        )
    if case.startswith("partition_"):
        first = campaign.partitions[0]
        if case == "partition_membership":
            task_ids = ("replacement-discovery-task",)
            changed = first.model_copy(
                update={
                    "task_ids": task_ids,
                    "manifest_hash": partition_manifest_hash(
                        campaign_id=first.campaign_id,
                        campaign_version=first.campaign_version,
                        partition=first.partition,
                        task_ids=task_ids,
                    ),
                }
            )
        elif case == "partition_protected_hash":
            changed = first.model_copy(update={"protected_content_hash": "b" * 64})
        else:
            changed = first.model_copy(update={"created_at": NOW + timedelta(seconds=1)})
        return HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"partitions": (changed, *campaign.partitions[1:])}
        )
    if case == "budget_order":
        return HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"budgets": tuple(reversed(campaign.budgets))}
        )
    if case == "budget_id":
        changed_item = campaign.budgets[1].model_copy(update={"budget_id": "other-budget"})
        return HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"budgets": (campaign.budgets[0], changed_item)}
        )
    replacements: dict[str, object] = {
        "model_id": "model-2",
        "model_version": "model-v2",
        "adapter_id": "adapter-2",
        "feedback_mode": FeedbackMode.PER_ATTEMPT,
        "tool_ids": ("tool-1",),
        "attempts": 2,
        "token_limit": 101,
        "reasoning_limit": 51,
        "evaluator_call_limit": 2,
        "wall_clock_seconds": Decimal("11"),
        "cost_limit": Decimal("2"),
        "human_intervention_limit": 1,
    }
    replacement = replacements[case]
    if case in {"model_id", "model_version", "adapter_id"}:
        changed_budgets = tuple(
            item.model_copy(update={"budget": item.budget.model_copy(update={case: replacement})})
            for item in campaign.budgets
        )
        return HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {case: replacement, "budgets": changed_budgets}
        )
    changed_item = campaign.budgets[1].model_copy(
        update={"budget": campaign.budgets[1].budget.model_copy(update={case: replacement})}
    )
    return HarnessCampaign.model_validate(
        campaign.model_dump(mode="python") | {"budgets": (campaign.budgets[0], changed_item)}
    )


def _iteration(*, candidate_hash: str = "b" * 64) -> CampaignIteration:
    return CampaignIteration(
        iteration_index=0,
        observation_id="observation-discovery",
        partition_manifest_id="manifest-harness_discovery_tasks",
        task_id="harness_discovery_tasks-task",
        partition=HarnessPartition.HARNESS_DISCOVERY_TASKS,
        variant=HarnessVariant.EVOLVED_HARNESS,
        budget_id="budget-candidate",
        attempt=1,
        candidate_output_hash=candidate_hash,
        result_id="result-1",
        outcome=AssessmentOutcome.PASSED,
        negative_result=False,
        evaluator_version_id="evaluator-v1",
        observed_at=NOW,
    )


def _protected_result() -> ProtectedCheckerResult:
    return ProtectedCheckerResult(
        result_id="result-1",
        campaign_id="campaign-v1",
        task_id="harness_discovery_tasks-task",
        expected_output_hash="a" * 64,
        candidate_output_hash="b" * 64,
        checker_id="checker-1",
        checker_version="checker-v1",
        outcome=AssessmentOutcome.PASSED,
        metric_values=(MetricValue(metric_id="correctness", value=Decimal("1")),),
        evaluated_at=NOW,
    )


def _checker(**updates: object) -> FixedCheckerConfiguration:
    payload: dict[str, object] = {
        "checker_id": "checker-1",
        "checker_version": "checker-v1",
        "checker_kind": FixedCheckerKind.EXACT_BYTES,
        "metric_ids": ("correctness",),
        "metric_higher_is_better": (True,),
        "evaluator_id": "evaluator",
        "evaluator_version_id": "evaluator-v1",
    }
    payload.update(updates)
    payload["configuration_hash"] = fixed_checker_configuration_hash(
        checker_id=str(payload["checker_id"]),
        checker_version=str(payload["checker_version"]),
        checker_kind=cast(FixedCheckerKind, payload["checker_kind"]),
        metric_ids=cast(tuple[str, ...], payload["metric_ids"]),
        evaluator_id=str(payload["evaluator_id"]),
        evaluator_version_id=str(payload["evaluator_version_id"]),
        metric_higher_is_better=cast(
            tuple[bool, ...],
            payload["metric_higher_is_better"],
        ),
    )
    return FixedCheckerConfiguration.model_validate(payload)


def _record_complete_evidence(
    runtime: Runtime,
    campaign: HarnessCampaign,
    *,
    transfer: Decimal,
    catastrophic_partition: HarnessPartition | None = None,
    higher_is_better: bool = True,
) -> tuple[tuple[CampaignIteration, ...], tuple[PartitionMetric, ...]]:
    candidate_values = {
        HarnessPartition.HARNESS_DISCOVERY_TASKS: (
            Decimal("0.8") if higher_is_better else Decimal("0.2")
        ),
        HarnessPartition.HARNESS_VALIDATION_TASKS: (
            Decimal("0.8") if higher_is_better else Decimal("0.2")
        ),
        HarnessPartition.HARNESS_TRANSFER_TASKS: transfer,
        HarnessPartition.HARNESS_REGRESSION_TASKS: Decimal("0.5"),
        HarnessPartition.HARNESS_SAFETY_TASKS: Decimal("0.5"),
    }
    iterations: list[CampaignIteration] = []
    result_ids: dict[HarnessPartition, list[str]] = {
        partition: [] for partition in HarnessPartition
    }
    index = 0
    for partition in HarnessPartition:
        manifest = next(item for item in campaign.partitions if item.partition is partition)
        for variant in (campaign.baseline_variant, campaign.candidate_variant):
            budget = next(item for item in campaign.budgets if item.variant is variant)
            result_id = f"evidence-{partition.value.lower()}-{variant.value.lower()}"
            output_hash = sha256_hex(result_id.encode())
            value = (
                Decimal("0.5")
                if variant is campaign.baseline_variant
                else candidate_values[partition]
            )
            catastrophic = (
                variant is campaign.candidate_variant and partition is catastrophic_partition
            )
            iteration = CampaignIteration(
                iteration_index=index,
                observation_id=f"observation-{result_id}",
                partition_manifest_id=manifest.partition_manifest_id,
                task_id=manifest.task_ids[0],
                partition=partition,
                variant=variant,
                budget_id=budget.budget_id,
                attempt=1,
                candidate_output_hash=output_hash,
                result_id=result_id,
                outcome=(AssessmentOutcome.FAILED if catastrophic else AssessmentOutcome.PASSED),
                negative_result=catastrophic,
                evaluator_version_id=campaign.evaluator_version_id,
                observed_at=NOW,
            )
            assert runtime.service.record_iteration(
                RecordHarnessIteration(
                    proposal_id=f"record-{result_id}",
                    idempotency_key=f"record-{result_id}-key",
                    proposer=runtime.authority,
                    approval=_approval(runtime),
                    iteration=iteration,
                    governing_policy_hash=runtime.policy.policy_hash,
                )
            ).accepted
            result = ProtectedCheckerResult(
                result_id=result_id,
                campaign_id=campaign.campaign_id,
                task_id=manifest.task_ids[0],
                expected_output_hash="a" * 64,
                candidate_output_hash=output_hash,
                checker_id="checker-1",
                checker_version="checker-v1",
                outcome=cast(AssessmentOutcome, iteration.outcome),
                metric_values=(MetricValue(metric_id="correctness", value=value),),
                evaluated_at=NOW,
            )
            assert runtime.service.record_protected_result(
                RecordHarnessProtectedResult(
                    proposal_id=f"protect-{result_id}",
                    idempotency_key=f"protect-{result_id}-key",
                    proposer=runtime.authority,
                    approval=_approval(runtime),
                    observation_id=iteration.observation_id,
                    partition_manifest_id=iteration.partition_manifest_id,
                    variant=variant,
                    evaluator_version_id=campaign.evaluator_version_id,
                    checker_configuration=(
                        _checker()
                        if higher_is_better
                        else _checker(metric_higher_is_better=(False,))
                    ),
                    result=result,
                    governing_policy_hash=runtime.policy.policy_hash,
                )
            ).accepted
            iterations.append(iteration)
            result_ids[partition].append(result_id)
            index += 1
    metrics = tuple(
        PartitionMetric(
            partition=partition,
            metric_id="correctness",
            baseline_value=Decimal("0.5"),
            candidate_value=candidate_values[partition],
            higher_is_better=higher_is_better,
            catastrophic_regression=partition is catastrophic_partition,
            result_ids=tuple(result_ids[partition]),
            evaluator_version_id=campaign.evaluator_version_id,
        )
        for partition in HarnessPartition
    )
    return tuple(iterations), metrics


def _report(
    runtime: Runtime,
    campaign: HarnessCampaign,
    *,
    transfer: Decimal = Decimal("0.4"),
    admission_requested: bool = False,
    confounds: tuple[HarnessConfound, ...] = (),
    iterations: tuple[CampaignIteration, ...] = (),
    metrics: tuple[PartitionMetric, ...] | None = None,
) -> HarnessCampaignReport:
    partitions = tuple(HarnessPartition)
    selected_metrics = metrics or tuple(
        PartitionMetric(
            partition=partition,
            metric_id="correctness",
            baseline_value=Decimal("0.5"),
            candidate_value=(
                transfer
                if partition is HarnessPartition.HARNESS_TRANSFER_TASKS
                else (
                    Decimal("0.5")
                    if partition
                    in (
                        HarnessPartition.HARNESS_REGRESSION_TASKS,
                        HarnessPartition.HARNESS_SAFETY_TASKS,
                    )
                    else Decimal("0.8")
                )
            ),
            higher_is_better=True,
            catastrophic_regression=False,
            result_ids=(f"metric-{partition.value.lower()}",),
            evaluator_version_id="evaluator-v1",
        )
        for partition in partitions
    )
    baseline = campaign.budgets[0].budget
    return HarnessCampaignReport(
        campaign=campaign,
        expected_iteration_count=len(iterations),
        iterations=iterations,
        negative_observation_ids=tuple(
            item.observation_id for item in iterations if item.negative_result
        ),
        budget_comparisons=tuple(
            compare_budgets(baseline, item.budget) for item in campaign.budgets[1:]
        ),
        metrics=selected_metrics,
        confounds=confounds,
        evaluator_audit_id="audit-1",
        evaluator_audit_passed=True,
        measurement_id="measurement-1",
        measurement_accepted=True,
        rollback=None,
        admission_requested=admission_requested,
        decision_authority=runtime.authority,
        reported_at=NOW,
        governing_policy_hash=runtime.policy.policy_hash,
    )


def _human_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity(actor_id=identifier, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity.model(identifier, f"provider-{identifier}", identifier, None, NOW)
