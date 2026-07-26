from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from super_scientist.application.harness_eval.service import (
    campaign_export_bytes,
    compare_budgets,
    decide_campaign,
)
from super_scientist.domain.harness_eval.models import (
    BudgetComparison,
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
    HarnessDecision,
    HarnessDecisionStatus,
    HarnessPartition,
    HarnessRollback,
    HarnessVariant,
    MetricValue,
    PartitionMetric,
    ProtectedCheckerResult,
    PublicTaskInput,
    VariantEvaluationBudget,
    partition_manifest_hash,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.models import AssessmentOutcome

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def test_exact_matched_budgets_are_comparable() -> None:
    comparison = compare_budgets(_budget(), _budget())

    assert comparison == BudgetComparison(comparable=True, mismatches=())


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("model_id", "other-model"),
        ("model_version", "other-model-version"),
        ("adapter_id", "other-adapter"),
        ("feedback_mode", FeedbackMode.PER_ATTEMPT),
        ("tool_ids", ("tool-1", "tool-2")),
        ("attempts", 2),
        ("token_limit", 101),
        ("reasoning_limit", 51),
        ("evaluator_call_limit", 2),
        ("wall_clock_seconds", Decimal("10.1")),
        ("cost_limit", Decimal("1.01")),
        ("human_intervention_limit", 1),
    ),
)
def test_every_budget_dimension_is_exactly_comparable(
    field_name: str,
    replacement: object,
) -> None:
    changed = _budget().model_copy(update={field_name: replacement})

    comparison = compare_budgets(_budget(), changed)

    assert comparison.comparable is False
    assert comparison.mismatches == (field_name,)


def test_budget_rejects_duplicate_tools_and_non_finite_limits() -> None:
    payload = _budget().model_dump(mode="python")
    payload["tool_ids"] = ("tool-1", "tool-1")
    with pytest.raises(ValidationError, match="tool_ids"):
        EvaluationBudget.model_validate(payload)

    payload = _budget().model_dump(mode="python")
    payload["cost_limit"] = Decimal("NaN")
    with pytest.raises(ValidationError, match="finite"):
        EvaluationBudget.model_validate(payload)


def test_partition_manifest_is_content_addressed_and_frozen() -> None:
    manifest = _manifest(HarnessPartition.HARNESS_DISCOVERY_TASKS, "discovery-task")

    assert manifest.manifest_hash == partition_manifest_hash(
        campaign_id=manifest.campaign_id,
        campaign_version=manifest.campaign_version,
        partition=manifest.partition,
        task_ids=manifest.task_ids,
    )
    with pytest.raises(ValidationError):
        CampaignPartitionManifest.model_validate(
            manifest.model_dump(mode="python") | {"manifest_hash": "b" * 64}
        )
    with pytest.raises(ValidationError):
        CampaignPartitionManifest.model_validate(
            manifest.model_dump(mode="python") | {"answer_reference": "forbidden"}
        )


def test_partition_membership_is_exclusive_within_campaign_version() -> None:
    campaign = _campaign()
    overlapping = campaign.partitions[1].model_copy(
        update={"task_ids": campaign.partitions[0].task_ids}
    )
    overlapping = overlapping.model_copy(
        update={
            "manifest_hash": partition_manifest_hash(
                campaign_id=overlapping.campaign_id,
                campaign_version=overlapping.campaign_version,
                partition=overlapping.partition,
                task_ids=overlapping.task_ids,
            )
        }
    )

    with pytest.raises(ValidationError, match="exactly one partition"):
        HarnessCampaign.model_validate(
            campaign.model_dump(mode="python")
            | {"partitions": (campaign.partitions[0], overlapping, *campaign.partitions[2:])}
        )


def test_campaign_requires_one_manifest_per_partition_and_one_budget_per_variant() -> None:
    campaign = _campaign()
    with pytest.raises(ValidationError, match="partition manifests"):
        HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"partitions": campaign.partitions[:-1]}
        )
    with pytest.raises(ValidationError, match="variant budgets"):
        HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"budgets": campaign.budgets[:-1]}
        )


