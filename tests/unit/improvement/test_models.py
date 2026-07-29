from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

import super_scientist.kernel.transactions.models as transaction_models
from super_scientist.domain.configurations.models import (
    AdapterTrainingRequest,
    AgentConfiguration,
    ConfigurationDiff,
    ConfigurationVersion,
    ControlConfiguration,
    DeterministicFakeTrainer,
    ExecutionState,
    FoundationModelConfiguration,
    MemoryConfiguration,
    PromptConfiguration,
    ScaffoldConfiguration,
    ToolConfiguration,
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
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
    ChangeClassification,
    EvaluatorAuditRecord,
    MeasurementDecision,
    MetricObservation,
    PerformanceTrajectoryPoint,
    ResourceBudget,
    ResourceUsage,
    ResourceUsageBreakdown,
    SelfImprovementMeasurementRecord,
    TrajectoryObservation,
)
from super_scientist.kernel.transactions.models import (
    AppendProgressEvent,
    AppendResearchRunEvent,
    BindReportSentence,
    CreateResearchRun,
    DecideCompletion,
    DecideEvaluatorSuccession,
    Proposal,
    ProposalKind,
    ProposeEvaluatorVersion,
    ProposeGovernancePolicyTransition,
    RecordConfigurationVersion,
    RecordEvaluatorAudit,
    RecordEvidenceTrailVersion,
    RecordProgressPlan,
    RecordRunBudget,
    RecordRunCheckpoint,
    RecordSelfImprovementMeasurement,
    RejectionCode,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def _actor(actor_id: str, kind: ActorKind = ActorKind.HUMAN) -> ActorIdentity:
    if kind is ActorKind.MODEL:
        return ActorIdentity.model(actor_id, f"provider-{actor_id}", actor_id, None, NOW)
    return ActorIdentity(actor_id=actor_id, kind=kind, created_at=NOW)


@pytest.mark.parametrize("target", tuple(ChangeTarget))
@pytest.mark.parametrize("persistence", tuple(PersistenceScope))
def test_every_change_classification_round_trips(
    target: ChangeTarget,
    persistence: PersistenceScope,
) -> None:
    value = ChangeClassification(
        target=target,
        loop_closure=LoopClosure.HUMAN_ON_LOOP,
        persistence=persistence,
        verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        grounding=ExternalGrounding.PRIMARY_SOURCE,
        signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
    )

    assert ChangeClassification.model_validate_json(value.model_dump_json()) == value


def test_assessment_provenance_rejects_learned_formal_verifier() -> None:
    with pytest.raises(ValidationError, match="learned assessment cannot claim formal verifier"):
        _assessment(
            actor=_actor("judge", ActorKind.MODEL),
            category=VerificationLevel.FORMAL_VERIFIER,
            mechanism="LEARNED",
        )


def test_evaluator_audit_recomputes_independence() -> None:
    evaluator = _actor("evaluator", ActorKind.MODEL)

    with pytest.raises(ValidationError, match="auditor must be independent"):
        _audit(auditor=evaluator, evaluator=evaluator)

    audit = _audit(auditor=_actor("auditor"), evaluator=evaluator)
    assert audit.independence_enforced is True
    assert audit.result is AssessmentOutcome.PASSED


@pytest.mark.parametrize(
    ("auditor_kind", "audited_role", "shared_field"),
    (
        (ActorKind.HUMAN, "evaluator", "configuration_hash"),
        (ActorKind.TOOL, "proposer", "provider_id"),
        (ActorKind.SERVICE, "candidate_producer", "model_id"),
    ),
)
def test_evaluator_audit_rejects_correlated_aliases_across_actor_kinds(
    auditor_kind: ActorKind,
    audited_role: str,
    shared_field: str,
) -> None:
    shared_value = "b" * 64 if shared_field == "configuration_hash" else f"shared-{shared_field}"
    auditor = _actor("auditor", auditor_kind).model_copy(update={shared_field: shared_value})
    audit = _audit(auditor=_actor("independent-auditor"), evaluator=_actor("evaluator"))
    payload = audit.model_dump(mode="python")
    payload["auditor"] = auditor
    payload[audited_role] = getattr(audit, audited_role).model_copy(
        update={shared_field: shared_value}
    )

    with pytest.raises(ValidationError, match="auditor must be independent"):
        EvaluatorAuditRecord.model_validate(payload)


@pytest.mark.parametrize(
    "weak_category",
    (
        VerificationLevel.RUBRIC_JUDGE,
        VerificationLevel.CROSS_MODEL_AGREEMENT,
        VerificationLevel.SELF_CRITIQUE,
        VerificationLevel.SELF_CONSISTENCY,
        VerificationLevel.MODEL_CONFIDENCE,
        VerificationLevel.MODEL_LIKELIHOOD,
    ),
)
def test_evaluator_audit_rejects_every_non_authoritative_category(
    weak_category: VerificationLevel,
) -> None:
    audit = _audit(auditor=_actor("auditor"), evaluator=_actor("evaluator", ActorKind.MODEL))
    payload = audit.model_dump(mode="python")
    payload["auditor_category"] = weak_category

    with pytest.raises(ValidationError, match="authoritative verification category"):
        EvaluatorAuditRecord.model_validate(payload)


def test_measurement_requires_complete_m0_through_mt_trajectory() -> None:
    with pytest.raises(ValidationError, match="m_0 through m_T"):
        _measurement(trajectory=(_trajectory_point(0),))

    measurement = _measurement()
    assert tuple(point.step_index for point in measurement.trajectory) == (0, 1)
    assert measurement.attempted_changes == (
        *measurement.admitted_changes,
        *measurement.rejected_changes,
    )
    assert measurement.execution_budget != measurement.search_budget


def test_measurement_requires_explicit_unmeasured_coverage_gaps() -> None:
    payload = _measurement().model_dump(mode="python")
    payload.pop("unmeasured_coverage_gaps", None)

    with pytest.raises(ValidationError, match="unmeasured_coverage_gaps"):
        SelfImprovementMeasurementRecord.model_validate(payload)


def test_legacy_measurement_schema_preserves_immutable_missing_gap_bytes() -> None:
    payload = _measurement().model_dump(mode="python")
    payload["schema_version"] = 1
    payload.pop("unmeasured_coverage_gaps")

    measurement = SelfImprovementMeasurementRecord.model_validate(payload)

    assert measurement.unmeasured_coverage_gaps is None
    assert "unmeasured_coverage_gaps" not in measurement.model_dump(mode="json")


def test_legacy_measurement_schema_cannot_be_reused_with_new_gap_data() -> None:
    payload = _measurement().model_dump(mode="python")
    payload["schema_version"] = 1

    with pytest.raises(ValidationError, match="cannot be retroactively extended"):
        SelfImprovementMeasurementRecord.model_validate(payload)


def test_measurement_retains_unmeasured_coverage_gaps() -> None:
    payload = _measurement().model_dump(mode="python")
    payload["unmeasured_coverage_gaps"] = (
        "production traffic remained unmeasured",
        "long-horizon drift remained unmeasured",
    )

    measurement = SelfImprovementMeasurementRecord.model_validate(payload)

    assert measurement.unmeasured_coverage_gaps == (
        "production traffic remained unmeasured",
        "long-horizon drift remained unmeasured",
    )


def test_measurement_rejects_best_only_or_unpartitioned_change_summaries() -> None:
    with pytest.raises(ValidationError, match="trajectory history"):
        _measurement(rejected_changes=())

    with pytest.raises(ValidationError, match="exactly m_0 through m_T"):
        _measurement(trajectory=(_trajectory_point(0), _trajectory_point(2)))


def test_measurement_rejects_best_and_final_summary_that_omits_intermediate_steps() -> None:
    payload = _measurement().model_dump(mode="python")
    final_point = _trajectory_point(3)
    payload.update(
        {
            "expected_final_index": 3,
            "trajectory": (_trajectory_point(0), final_point),
            "peak_observation": _observation(final_point),
            "final_observation": _observation(final_point),
            "attempted_changes": ("candidate-0", "candidate-3"),
            "admitted_changes": ("candidate-0",),
            "rejected_changes": ("candidate-3",),
            "regressions": ("baseline miss",),
            "rollback_events": ("rollback-drill-3",),
            "usage_by_category": _usage_breakdown(execution=_usage(2)),
            "usage": _usage(2),
        }
    )

    with pytest.raises(ValidationError, match="exactly m_0 through m_T"):
        SelfImprovementMeasurementRecord.model_validate(payload)


def test_measurement_reconciles_point_category_and_aggregate_usage() -> None:
    payload = _measurement().model_dump(mode="python")
    payload["usage"] = _usage(1)

    with pytest.raises(ValidationError, match="aggregate usage"):
        SelfImprovementMeasurementRecord.model_validate(payload)

    point_payload = _trajectory_point(0).model_dump(mode="python")
    point_payload["usage"] = _zero_usage()
    with pytest.raises(ValidationError, match="point aggregate usage"):
        PerformanceTrajectoryPoint.model_validate(point_payload)


def test_measurement_binds_peak_final_change_grounding_and_full_history() -> None:
    payload = _measurement().model_dump(mode="python")
    final_observation = payload["final_observation"]
    assert isinstance(final_observation, dict)
    final_observation["step_index"] = 0
    with pytest.raises(ValidationError, match="final observation"):
        SelfImprovementMeasurementRecord.model_validate(payload)

    payload = _measurement().model_dump(mode="python")
    trajectory = payload["trajectory"]
    assert isinstance(trajectory, tuple)
    first = trajectory[0]
    assert isinstance(first, dict)
    first["change_id"] = "unrelated-change"
    with pytest.raises(ValidationError, match="change and grounding"):
        SelfImprovementMeasurementRecord.model_validate(payload)

    payload = _measurement().model_dump(mode="python")
    classification = payload["classification"]
    assert isinstance(classification, dict)
    classification["grounding"] = ExternalGrounding.PRIMARY_SOURCE
    with pytest.raises(ValidationError, match="change and grounding"):
        SelfImprovementMeasurementRecord.model_validate(payload)


def test_new_rejection_codes_are_appended_with_stable_values() -> None:
    expected = (
        "MISSING_ENTITY",
        "INVALID_LINEAGE",
        "INSUFFICIENT_GROUNDING",
        "PROHIBITED_CLOSED_LOOP",
        "UNMATCHED_BUDGETS",
        "PROTECTED_DATA_ACCESS",
        "STALE_HANDBOOK_MAPPING",
        "INVALID_DEPENDENCY",
        "FALSE_FINISH",
        "CIRCULAR_EVALUATOR_APPROVAL",
        "BENCHMARK_SPECIFIC_ADMISSION",
        "DUPLICATE_RULE",
        "UNRESOLVED_RULE_CONFLICT",
        "EXPERIMENTAL_PRIMITIVE_QUARANTINED",
    )
    existing = tuple(RejectionCode)[:11]

    assert tuple(code.value for code in tuple(RejectionCode)[11:]) == expected
    assert tuple(code.value for code in existing) == (
        "INVALID_PROPOSAL",
        "ENTITY_ID_MISMATCH",
        "ENTITY_ALREADY_EXISTS",
        "SELF_APPROVAL",
        "MISSING_EVIDENCE",
        "EVIDENCE_HASH_MISMATCH",
        "INVALID_STATUS_TRANSITION",
        "INDEPENDENT_REVIEW_REQUIRED",
        "PERMISSION_DENIED",
        "IDEMPOTENCY_CONFLICT",
        "POLICY_HASH_MISMATCH",
    )


def test_proposal_union_has_thirty_seven_fixed_additive_persistent_kinds() -> None:
    proposal_types = (
        CreateResearchRun,
        AppendResearchRunEvent,
        RecordConfigurationVersion,
        RecordEvaluatorAudit,
        RecordSelfImprovementMeasurement,
        ProposeEvaluatorVersion,
        DecideEvaluatorSuccession,
        ProposeGovernancePolicyTransition,
        RecordProgressPlan,
        AppendProgressEvent,
        RecordRunBudget,
        RecordRunCheckpoint,
        DecideCompletion,
        transaction_models.ProposeEvidenceTrailNodes,
        transaction_models.ProposeEvidenceTrailRelations,
        RecordEvidenceTrailVersion,
        BindReportSentence,
        transaction_models.RecordRuleIncident,
        transaction_models.ProposeBehavioralRule,
        transaction_models.ImportReviewerAssessment,
        transaction_models.ConsolidateBehavioralRule,
        transaction_models.ProposePrimitiveVersion,
        transaction_models.RecordPrimitiveEvaluation,
        transaction_models.AdmitPrimitiveVersion,
        transaction_models.ProposeHypothesisVersion,
        transaction_models.RegisterExecutableModel,
        transaction_models.RegisterVerificationMechanism,
        transaction_models.RecordSimulationResult,
        transaction_models.RecordVerificationResult,
        transaction_models.RecordCounterexample,
        transaction_models.ReviseHypothesis,
        transaction_models.AdmitHypothesis,
        transaction_models.CreateHarnessCampaign,
        transaction_models.RecordHarnessIteration,
        transaction_models.RecordHarnessProtectedResult,
        transaction_models.RecordHarnessConfound,
        transaction_models.DecideHarnessCampaign,
    )
    expected = (
        "create_research_run",
        "append_research_run_event",
        "record_configuration_version",
        "record_evaluator_audit",
        "record_self_improvement_measurement",
        "propose_evaluator_version",
        "decide_evaluator_succession",
        "propose_governance_policy_transition",
        "record_progress_plan",
        "append_progress_event",
        "record_run_budget",
        "record_run_checkpoint",
        "decide_completion",
        "propose_evidence_trail_nodes",
        "propose_evidence_trail_relations",
        "record_evidence_trail_version",
        "bind_report_sentence",
        "record_rule_incident",
        "propose_behavioral_rule",
        "import_reviewer_assessment",
        "consolidate_behavioral_rule",
        "propose_primitive_version",
        "record_primitive_evaluation",
        "admit_primitive_version",
        "propose_hypothesis_version",
        "register_executable_model",
        "register_verification_mechanism",
        "record_simulation_result",
        "record_verification_result",
        "record_counterexample",
        "revise_hypothesis",
        "admit_hypothesis",
        "create_harness_campaign",
        "record_harness_iteration",
        "record_harness_protected_result",
        "record_harness_confound",
        "decide_harness_campaign",
    )
    legacy = ("add_evidence", "propose_claim", "transition_claim")

    assert (
        tuple(model.model_fields["proposal_type"].default for model in proposal_types) == expected
    )
    assert TypeAdapter(ProposalKind).json_schema()["enum"] == [*legacy, *expected]
    discriminator = TypeAdapter(Proposal).json_schema()["discriminator"]["mapping"]
    assert set(discriminator) == {*legacy, *expected, "invalid_proposal"}


def test_execution_state_is_not_part_of_persistent_configuration() -> None:
    version = _configuration_version()

    assert "execution_state" not in version.model_dump()
    assert ConfigurationDiff.between(version, version).changed_layers == ()
    assert "ExecutionState" not in ConfigurationVersion.model_json_schema()["$defs"]
    assert (
        ExecutionState(
            execution_state_id="state-1",
            run_id="run-1",
            step_index=0,
            state_digest=HASH,
            observed_at=NOW,
        ).run_id
        == "run-1"
    )


def test_configuration_diff_names_only_changed_persistent_layers() -> None:
    baseline = _configuration_version()
    changed_prompt = baseline.model_copy(
        update={
            "agent_configuration": baseline.agent_configuration.model_copy(
                update={
                    "scaffold": baseline.agent_configuration.scaffold.model_copy(
                        update={
                            "prompt": PromptConfiguration(
                                prompt_configuration_id="prompt-2",
                                template_hash="b" * 64,
                                variable_names=("question",),
                            )
                        }
                    )
                }
            ),
            "configuration_version_id": "configuration-2",
        }
    )

    assert ConfigurationDiff.between(baseline, changed_prompt).changed_layers == ("PROMPT",)


def test_fake_trainer_returns_deterministic_metadata_without_model_runtime() -> None:
    request = AdapterTrainingRequest(
        candidate_id="adapter-candidate-1",
        base_model_configuration_id="foundation-1",
        dataset_lineage_ids=("dataset-1", "dataset-2"),
        evaluation_id="evaluation-1",
        rollback_configuration_id="configuration-1",
        requested_at=NOW,
    )
    trainer = DeterministicFakeTrainer()

    first = trainer.train(request)
    second = trainer.train(request)

    assert first == second
    assert first.dataset_lineage_ids == request.dataset_lineage_ids
    assert len(first.artifact_hash) == 64
    assert first.promoted is False


def _assessment(
    *,
    actor: ActorIdentity | None = None,
    category: VerificationLevel = VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    mechanism: str = "HUMAN",
) -> AssessmentProvenance:
    return AssessmentProvenance(
        actor=actor or _actor("auditor"),
        actor_version="auditor-v1",
        category=category,
        deterministic_or_learned=mechanism,
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("declared fixture assumptions",),
        evidence_ids=("evidence-1",),
        checks_run=("check-1",),
        limitations=("fixture coverage only",),
        result=AssessmentOutcome.PASSED,
        assessed_at=NOW,
        governing_policy_hash=HASH,
    )


def _audit(
    *,
    auditor: ActorIdentity,
    evaluator: ActorIdentity,
) -> EvaluatorAuditRecord:
    return EvaluatorAuditRecord(
        evaluator_audit_id="audit-1",
        auditor=auditor,
        auditor_version="auditor-v1",
        auditor_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        evaluator=evaluator,
        evaluator_version="evaluator-v1",
        proposer=_actor("proposer", ActorKind.MODEL),
        candidate_producer=_actor("producer", ActorKind.SERVICE),
        auditor_to_evaluator=ActorRelationship.INDEPENDENT,
        auditor_to_proposer=ActorRelationship.INDEPENDENT,
        auditor_to_candidate_producer=ActorRelationship.INDEPENDENT,
        independence_enforced=True,
        evidence_ids=("evidence-1",),
        checks_run=("audit-check",),
        assumptions=("identity metadata is accurate",),
        limitations=("one protected dataset",),
        result=AssessmentOutcome.PASSED,
        audited_at=NOW,
        governing_policy_hash=HASH,
    )


def _measurement(
    *,
    trajectory: tuple[PerformanceTrajectoryPoint, ...] | None = None,
    rejected_changes: tuple[str, ...] | None = None,
) -> SelfImprovementMeasurementRecord:
    points = trajectory or (_trajectory_point(0), _trajectory_point(1))
    attempted = tuple(change_id for point in points for change_id in point.attempted_change_ids)
    admitted = tuple(change_id for point in points for change_id in point.admitted_change_ids)
    rejected = tuple(change_id for point in points for change_id in point.rejected_change_ids)
    regressions = tuple(regression for point in points for regression in point.regressions)
    rollbacks = tuple(rollback_id for point in points for rollback_id in point.rollback_event_ids)
    aggregate_usage = _usage(len(points))
    final_point = points[-1]
    return SelfImprovementMeasurementRecord(
        measurement_id="measurement-1",
        change_id="change-1",
        run_id="run-1",
        classification=ChangeClassification(
            target=ChangeTarget.GOVERNANCE_POLICY,
            loop_closure=LoopClosure.HUMAN_IN_LOOP,
            persistence=PersistenceScope.GOVERNANCE_POLICY,
            verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            grounding=ExternalGrounding.CONTROLLED_EXPERIMENT,
            signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
        ),
        proposer=_actor("proposer", ActorKind.MODEL),
        evaluator=_actor("evaluator", ActorKind.MODEL),
        evaluator_version="evaluator-v1",
        evaluator_tier="protected-external",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        baseline_version_id="policy-v1",
        candidate_version_id="policy-v2",
        protected_metrics=(_metric("protected-accuracy", 0.8, protected=True),),
        countermetrics=(_metric("failure-rate", 0.1, protected=False),),
        expected_final_index=final_point.step_index,
        trajectory=points,
        peak_observation=_observation(final_point),
        final_observation=_observation(final_point),
        attempted_changes=attempted,
        admitted_changes=admitted,
        rejected_changes=rejected if rejected_changes is None else rejected_changes,
        regressions=regressions,
        rollback_events=rollbacks,
        execution_budget=_budget(10),
        search_budget=_budget(20),
        evaluation_budget=_budget(30),
        judging_budget=_budget(40),
        human_budget=_budget(50),
        usage_by_category=_usage_breakdown(execution=aggregate_usage),
        usage=aggregate_usage,
        failures=("one failed candidate retained",),
        unmeasured_coverage_gaps=("production traffic remained unmeasured",),
        rollback_target_id="policy-v1",
        evaluator_audit_id="audit-1",
        decision=MeasurementDecision.ACCEPTED,
        decision_authority=_actor("human-authority"),
        decided_at=NOW,
        governing_policy_hash=HASH,
    )


def _metric(metric_id: str, value: float, *, protected: bool) -> MetricObservation:
    return MetricObservation(
        metric_id=metric_id,
        value=value,
        source_id="evaluation-1",
        protected=protected,
        external=True,
    )


def _trajectory_point(step_index: int) -> PerformanceTrajectoryPoint:
    usage = _usage()
    return PerformanceTrajectoryPoint(
        step_index=step_index,
        change_id="change-1",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        metrics=(_metric("accuracy", 0.5 + step_index / 10, protected=False),),
        attempted_change_ids=(f"candidate-{step_index}",),
        admitted_change_ids=(f"candidate-{step_index}",) if step_index == 0 else (),
        rejected_change_ids=() if step_index == 0 else (f"candidate-{step_index}",),
        regressions=() if step_index else ("baseline miss",),
        rollback_event_ids=() if step_index == 0 else (f"rollback-drill-{step_index}",),
        usage_by_category=_usage_breakdown(execution=usage),
        usage=usage,
    )


def _observation(point: PerformanceTrajectoryPoint) -> TrajectoryObservation:
    return TrajectoryObservation(step_index=point.step_index, metrics=point.metrics)


def _usage(multiplier: int = 1) -> ResourceUsage:
    return ResourceUsage(
        cost_usd=0.1 * multiplier,
        compute_units=0.2 * multiplier,
        tokens=multiplier,
        elapsed_seconds=0.3 * multiplier,
        tool_calls=multiplier,
        human_interventions=0,
    )


def _zero_usage() -> ResourceUsage:
    return ResourceUsage(
        cost_usd=0.0,
        compute_units=0.0,
        tokens=0,
        elapsed_seconds=0.0,
        tool_calls=0,
        human_interventions=0,
    )


def _usage_breakdown(
    *,
    execution: ResourceUsage | None = None,
    search: ResourceUsage | None = None,
    evaluation: ResourceUsage | None = None,
    judging: ResourceUsage | None = None,
    human: ResourceUsage | None = None,
) -> ResourceUsageBreakdown:
    return ResourceUsageBreakdown(
        execution=execution or _zero_usage(),
        search=search or _zero_usage(),
        evaluation=evaluation or _zero_usage(),
        judging=judging or _zero_usage(),
        human=human or _zero_usage(),
    )


def _budget(multiplier: int) -> ResourceBudget:
    return ResourceBudget(
        cost_usd=float(multiplier),
        compute_units=float(multiplier),
        tokens=multiplier,
        elapsed_seconds=float(multiplier),
        tool_calls=multiplier,
        human_interventions=multiplier,
    )


def _configuration_version() -> ConfigurationVersion:
    prompt = PromptConfiguration(
        prompt_configuration_id="prompt-1",
        template_hash=HASH,
        variable_names=("question",),
    )
    memory = MemoryConfiguration(
        memory_configuration_id="memory-1",
        schema_hash=HASH,
        cross_run_enabled=False,
    )
    tools = ToolConfiguration(
        tool_configuration_id="tools-1",
        tool_ids=("read-only-tool",),
        routing_hash=HASH,
    )
    control = ControlConfiguration(
        control_configuration_id="control-1",
        policy_hash=HASH,
        max_steps=10,
    )
    scaffold = ScaffoldConfiguration(
        scaffold_configuration_id="scaffold-1",
        prompt=prompt,
        memory=memory,
        tools=tools,
        control=control,
    )
    agent = AgentConfiguration(
        agent_configuration_id="agent-1",
        foundation_model=FoundationModelConfiguration(
            foundation_model_configuration_id="foundation-1",
            provider_id="provider",
            model_id="model",
            adapter_id=None,
        ),
        scaffold=scaffold,
    )
    return ConfigurationVersion(
        configuration_version_id="configuration-1",
        agent_configuration=agent,
        predecessor_configuration_version_id=None,
        rollback_configuration_version_id="configuration-1",
        created_by=_actor("config-author"),
        created_at=NOW,
        governing_policy_hash=HASH,
    )
