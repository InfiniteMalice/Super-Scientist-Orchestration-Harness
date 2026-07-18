from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

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
    SelfImprovementMeasurementRecord,
)
from super_scientist.kernel.transactions.models import (
    AppendResearchRunEvent,
    CreateResearchRun,
    DecideEvaluatorSuccession,
    Proposal,
    ProposalKind,
    ProposeEvaluatorVersion,
    ProposeGovernancePolicyTransition,
    RecordConfigurationVersion,
    RecordEvaluatorAudit,
    RecordSelfImprovementMeasurement,
    RejectionCode,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
HASH = "a" * 64


def _actor(actor_id: str, kind: ActorKind = ActorKind.HUMAN) -> ActorIdentity:
    if kind is ActorKind.MODEL:
        return ActorIdentity.model(actor_id, "provider", actor_id, None, NOW)
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


def test_measurement_rejects_best_only_or_unpartitioned_change_summaries() -> None:
    with pytest.raises(ValidationError, match="partition attempted changes"):
        _measurement(rejected_changes=())

    with pytest.raises(ValidationError, match="consecutive"):
        _measurement(trajectory=(_trajectory_point(0), _trajectory_point(2)))


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


def test_proposal_union_has_eight_fixed_additive_persistent_kinds() -> None:
    proposal_types = (
        CreateResearchRun,
        AppendResearchRunEvent,
        RecordConfigurationVersion,
        RecordEvaluatorAudit,
        RecordSelfImprovementMeasurement,
        ProposeEvaluatorVersion,
        DecideEvaluatorSuccession,
        ProposeGovernancePolicyTransition,
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
    rejected_changes: tuple[str, ...] = ("change-rejected",),
) -> SelfImprovementMeasurementRecord:
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
        trajectory=trajectory or (_trajectory_point(0), _trajectory_point(1)),
        attempted_changes=("change-admitted", "change-rejected"),
        admitted_changes=("change-admitted",),
        rejected_changes=rejected_changes,
        regressions=("countermetric degraded on slice-2",),
        rollback_events=("rollback-drill-1",),
        execution_budget=_budget(10),
        search_budget=_budget(20),
        evaluation_budget=_budget(30),
        judging_budget=_budget(40),
        human_budget=_budget(50),
        usage=ResourceUsage(
            cost_usd=1.0,
            compute_units=2.0,
            tokens=100,
            elapsed_seconds=3.0,
            tool_calls=4,
            human_interventions=1,
        ),
        failures=("one failed candidate retained",),
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
    return PerformanceTrajectoryPoint(
        step_index=step_index,
        metrics=(_metric("accuracy", 0.5 + step_index / 10, protected=False),),
        attempted_change_ids=(f"candidate-{step_index}",),
        admitted_change_ids=(f"candidate-{step_index}",) if step_index else (),
        rejected_change_ids=() if step_index else (f"candidate-{step_index}",),
        regressions=() if step_index else ("baseline miss",),
        rollback_event_ids=(),
        usage=ResourceUsage(
            cost_usd=0.1,
            compute_units=0.2,
            tokens=10,
            elapsed_seconds=0.3,
            tool_calls=1,
            human_interventions=0,
        ),
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