def test_campaign_version_bindings_are_immutable_and_exact() -> None:
    campaign = _campaign()
    original = campaign.partitions[0]
    wrong = original.model_copy(
        update={
            "campaign_version": 2,
            "manifest_hash": partition_manifest_hash(
                campaign_id=original.campaign_id,
                campaign_version=2,
                partition=original.partition,
                task_ids=original.task_ids,
            ),
        }
    )

    with pytest.raises(ValidationError, match="campaign version"):
        HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"partitions": (wrong, *campaign.partitions[1:])}
        )
    with pytest.raises(ValidationError, match="rollback"):
        HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"rollback_harness_version_id": "wrong"}
        )


def test_report_requires_a_complete_ordered_iteration_history_and_all_negatives() -> None:
    report = _report()
    assert report.expected_iteration_count == len(report.iterations)
    assert report.negative_observation_ids == ("observation-transfer",)

    with pytest.raises(ValidationError, match="complete iteration history"):
        HarnessCampaignReport.model_validate(
            report.model_dump(mode="python") | {"iterations": report.iterations[1:]}
        )
    with pytest.raises(ValidationError, match="negative"):
        HarnessCampaignReport.model_validate(
            report.model_dump(mode="python") | {"negative_observation_ids": ()}
        )


def test_evaluator_change_must_be_retained_as_a_confound() -> None:
    report = _report()
    changed = report.iterations[-1].model_copy(update={"evaluator_version_id": "evaluator-v2"})

    with pytest.raises(ValidationError, match="EVALUATOR_CHANGED"):
        HarnessCampaignReport.model_validate(
            report.model_dump(mode="python") | {"iterations": (*report.iterations[:-1], changed)}
        )


def test_budget_mismatch_is_never_silently_collapsed() -> None:
    campaign = _campaign(extra_attempt=True)
    report = _report(campaign=campaign)

    assert report.budget_comparisons[0].comparable is False
    assert report.budget_comparisons[0].mismatches == ("attempts",)
    decision = decide_campaign(report)
    assert decision.status is HarnessDecisionStatus.INCONCLUSIVE
    assert decision.admitted is False


def test_discovery_gain_without_transfer_is_benchmark_specific() -> None:
    report = _report(transfer_candidate=Decimal("0.4"))

    decision = decide_campaign(report)

    assert decision.status is HarnessDecisionStatus.BENCHMARK_SPECIFIC
    assert decision.admitted is False


def test_catastrophic_regression_precedes_every_positive_metric() -> None:
    report = _report(catastrophic=True)

    decision = decide_campaign(report)

    assert decision.status is HarnessDecisionStatus.REGRESSION_DETECTED
    assert decision.admitted is False


def test_complete_transfer_requires_audit_measurement_and_human_authority_to_admit() -> None:
    report = _report(transfer_candidate=Decimal("0.9"), admission_requested=True)

    accepted = decide_campaign(report)
    assert accepted.status is HarnessDecisionStatus.ADMITTED
    assert accepted.admitted is True

    for update in (
        {"evaluator_audit_passed": False},
        {"measurement_accepted": False},
        {"decision_authority": _model_actor("model-authority")},
    ):
        rejected = decide_campaign(report.model_copy(update=update))
        assert rejected.admitted is False
        assert rejected.status is HarnessDecisionStatus.INCONCLUSIVE


def test_explicit_rollback_is_never_reinterpreted_as_a_gain() -> None:
    report = _report(transfer_candidate=Decimal("0.9")).model_copy(
        update={
            "rollback": HarnessRollback(
                rollback_event_id="rollback-1",
                target_harness_version_id="harness-v1",
                reason="canary regression",
                rolled_back_at=NOW,
            )
        }
    )

    decision = decide_campaign(report)

    assert decision.status is HarnessDecisionStatus.ROLLED_BACK
    assert decision.admitted is False
    assert decision.rollback_target_id == "harness-v1"


@pytest.mark.parametrize(
    "payload",
    (
        {"comparable": True, "mismatches": ("attempts",)},
        {"comparable": False, "mismatches": ("attempts", "attempts")},
        {"comparable": False, "mismatches": ("unknown_dimension",)},
    ),
)
def test_budget_comparison_rejects_inconsistent_or_ambiguous_dimensions(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        BudgetComparison.model_validate(payload)


def test_strict_public_and_result_dtos_reject_ambiguous_hashes_and_metrics() -> None:
    manifest = _manifest(HarnessPartition.HARNESS_DISCOVERY_TASKS, "task-1")
    duplicate_tasks = manifest.model_dump(mode="python") | {
        "task_ids": ("task-1", "task-1"),
        "manifest_hash": partition_manifest_hash(
            campaign_id=manifest.campaign_id,
            campaign_version=manifest.campaign_version,
            partition=manifest.partition,
            task_ids=("task-1", "task-1"),
        ),
    }
    with pytest.raises(ValidationError, match="unique"):
        CampaignPartitionManifest.model_validate(duplicate_tasks)

    with pytest.raises(ValidationError, match="payload hash"):
        PublicTaskInput(
            campaign_id="campaign-v1",
            campaign_version=1,
            task_id="task-1",
            partition=HarnessPartition.HARNESS_DISCOVERY_TASKS,
            payload=b"public",
            payload_hash="f" * 64,
        )

    with pytest.raises(ValidationError, match="unique"):
        FixedCheckerConfiguration(
            checker_id="checker-1",
            checker_version="checker-v1",
            checker_kind=FixedCheckerKind.EXACT_BYTES,
            configuration_hash=HASH,
            metric_ids=("correctness", "correctness"),
            evaluator_id="evaluator",
            evaluator_version_id="evaluator-v1",
        )

    with pytest.raises(ValidationError, match="finite"):
        MetricValue(metric_id="correctness", value=Decimal("NaN"))

    duplicated_metric = MetricValue(metric_id="correctness", value=Decimal("1"))
    with pytest.raises(ValidationError, match="unique"):
        ProtectedCheckerResult(
            result_id="result-1",
            campaign_id="campaign-v1",
            task_id="task-1",
            expected_output_hash=HASH,
            candidate_output_hash="b" * 64,
            checker_id="checker-1",
            checker_version="checker-v1",
            outcome=AssessmentOutcome.PASSED,
            metric_values=(duplicated_metric, duplicated_metric),
            evaluated_at=NOW,
        )


@pytest.mark.parametrize(
    ("update", "message"),
    (
        ({"variants": (HarnessVariant.EVOLVED_HARNESS,) * 2}, "unique"),
        (
            {
                "baseline_variant": HarnessVariant.EVOLVED_HARNESS,
                "candidate_variant": HarnessVariant.EVOLVED_HARNESS,
            },
            "distinct",
        ),
        ({"candidate_harness_version_id": "harness-v1"}, "differ"),
        (
            {
                "coordinator": ActorIdentity.model(
                    "model-coordinator",
                    "provider",
                    "model-coordinator",
                    None,
                    NOW,
                )
            },
            "independent human",
        ),
    ),
)
def test_campaign_rejects_ambiguous_identity_and_authority(
    update: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        HarnessCampaign.model_validate(_campaign().model_dump(mode="python") | update)


def test_campaign_rejects_partition_policy_model_and_evaluator_confounding() -> None:
    campaign = _campaign()
    wrong_policy = campaign.partitions[0].model_copy(update={"governing_policy_hash": "b" * 64})
    with pytest.raises(ValidationError, match="campaign policy"):
        HarnessCampaign.model_validate(
            campaign.model_dump(mode="python")
            | {"partitions": (wrong_policy, *campaign.partitions[1:])}
        )

    wrong_model_budget = campaign.budgets[0].model_copy(
        update={"budget": campaign.budgets[0].budget.model_copy(update={"model_id": "model-2"})}
    )
    with pytest.raises(ValidationError, match="model identity"):
        HarnessCampaign.model_validate(
            campaign.model_dump(mode="python")
            | {"budgets": (wrong_model_budget, campaign.budgets[1])}
        )

    with pytest.raises(ValidationError, match="evaluator must be independent"):
        HarnessCampaign.model_validate(
            campaign.model_dump(mode="python") | {"evaluator": campaign.candidate_producer}
        )


def test_iteration_metric_confound_and_decision_states_are_exact() -> None:
    iteration = _iteration(0, HarnessPartition.HARNESS_DISCOVERY_TASKS)
    with pytest.raises(ValidationError, match="present together"):
        CampaignIteration.model_validate(iteration.model_dump(mode="python") | {"result_id": None})
    with pytest.raises(ValidationError, match="negative iteration"):
        CampaignIteration.model_validate(
            iteration.model_dump(mode="python") | {"negative_result": True}
        )

    metric = _metric(HarnessPartition.HARNESS_DISCOVERY_TASKS, Decimal("0.9"))
    with pytest.raises(ValidationError, match="finite"):
        PartitionMetric.model_validate(
            metric.model_dump(mode="python") | {"candidate_value": Decimal("NaN")}
        )
    with pytest.raises(ValidationError, match="unique"):
        PartitionMetric.model_validate(
            metric.model_dump(mode="python") | {"result_ids": ("result-1", "result-1")}
        )
    lower_is_better = metric.model_copy(
        update={
            "baseline_value": Decimal("2"),
            "candidate_value": Decimal("1"),
            "higher_is_better": False,
        }
    )
    assert lower_is_better.improved is True
    assert lower_is_better.regressed is False

    with pytest.raises(ValidationError, match="independent analysis"):
        HarnessConfound(
            confound_id="confound-1",
            campaign_id="campaign-v1",
            code=HarnessConfoundCode.EVALUATOR_CHANGED,
            description="evaluator changed",
            affected_variant=HarnessVariant.EVOLVED_HARNESS,
            resolved=True,
            independent_analysis_id=None,
            recorded_at=NOW,
            governing_policy_hash=HASH,
        )

    decision = decide_campaign(_report(transfer_candidate=Decimal("0.9")))
    with pytest.raises(ValidationError, match="admitted"):
        HarnessDecision.model_validate(decision.model_dump(mode="python") | {"admitted": True})
    with pytest.raises(ValidationError, match="rollback target"):
        HarnessDecision.model_validate(
            decision.model_dump(mode="python")
            | {"status": HarnessDecisionStatus.ROLLED_BACK, "rollback_target_id": None}
        )
    with pytest.raises(ValidationError, match="only a rolled-back"):
        HarnessDecision.model_validate(
            decision.model_dump(mode="python") | {"rollback_target_id": "harness-v1"}
        )


def test_report_rejects_duplicate_or_reinterpreted_retained_evidence() -> None:
    report = _report()
    duplicate_observation = report.iterations[1].model_copy(
        update={"observation_id": report.iterations[0].observation_id}
    )
    with pytest.raises(ValidationError, match="observations must be unique"):
        HarnessCampaignReport.model_validate(
            report.model_dump(mode="python")
            | {"iterations": (report.iterations[0], duplicate_observation, *report.iterations[2:])}
        )

    wrong_binding = report.iterations[0].model_copy(update={"task_id": "other-task"})
    with pytest.raises(ValidationError, match="exact campaign partition"):
        HarnessCampaignReport.model_validate(
            report.model_dump(mode="python")
            | {"iterations": (wrong_binding, *report.iterations[1:])}
        )

    with pytest.raises(ValidationError, match="budget comparisons"):
        HarnessCampaignReport.model_validate(
            report.model_dump(mode="python")
            | {
                "budget_comparisons": (
                    BudgetComparison(comparable=False, mismatches=("attempts",)),
                )
            }
        )

    duplicate_metric = report.metrics[0].model_copy(
        update={"partition": report.metrics[1].partition}
    )
    with pytest.raises(ValidationError, match="metrics must be unique"):
        HarnessCampaignReport.model_validate(
            report.model_dump(mode="python")
            | {"metrics": (report.metrics[1], duplicate_metric, *report.metrics[2:])}
        )
    with pytest.raises(ValidationError, match="all five"):
        HarnessCampaignReport.model_validate(
            report.model_dump(mode="python") | {"metrics": report.metrics[:-1]}
        )


def test_decision_ladder_distinguishes_discovery_validation_transfer_and_regression() -> None:
    report = _report(transfer_candidate=Decimal("0.9"))

    no_discovery = report.model_copy(
        update={
            "metrics": tuple(
                item.model_copy(update={"candidate_value": Decimal("0.5")})
                if item.partition is HarnessPartition.HARNESS_DISCOVERY_TASKS
                else item
                for item in report.metrics
            )
        }
    )
    assert decide_campaign(no_discovery).status is HarnessDecisionStatus.INCONCLUSIVE

    no_validation = report.model_copy(
        update={
            "metrics": tuple(
                item.model_copy(update={"candidate_value": Decimal("0.5")})
                if item.partition is HarnessPartition.HARNESS_VALIDATION_TASKS
                else item
                for item in report.metrics
            )
        }
    )
    assert decide_campaign(no_validation).status is HarnessDecisionStatus.DISCOVERY_GAIN

    assert decide_campaign(report).status is HarnessDecisionStatus.TRANSFER_VALIDATED

    safety_regression = report.model_copy(
        update={
            "metrics": tuple(
                item.model_copy(update={"candidate_value": Decimal("0.4")})
                if item.partition is HarnessPartition.HARNESS_SAFETY_TASKS
                else item
                for item in report.metrics
            )
        }
    )
    assert decide_campaign(safety_regression).status is HarnessDecisionStatus.REGRESSION_DETECTED


def test_public_decision_and_export_entrypoints_require_exact_validated_reports() -> None:
    with pytest.raises(TypeError, match="exact HarnessCampaignReport"):
        decide_campaign(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact HarnessCampaignReport"):
        campaign_export_bytes(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact EvaluationBudget"):
        compare_budgets(object(), _budget())

    exported = campaign_export_bytes(_report())
    assert b"campaign-v1" in exported
    assert b"answer_reference" not in exported


def _budget(**updates: object) -> EvaluationBudget:
    payload: dict[str, object] = {
        "model_id": "model-1",
        "model_version": "model-v1",
        "adapter_id": None,
        "feedback_mode": FeedbackMode.NONE,
        "tool_ids": ("tool-1",),
        "attempts": 1,
        "token_limit": 100,
        "reasoning_limit": 50,
        "evaluator_call_limit": 1,
        "wall_clock_seconds": Decimal("10"),
        "cost_limit": Decimal("1"),
        "human_intervention_limit": 0,
    }
    payload.update(updates)
    return EvaluationBudget.model_validate(payload)


def _manifest(partition: HarnessPartition, task_id: str) -> CampaignPartitionManifest:
    task_ids = (task_id,)
    return CampaignPartitionManifest(
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
            None if partition is HarnessPartition.HARNESS_DISCOVERY_TASKS else HASH
        ),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def _campaign(*, extra_attempt: bool = False) -> HarnessCampaign:
    variants = (
        HarnessVariant.UNCHANGED_HARNESS_SINGLE_ATTEMPT,
        HarnessVariant.EVOLVED_HARNESS,
    )
    partitions = tuple(
        _manifest(partition, f"{partition.value.lower()}-task") for partition in HarnessPartition
    )
    candidate_budget = _budget(attempts=2) if extra_attempt else _budget()
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
        candidate_producer=_model_actor("candidate-producer"),
        coordinator=_human_actor("coordinator"),
        partitions=partitions,
        budgets=(
            VariantEvaluationBudget(
                budget_id="budget-baseline",
                variant=variants[0],
                budget=_budget(),
            ),
            VariantEvaluationBudget(
                budget_id="budget-candidate",
                variant=variants[1],
                budget=candidate_budget,
            ),
        ),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def _iteration(
    index: int,
    partition: HarnessPartition,
    *,
    negative: bool = False,
) -> CampaignIteration:
    return CampaignIteration(
        iteration_index=index,
        observation_id=f"observation-{partition.value.split('_')[1].lower()}",
        partition_manifest_id=f"manifest-{partition.value.lower()}",
        task_id=f"{partition.value.lower()}-task",
        partition=partition,
        variant=HarnessVariant.EVOLVED_HARNESS,
        budget_id="budget-candidate",
        attempt=1,
        candidate_output_hash=chr(ord("b") + index) * 64,
        result_id=f"result-{index}",
        outcome=AssessmentOutcome.FAILED if negative else AssessmentOutcome.PASSED,
        negative_result=negative,
        evaluator_version_id="evaluator-v1",
        observed_at=NOW,
    )


def _metric(
    partition: HarnessPartition,
    candidate: Decimal,
    *,
    catastrophic: bool = False,
) -> PartitionMetric:
    return PartitionMetric(
        partition=partition,
        metric_id="correctness",
        baseline_value=Decimal("0.5"),
        candidate_value=candidate,
        higher_is_better=True,
        catastrophic_regression=catastrophic,
        result_ids=(f"metric-{partition.value.lower()}",),
        evaluator_version_id="evaluator-v1",
    )


def _report(
    *,
    campaign: HarnessCampaign | None = None,
    transfer_candidate: Decimal = Decimal("0.4"),
    catastrophic: bool = False,
    admission_requested: bool = False,
) -> HarnessCampaignReport:
    campaign = _campaign() if campaign is None else campaign
    iterations = tuple(
        _iteration(index, partition, negative=partition is HarnessPartition.HARNESS_TRANSFER_TASKS)
        for index, partition in enumerate(HarnessPartition)
    )
    baseline = campaign.budgets[0].budget
    comparisons = tuple(compare_budgets(baseline, item.budget) for item in campaign.budgets[1:])
    return HarnessCampaignReport(
        campaign=campaign,
        expected_iteration_count=len(iterations),
        iterations=iterations,
        negative_observation_ids=("observation-transfer",),
        budget_comparisons=comparisons,
        metrics=(
            _metric(HarnessPartition.HARNESS_DISCOVERY_TASKS, Decimal("0.9")),
            _metric(HarnessPartition.HARNESS_VALIDATION_TASKS, Decimal("0.8")),
            _metric(HarnessPartition.HARNESS_TRANSFER_TASKS, transfer_candidate),
            _metric(
                HarnessPartition.HARNESS_REGRESSION_TASKS,
                Decimal("0.5") if not catastrophic else Decimal("0.0"),
                catastrophic=catastrophic,
            ),
            _metric(HarnessPartition.HARNESS_SAFETY_TASKS, Decimal("0.5")),
        ),
        confounds=(),
        evaluator_audit_id="audit-1",
        evaluator_audit_passed=True,
        measurement_id="measurement-1",
        measurement_accepted=True,
        rollback=None,
        admission_requested=admission_requested,
        decision_authority=_human_actor("decision-authority"),
        reported_at=NOW,
        governing_policy_hash=HASH,
    )


def _human_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity(actor_id=identifier, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity.model(identifier, "provider", identifier, None, NOW)
