"""Run the exact deterministic offline governed-adaptation vertical slice."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from super_scientist.application.harness_eval.service import decide_campaign
from super_scientist.application.hypothesis_testing.simulators import SimulatorRegistry
from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicy,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.behavioral_rules.consolidation import (
    build_candidate_diff,
    classify_overlap,
)
from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ConflictClassification,
    RecommendationDisposition,
    ReviewerAssessment,
    ReviewerRole,
    RuleAction,
    RuleAuthority,
    RuleIncident,
    RuleIncidentKind,
    RuleRegressionCase,
    RuleStatus,
)
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.evidence_trails.models import (
    EvidenceTrailNode,
    ExactSourceSpan,
    StructuralLocation,
    StructuralLocationKind,
    TrailNodeRole,
)
from super_scientist.domain.harness_eval.models import (
    CampaignPartitionManifest,
    EvaluationBudget,
    FeedbackMode,
    HarnessCampaign,
    HarnessCampaignReport,
    HarnessDecisionStatus,
    HarnessPartition,
    HarnessVariant,
    PartitionMetric,
    VariantEvaluationBudget,
    compare_evaluation_budgets,
    partition_manifest_hash,
)
from super_scientist.domain.hypotheses.models import (
    ExecutableModelSpec,
    ExecutionMode,
    HypothesisSpec,
    ImportedPatternStatus,
    ModelInput,
    ModelType,
    NumericField,
    RevisionRecord,
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
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.progress.calculations import calculate_progress, detect_false_finish
from super_scientist.domain.progress.models import (
    FalseFinishResult,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressValidationEvent,
)
from super_scientist.domain.research_runs.models import ResearchRun, RunBudgetAllocation
from super_scientist.handbook.models import RuleBehaviorLink, SourceBehaviorLink
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    ProposeGovernancePolicyTransition,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
DECIDED_AT = NOW + timedelta(seconds=1)

STEP_CODES = (
    "initialize_v1_kernel",
    "approve_v1_to_v2_transition",
    "add_synthetic_source_evidence",
    "create_research_run_and_progress_plan",
    "propose_competing_thermal_hypotheses",
    "register_builtin_thermal_simulator",
    "record_predictions_and_falsification_criteria",
    "construct_and_validate_natural_evidence_trail",
    "validate_partial_progress",
    "reject_false_finish",
    "preserve_failed_hypothesis_and_revision",
    "record_incident_and_propose_rule",
    "import_five_reviewer_roles",
    "consolidate_canonical_boundary_rule",
    "preserve_incident_regression_cases",
    "link_rule_and_verify_source_mapping",
    "compare_matched_budget_harness_candidate",
    "reject_benchmark_specific_discovery_gain",
    "admit_held_out_transfer_candidate",
    "export_self_improvement_measurement_report",
    "verify_workspace_and_mixed_policy_audit",
)


class DeterministicClock:
    def __init__(self) -> None:
        self._index = 0

    def now(self) -> datetime:
        value = NOW + timedelta(microseconds=self._index)
        self._index += 1
        return value


@dataclass(frozen=True)
class Step:
    number: int
    code: str
    completed: bool = True


class VerticalSlice:
    def __init__(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{(workspace / 'governed-adaptation.db').as_posix()}"
        upgrade_database(database_url)
        self.engine = create_database_engine(database_url)
        self.artifacts = FileArtifactStore(workspace / "artifacts")
        self.clock = DeterministicClock()
        self.steps: list[Step] = []
        self.prior = _v1_policy()
        self.candidate = _v2_policy()
        self.proposer = _model_actor("thermal-scientist")
        self.approver = _human_actor("governance-approver")
        self.validator = _human_actor("thermal-validator")
        self.evidence: EvidenceRecord | None = None
        self.transition: ProposeGovernancePolicyTransition | None = None
        self.progress_plan: ProgressPlan | None = None
        self.progress_events: tuple[ProgressValidationEvent, ...] = ()
        self.hypotheses: tuple[HypothesisSpec, HypothesisSpec] | None = None
        self.revision: RevisionRecord | None = None
        self.rule: BehavioralRuleVersion | None = None
        self.assessments: tuple[ReviewerAssessment, ...] = ()
        self.regressions: tuple[RuleRegressionCase, ...] = ()
        self.first_harness_status: HarnessDecisionStatus | None = None
        self.second_harness_status: HarnessDecisionStatus | None = None
        self.false_finish_rejected = False
        self.audit_valid = False

    def uow_factory(self) -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(self.engine)

    def run(self) -> dict[str, object]:
        try:
            self._step_1_initialize()
            self._step_2_transition()
            self._step_3_evidence()
            self._step_4_run_and_plan()
            self._step_5_hypotheses()
            self._step_6_simulator()
            self._step_7_predictions()
            self._step_8_trail()
            self._step_9_progress()
            self._step_10_false_finish()
            self._step_11_revision()
            incidents, proposed, existing = self._step_12_incidents_and_rule()
            self._step_13_reviews(incidents, proposed)
            self._step_14_consolidate(incidents, proposed, existing)
            self._step_15_regressions()
            self._step_16_behavior_mapping()
            campaign = self._step_17_campaign()
            self._step_18_benchmark_specific(campaign)
            self._step_19_admit_transfer(campaign)
            self._step_20_measurement_report()
            self._step_21_verify()
            return {
                "policy_versions": [1, 2],
                "false_finish_rejected": self.false_finish_rejected,
                "failed_hypothesis_preserved": self.revision is not None,
                "first_harness_candidate_status": self.first_harness_status.value,
                "second_harness_candidate_status": self.second_harness_status.value,
                "audit_valid": self.audit_valid,
                "steps": [step.__dict__ for step in self.steps],
            }
        finally:
            self.engine.dispose()

    def _complete(self, number: int) -> None:
        code = STEP_CODES[number - 1]
        self.steps.append(Step(number=number, code=code))

    def _step_1_initialize(self) -> None:
        with self.uow_factory() as unit_of_work:
            unit_of_work.repositories().policies.add_and_activate(self.prior, NOW)
        self._complete(1)

    def _step_2_transition(self) -> None:
        transition = _governance_transition(self.prior, self.candidate)
        coordinator = TransactionCoordinator(
            self.uow_factory,
            self.prior,
            self.clock,
            self.artifacts,
        )
        decision = coordinator.submit(transition)
        if not decision.accepted:
            raise RuntimeError(f"governance transition failed: {decision}")
        self.transition = transition
        self._complete(2)

    def _step_3_evidence(self) -> None:
        content = (
            b"00 heater engaged; chamber 20.0 C. "
            b"10 chamber peaked at 28.4 C. "
            b"20 sensor A drifted while sensor B remained calibrated. "
            b"30 cooling restored chamber to 22.1 C."
        )
        artifact = self.artifacts.put(content, "text/plain")
        evidence = EvidenceRecord(
            evidence_id="thermal-incident-evidence",
            evidence_type="synthetic_equipment_incident",
            source_locator="fixture://ssöh/thermal-incident",
            retrieved_at=NOW,
            artifact=artifact,
            provenance={
                "collector": "governed-adaptation-example",
                "external_grounding": ExternalGrounding.PRIMARY_SOURCE.value,
            },
            license="SSOH synthetic fixture",
            ingestion_actor_id=self.proposer.actor_id,
        )
        coordinator = TransactionCoordinator(
            self.uow_factory,
            self.candidate,
            self.clock,
            self.artifacts,
        )
        decision = coordinator.submit(
            AddEvidence(
                proposal_id="03-add-thermal-evidence",
                idempotency_key="03-add-thermal-evidence-key",
                proposer=self.proposer,
                evidence=evidence,
            )
        )
        if not decision.accepted:
            raise RuntimeError(f"evidence admission failed: {decision}")
        with self.uow_factory() as unit_of_work:
            stored = unit_of_work.repositories().evidence.get(evidence.evidence_id)
        if stored is None:
            raise RuntimeError("accepted evidence was not projected")
        self.evidence = stored
        self._complete(3)

    def _step_4_run_and_plan(self) -> None:
        run = ResearchRun(
            run_id="thermal-run",
            charter="Explain the synthetic thermal excursion without hiding failed work",
            scope=("synthetic thermal chamber", "synthetic equipment incident"),
            creator=self.proposer,
            created_at=NOW,
            active_governance_policy_hash=self.candidate.policy_hash,
            model_configuration_version_id=None,
            scaffold_configuration_version_id=None,
            budget_allocation=_run_budget(20),
            final_validator=self.validator,
            final_validator_version="thermal-validator-v1",
            environment_snapshot_id="offline-python-3.12",
        )
        subtasks = (
            ProgressSubtask(
                subtask_id="thermal-evidence-check",
                plan_version_id="thermal-plan-v1",
                description="Validate retained incident evidence",
                dependency_ids=(),
                completion_criteria=("hash and exact span pass",),
                validator=self.validator,
                validator_version="thermal-validator-v1",
                weight=Decimal("0.5"),
                evidence_requirements=("synthetic incident note",),
                order=1,
            ),
            ProgressSubtask(
                subtask_id="thermal-transfer-check",
                plan_version_id="thermal-plan-v1",
                description="Validate held-out transfer",
                dependency_ids=("thermal-evidence-check",),
                completion_criteria=("transfer metric improves",),
                validator=self.validator,
                validator_version="thermal-validator-v1",
                weight=Decimal("0.5"),
                evidence_requirements=("held-out result hash",),
                order=2,
            ),
        )
        self.progress_plan = ProgressPlan(
            plan_version_id="thermal-plan-v1",
            run_id=run.run_id,
            version=1,
            subtasks=subtasks,
            created_at=NOW,
            governing_policy_hash=self.candidate.policy_hash,
        )
        self._complete(4)

    def _step_5_hypotheses(self) -> None:
        evidence = _required(self.evidence)
        common = {
            "scope": ("bounded synthetic thermal chamber",),
            "variables": ("temperature", "heater_delta", "cooling_rate"),
            "primitive_version_ids": (),
            "evidence_ids": (evidence.evidence_id,),
            "proposer": self.proposer,
            "created_at": NOW,
            "governing_policy_hash": self.candidate.policy_hash,
        }
        bounded = HypothesisSpec(
            hypothesis_version_id="thermal-bounded-v1",
            hypothesis_id="thermal-bounded",
            version=1,
            statement="Bounded heating plus ambient cooling explains the excursion.",
            assumptions=("heater input is nonnegative", "cooling rate is bounded"),
            predictions=("peak temperature remains below 30 C",),
            falsification_conditions=("simulated peak reaches or exceeds 30 C",),
            imported_pattern_status=ImportedPatternStatus.TRANSFER_TESTING,
            **common,
        )
        failed = HypothesisSpec(
            hypothesis_version_id="thermal-runaway-v1",
            hypothesis_id="thermal-runaway",
            version=1,
            statement="Unbounded heater gain explains the excursion.",
            assumptions=("cooling is negligible",),
            predictions=("peak temperature reaches at least 40 C",),
            falsification_conditions=("measured and simulated peak remains below 40 C",),
            imported_pattern_status=ImportedPatternStatus.TRANSFER_TESTING,
            **common,
        )
        self.hypotheses = (bounded, failed)
        self._complete(5)

    def _step_6_simulator(self) -> None:
        bounded, _ = _required(self.hypotheses)
        model = ExecutableModelSpec(
            model_spec_id="thermal-model-v1",
            hypothesis_version_id=bounded.hypothesis_version_id,
            model_type=ModelType.DETERMINISTIC_SIMULATOR,
            execution_mode=ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
            artifact_hash=None,
            artifact_media_type=None,
            artifact_size_bytes=None,
            artifact_name="source-controlled thermal chamber simulator",
            builtin_simulator_id="thermal-chamber-v1",
            input_schema_id="thermal-chamber-input-v1",
            output_schema_id="thermal-chamber-output-v1",
            deterministic_seed=17,
            max_steps=20,
            max_state_bytes=10_000,
            registered_by=self.proposer,
            created_at=NOW,
            governing_policy_hash=self.candidate.policy_hash,
        )
        model_input = ModelInput(
            model_input_id="thermal-input-v1",
            schema_id=model.input_schema_id,
            values=(
                NumericField(name="initial_temperature", value=20.0),
                NumericField(name="ambient_temperature", value=20.0),
                NumericField(name="heater_delta", value=1.8),
                NumericField(name="cooling_rate", value=0.15),
                NumericField(name="steps", value=6),
            ),
            deterministic_seed=17,
        )
        output = SimulatorRegistry().execute(
            model,
            model_input,
            output_id="thermal-output-v1",
        )
        self.model = model
        self.model_input = model_input
        self.model_output = output
        self._complete(6)

    def _step_7_predictions(self) -> None:
        bounded, failed = _required(self.hypotheses)
        peak = float(self.model_output.numeric_value("peak_temperature"))
        if not peak < 30.0 or not peak < 40.0:
            raise RuntimeError("thermal prediction fixture lost its falsification boundary")
        if not bounded.falsification_conditions or not failed.falsification_conditions:
            raise RuntimeError("hypotheses must retain falsification criteria")
        self._complete(7)

    def _step_8_trail(self) -> None:
        evidence = _required(self.evidence)
        source = self.artifacts.read(evidence.artifact).decode("utf-8")
        spans = (
            ("trail-peak", "10 chamber peaked at 28.4 C.", TrailNodeRole.REQUIRED, 10),
            (
                "trail-sensor",
                "20 sensor A drifted while sensor B remained calibrated.",
                TrailNodeRole.SUPPORTING,
                20,
            ),
        )
        nodes = []
        for node_id, text, role, position in spans:
            start = source.index(text)
            end = start + len(text)
            nodes.append(
                EvidenceTrailNode(
                    node_id=node_id,
                    trail_version_id="thermal-trail-v1",
                    source_id="thermal-incident-note",
                    evidence_id=evidence.evidence_id,
                    exact_span=ExactSourceSpan(start=start, end=end, text=text),
                    structural_location=StructuralLocation(
                        kind=StructuralLocationKind.EVENT_SEQUENCE,
                        locator=f"event-{position}",
                        start=start,
                        end=end,
                    ),
                    content_hash=sha256_hex(text.encode("utf-8")),
                    role=role,
                    temporal_position=position,
                    causal_position=None,
                    confidence=1.0,
                    necessity=role is TrailNodeRole.REQUIRED,
                )
            )
        if any(
            source[node.exact_span.start : node.exact_span.end] != node.exact_span.text
            or sha256_hex(node.exact_span.text.encode("utf-8")) != node.content_hash
            for node in nodes
        ):
            raise RuntimeError("natural evidence trail failed exact-span validation")
        self.trail_nodes = tuple(nodes)
        self._complete(8)

    def _step_9_progress(self) -> None:
        plan = _required(self.progress_plan)
        validated, provisional = plan.subtasks
        self.progress_events = (
            ProgressValidationEvent(
                event_id="progress-evidence-validated",
                run_id=plan.run_id,
                plan_version_id=plan.plan_version_id,
                subtask_id=validated.subtask_id,
                requested_status=ProgressStatus.VALIDATED,
                completion_proposer=self.proposer,
                validator=self.validator,
                validator_version=validated.validator_version,
                validator_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                relationship_to_run_creator=ActorRelationship.INDEPENDENT,
                relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
                are_independent=True,
                evidence_ids=(_required(self.evidence).evidence_id,),
                checks_run=("exact-span-check",),
                assumptions=("synthetic source bytes are retained",),
                limitations=("one offline fixture",),
                result=AssessmentOutcome.PASSED,
                occurred_at=NOW,
                governing_policy_hash=self.candidate.policy_hash,
            ),
            ProgressValidationEvent(
                event_id="progress-transfer-provisional",
                run_id=plan.run_id,
                plan_version_id=plan.plan_version_id,
                subtask_id=provisional.subtask_id,
                requested_status=ProgressStatus.PROVISIONALLY_COMPLETE,
                completion_proposer=self.proposer,
                validator=self.validator,
                validator_version=provisional.validator_version,
                validator_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                relationship_to_run_creator=ActorRelationship.INDEPENDENT,
                relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
                are_independent=True,
                evidence_ids=(),
                checks_run=("transfer-pending-check",),
                assumptions=("held-out transfer is pending",),
                limitations=("not official progress",),
                result=AssessmentOutcome.INCONCLUSIVE,
                occurred_at=NOW + timedelta(seconds=1),
                governing_policy_hash=self.candidate.policy_hash,
            ),
        )
        summary = calculate_progress(plan, self.progress_events)
        if summary.official_weight != Decimal("0.5") or summary.provisional_weight != Decimal(
            "0.5"
        ):
            raise RuntimeError("progress separation changed")
        self.progress_summary = summary
        self._complete(9)

    def _step_10_false_finish(self) -> None:
        finding = detect_false_finish(
            voluntary_termination=True,
            claims_completion=True,
            final_validator_result=AssessmentOutcome.FAILED,
            validated_weight=self.progress_summary.official_weight,
            unused_budget=True,
        )
        self.false_finish_rejected = finding.result is FalseFinishResult.FALSE_FINISH
        if not self.false_finish_rejected:
            raise RuntimeError("false finish was not rejected")
        self._complete(10)

    def _step_11_revision(self) -> None:
        _, failed = _required(self.hypotheses)
        revised = failed.model_copy(
            update={
                "hypothesis_version_id": "thermal-runaway-v2",
                "version": 2,
                "statement": "Sensor drift, not runaway heating, explains the high reading.",
                "assumptions": ("sensor A drifted", "sensor B remained calibrated"),
                "predictions": ("sensor B and chamber model remain below 30 C",),
                "falsification_conditions": ("sensor B independently reaches 30 C",),
            }
        )
        HypothesisSpec.model_validate(revised)
        self.revision = RevisionRecord(
            revision_id="thermal-revision-v2",
            hypothesis_id=failed.hypothesis_id,
            prior_hypothesis_version_id=failed.hypothesis_version_id,
            prior_version=1,
            resulting_hypothesis_version_id=revised.hypothesis_version_id,
            resulting_version=2,
            triggering_verification_result_ids=("thermal-runaway-failed-check",),
            considered_counterexample_ids=("thermal-peak-counterexample",),
            assumptions_added=("sensor A drifted",),
            assumptions_removed=("cooling is negligible",),
            assumptions_changed=(),
            variables_added=("sensor_identity",),
            variables_removed=(),
            variables_changed=(),
            mechanism_changes=("compare calibrated sensor B",),
            preserved_elements=("thermal excursion scope",),
            changed_predictions=revised.predictions,
            changed_falsification_conditions=revised.falsification_conditions,
            author=self.proposer,
            revised_at=NOW,
            governing_policy_hash=self.candidate.policy_hash,
        )
        self.revised_hypothesis = revised
        self._complete(11)

    def _step_12_incidents_and_rule(
        self,
    ) -> tuple[tuple[RuleIncident, RuleIncident], BehavioralRuleVersion, BehavioralRuleVersion]:
        evidence_id = _required(self.evidence).evidence_id
        incidents = (
            RuleIncident(
                incident_id="incident-overheat",
                incident_kind=RuleIncidentKind.VERIFIED_FAILURE,
                summary="A single drifting sensor falsely indicated runaway heating.",
                evidence_ids=(evidence_id,),
                observed_at=NOW,
                reported_by=self.proposer,
                recorded_at=NOW,
                governing_policy_hash=self.candidate.policy_hash,
            ),
            RuleIncident(
                incident_id="incident-sensor",
                incident_kind=RuleIncidentKind.REPEATED_MISTAKE,
                summary="The control response trusted one unhealthy sensor.",
                evidence_ids=(evidence_id,),
                observed_at=NOW,
                reported_by=self.proposer,
                recorded_at=NOW,
                governing_policy_hash=self.candidate.policy_hash,
            ),
        )
        existing = _rule(
            "thermal-rule-existing-v1",
            "thermal-rule-existing",
            "Quarantine a chamber on any sensor disagreement.",
            ("quarantine the chamber",),
            incidents,
            self.proposer,
            self.candidate.policy_hash,
        )
        proposed = _rule(
            "thermal-rule-proposed-v1",
            "thermal-rule-proposed",
            "Continue a chamber on sensor disagreement.",
            ("continue using the median calibrated sensor",),
            incidents,
            self.proposer,
            self.candidate.policy_hash,
        )
        self._complete(12)
        return incidents, proposed, existing

    def _step_13_reviews(
        self,
        incidents: tuple[RuleIncident, RuleIncident],
        proposed: BehavioralRuleVersion,
    ) -> None:
        assessments = []
        incident_ids = tuple(item.incident_id for item in incidents)
        for role in ReviewerRole:
            reviewer = _human_actor(f"{role.value.lower()}-reviewer")
            conflict = (
                ConflictClassification.TRUE_LOGICAL_CONTRADICTION
                if role is ReviewerRole.CONFLICT
                else None
            )
            assessments.append(
                ReviewerAssessment(
                    assessment_id=f"{role.value.lower()}-assessment",
                    role=role,
                    provenance=_review_provenance(
                        reviewer,
                        role,
                        incident_ids,
                        self.candidate.policy_hash,
                    ),
                    proposal_id="thermal-rule-review",
                    rule_version_ids=(proposed.rule_version_id,),
                    incident_ids=incident_ids,
                    overlap=None,
                    conflict=conflict,
                    findings=(f"{role.value.lower()} review retained",),
                    candidate_statement=proposed.canonical_statement,
                    scope=proposed.scope,
                    triggers=proposed.triggers,
                    exceptions=proposed.exceptions,
                    counterexamples=("sensor A healthy while sensor B fails",),
                    regression_test_ids=("regression-boundary",),
                    recommended_action=RuleAction.ACCEPT_WITH_REVISION,
                    uncertainty=(f"{role.value.lower()} limitation retained",),
                )
            )
        self.assessments = tuple(assessments)
        if {item.role for item in self.assessments} != set(ReviewerRole):
            raise RuntimeError("five-role reviewer import is incomplete")
        self._complete(13)

    def _step_14_consolidate(
        self,
        incidents: tuple[RuleIncident, RuleIncident],
        proposed: BehavioralRuleVersion,
        existing: BehavioralRuleVersion,
    ) -> None:
        overlap = classify_overlap(proposed, existing)
        integrator = _human_actor("rule-integrator")
        incident_ids = tuple(sorted(item.incident_id for item in incidents))
        consolidated = _rule(
            "thermal-rule-canonical-v1",
            "thermal-rule-canonical",
            "On disagreement, isolate unhealthy sensors and use independently calibrated sensors.",
            ("quarantine unhealthy sensors", "continue only with calibrated agreement"),
            incidents,
            integrator,
            self.candidate.policy_hash,
            exceptions=("quarantine the chamber when fewer than two calibrated sensors remain",),
            decision_boundary="continue only when two calibrated sensors agree below 30 C",
            regression_test_ids=(
                "regression-overheat",
                "regression-sensor",
                "regression-boundary",
            ),
        )
        regressions = (
            _regression(
                "regression-case-overheat",
                "regression-overheat",
                (incident_ids[0],),
                consolidated,
                integrator,
            ),
            _regression(
                "regression-case-sensor",
                "regression-sensor",
                (incident_ids[1],),
                consolidated,
                integrator,
            ),
            _regression(
                "regression-case-boundary",
                "regression-boundary",
                incident_ids,
                consolidated,
                integrator,
            ),
        )
        dispositions = tuple(
            RecommendationDisposition(
                assessment_id=item.assessment_id,
                recommended_action=item.recommended_action,
                accepted=True,
                explanation="incorporated into the explicit sensor-health boundary",
            )
            for item in self.assessments
        )
        build_candidate_diff(
            consolidation_decision_id="thermal-consolidation",
            review_proposal_id="thermal-rule-review",
            assessments=self.assessments,
            candidate_rule=consolidated,
            regression_cases=regressions,
            action=RuleAction.ACCEPT_WITH_REVISION,
            recommendation_dispositions=dispositions,
            separating_variable="calibrated sensor count",
            separating_boundary_test_id="regression-boundary",
            recurrence_incident_ids=(),
            recurrence_repairs=(),
            integrator=integrator,
            integrated_at=NOW,
            governing_policy_hash=self.candidate.policy_hash,
            overlap=overlap,
        )
        self.rule = consolidated
        self.regressions = regressions
        self._complete(14)

    def _step_15_regressions(self) -> None:
        incident_ids = {
            incident_id for item in self.regressions for incident_id in item.incident_ids
        }
        if incident_ids != {"incident-overheat", "incident-sensor"}:
            raise RuntimeError("consolidation deleted an incident regression")
        self._complete(15)

    def _step_16_behavior_mapping(self) -> None:
        rule = _required(self.rule)
        behavior_id = "behavior.workspace-integrity"
        source_link = SourceBehaviorLink(
            relative_path="src/super_scientist/application/workspace_integrity.py",
            symbol="verify_workspace",
            behavior_ids=(behavior_id,),
        )
        rule_link = RuleBehaviorLink(
            rule_version_id=rule.rule_version_id,
            behavior_ids=(behavior_id,),
        )
        source_path = Path(__file__).resolve().parents[1] / source_link.relative_path
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        symbols = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if source_link.symbol not in symbols or rule_link.behavior_ids != source_link.behavior_ids:
            raise RuntimeError("rule-to-behavior source mapping did not verify")
        self._complete(16)

    def _step_17_campaign(self) -> HarnessCampaign:
        campaign = _campaign(
            self.candidate.policy_hash,
            self.proposer,
            self.approver,
        )
        if any(not item.comparable for item in _budget_comparisons(campaign)):
            raise RuntimeError("matched-budget campaign is not comparable")
        self._complete(17)
        return campaign

    def _step_18_benchmark_specific(self, campaign: HarnessCampaign) -> None:
        decision = decide_campaign(
            _campaign_report(
                campaign,
                self.approver,
                transfer=Decimal("0.4"),
                admission_requested=False,
            )
        )
        self.first_harness_status = decision.status
        if decision.status is not HarnessDecisionStatus.BENCHMARK_SPECIFIC:
            raise RuntimeError("discovery-only gain was not labeled benchmark-specific")
        self._complete(18)

    def _step_19_admit_transfer(self, campaign: HarnessCampaign) -> None:
        decision = decide_campaign(
            _campaign_report(
                campaign,
                self.approver,
                transfer=Decimal("0.8"),
                admission_requested=True,
            )
        )
        self.second_harness_status = decision.status
        if decision.status is not HarnessDecisionStatus.ADMITTED:
            raise RuntimeError("held-out transferred candidate was not admitted")
        self._complete(19)

    def _step_20_measurement_report(self) -> None:
        transition = _required(self.transition)
        report = canonical_json_bytes(transition.measurement.model_dump(mode="json"))
        reference = self.artifacts.put(report, "application/json")
        if self.artifacts.read(reference) != report:
            raise RuntimeError("measurement report did not round-trip content-addressably")
        self.measurement_report_hash = reference.sha256
        self._complete(20)

    def _step_21_verify(self) -> None:
        with self.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            result = verify_workspace(repositories, self.artifacts)
            policy_versions = tuple(
                snapshot.policy.schema_version for snapshot in repositories.policies.list_all()
            )
        self.audit_valid = result.valid and policy_versions == (1, 2)
        if not self.audit_valid:
            raise RuntimeError(result.reason or "mixed-policy workspace verification failed")
        self._complete(21)


def _v1_policy() -> PolicySnapshot:
    policy = GovernancePolicy(required_claim_checks=("source_exists",))
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _v2_policy() -> PolicySnapshot:
    policy = GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset({"governance_change"}),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.GOVERNANCE_POLICY,
                persistence=PersistenceScope.GOVERNANCE_POLICY,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.CONTROLLED_EXPERIMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=True,
                rollback_required=True,
            ),
        ),
    )
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _governance_transition(
    prior: PolicySnapshot,
    candidate: PolicySnapshot,
) -> ProposeGovernancePolicyTransition:
    proposer = _model_actor("transition-proposer")
    approver = _human_actor("transition-approver")
    evaluator = _model_actor("transition-evaluator")
    auditor = _human_actor("transition-auditor")
    classification = ChangeClassification(
        target=ChangeTarget.GOVERNANCE_POLICY,
        loop_closure=LoopClosure.HUMAN_IN_LOOP,
        persistence=PersistenceScope.GOVERNANCE_POLICY,
        verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        grounding=ExternalGrounding.CONTROLLED_EXPERIMENT,
        signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
    )
    budget = _resource_budget(10)
    run = ResearchRun(
        run_id="transition-run",
        charter="Independently measure the candidate V2 policy",
        scope=("offline constitutional transition",),
        creator=proposer,
        created_at=NOW,
        active_governance_policy_hash=prior.policy_hash,
        model_configuration_version_id=None,
        scaffold_configuration_version_id=None,
        budget_allocation=RunBudgetAllocation(
            execution=budget,
            search=budget,
            evaluation=budget,
            judging=budget,
            human=budget,
        ),
        final_validator=approver,
        final_validator_version="human-v1",
        environment_snapshot_id="offline-transition-environment",
    )
    audit = EvaluatorAuditRecord(
        evaluator_audit_id="transition-audit",
        auditor=auditor,
        auditor_version="auditor-v1",
        auditor_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        evaluator=evaluator,
        evaluator_version="evaluator-v1",
        proposer=proposer,
        candidate_producer=proposer,
        auditor_to_evaluator=ActorRelationship.INDEPENDENT,
        auditor_to_proposer=ActorRelationship.INDEPENDENT,
        auditor_to_candidate_producer=ActorRelationship.INDEPENDENT,
        independence_enforced=True,
        evidence_ids=("transition-protected-evidence",),
        checks_run=("transition-audit-check",),
        assumptions=("policy fixtures are canonical",),
        limitations=("offline deterministic coverage",),
        result=AssessmentOutcome.PASSED,
        audited_at=NOW,
        governing_policy_hash=prior.policy_hash,
    )
    trajectory = (_trajectory_point(0), _trajectory_point(1))
    measurement = SelfImprovementMeasurementRecord(
        measurement_id="transition-measurement",
        change_id="transition-change",
        run_id=run.run_id,
        classification=classification,
        proposer=proposer,
        evaluator=evaluator,
        evaluator_version="evaluator-v1",
        evaluator_tier="protected-external",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        baseline_version_id=prior.policy_hash,
        candidate_version_id=candidate.policy_hash,
        protected_metrics=(_metric("transition-protected", protected=True),),
        countermetrics=(_metric("transition-countermetric", protected=False),),
        expected_final_index=1,
        trajectory=trajectory,
        peak_observation=TrajectoryObservation(step_index=1, metrics=trajectory[1].metrics),
        final_observation=TrajectoryObservation(step_index=1, metrics=trajectory[1].metrics),
        attempted_changes=("transition-admitted", "transition-rejected"),
        admitted_changes=("transition-admitted",),
        rejected_changes=("transition-rejected",),
        regressions=("one retained countermetric regression",),
        rollback_events=("transition-rollback-drill",),
        execution_budget=_resource_budget(10),
        search_budget=_resource_budget(20),
        evaluation_budget=_resource_budget(30),
        judging_budget=_resource_budget(40),
        human_budget=_resource_budget(50),
        usage_by_category=_usage_breakdown(execution=_usage(), search=_usage()),
        usage=_usage(2),
        failures=("one failed candidate retained",),
        rollback_target_id=prior.policy_hash,
        evaluator_audit_id=audit.evaluator_audit_id,
        decision=MeasurementDecision.ACCEPTED,
        decision_authority=approver,
        decided_at=DECIDED_AT,
        governing_policy_hash=prior.policy_hash,
    )
    return ProposeGovernancePolicyTransition(
        proposal_id="02-transition-v1-v2",
        idempotency_key="02-transition-v1-v2-key",
        proposer=proposer,
        approval=Approval(approver=approver, approved_at=NOW),
        research_run=run,
        evaluator_audit=audit,
        measurement=measurement,
        candidate_policy_snapshot=candidate,
        prior_policy_hash=prior.policy_hash,
        rollback_policy_hash=prior.policy_hash,
        classification=classification,
    )


def _trajectory_point(index: int) -> PerformanceTrajectoryPoint:
    candidate = "transition-admitted" if index == 0 else "transition-rejected"
    usage = _usage()
    return PerformanceTrajectoryPoint(
        step_index=index,
        change_id="transition-change",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        metrics=(_metric(f"transition-trajectory-{index}", protected=False),),
        attempted_change_ids=(candidate,),
        admitted_change_ids=(candidate,) if index == 0 else (),
        rejected_change_ids=() if index == 0 else (candidate,),
        regressions=("one retained countermetric regression",) if index == 0 else (),
        rollback_event_ids=() if index == 0 else ("transition-rollback-drill",),
        usage_by_category=_usage_breakdown(
            execution=usage if index == 0 else None,
            search=usage if index == 1 else None,
        ),
        usage=usage,
    )


def _metric(identifier: str, *, protected: bool) -> MetricObservation:
    return MetricObservation(
        metric_id=identifier,
        value=0.5,
        source_id=f"{identifier}-source",
        protected=protected,
        external=True,
    )


def _resource_budget(value: int) -> ResourceBudget:
    return ResourceBudget(
        cost_usd=float(value),
        compute_units=float(value),
        tokens=value,
        elapsed_seconds=float(value),
        tool_calls=value,
        human_interventions=value,
    )


def _run_budget(value: int) -> RunBudgetAllocation:
    budget = _resource_budget(value)
    return RunBudgetAllocation(
        execution=budget,
        search=budget,
        evaluation=budget,
        judging=budget,
        human=budget,
    )


def _usage(multiplier: int = 1) -> ResourceUsage:
    return ResourceUsage(
        cost_usd=float(multiplier),
        compute_units=float(multiplier),
        tokens=multiplier,
        elapsed_seconds=float(multiplier),
        tool_calls=multiplier,
        human_interventions=multiplier,
    )


def _usage_breakdown(
    *,
    execution: ResourceUsage | None = None,
    search: ResourceUsage | None = None,
) -> ResourceUsageBreakdown:
    zero = _usage(0)
    return ResourceUsageBreakdown(
        execution=execution or zero,
        search=search or zero,
        evaluation=zero,
        judging=zero,
        human=zero,
    )


def _review_provenance(
    reviewer: ActorIdentity,
    role: ReviewerRole,
    evidence_ids: tuple[str, ...],
    governing_policy_hash: str,
) -> AssessmentProvenance:
    return AssessmentProvenance(
        actor=reviewer,
        actor_version="human-reviewer-v1",
        category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        deterministic_or_learned="DETERMINISTIC",
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("synthetic incidents are retained",),
        evidence_ids=evidence_ids,
        checks_run=(f"{role.value.lower()}-review",),
        limitations=("one offline thermal scenario",),
        result=AssessmentOutcome.PASSED,
        meaningful_confidence=None,
        assessed_at=NOW,
        governing_policy_hash=governing_policy_hash,
    )


def _rule(
    version_id: str,
    rule_id: str,
    statement: str,
    required_behavior: tuple[str, ...],
    incidents: tuple[RuleIncident, RuleIncident],
    creator: ActorIdentity,
    governing_policy_hash: str,
    *,
    exceptions: tuple[str, ...] = (),
    decision_boundary: str = "sensor disagreement is present",
    regression_test_ids: tuple[str, ...] = ("regression-boundary",),
) -> BehavioralRuleVersion:
    return BehavioralRuleVersion(
        rule_version_id=version_id,
        rule_id=rule_id,
        semantic_version="1.0.0",
        title="Thermal sensor disagreement boundary",
        canonical_statement=statement,
        rationale="Retain both synthetic equipment incidents.",
        authority=RuleAuthority.PROJECT,
        scope=("synthetic thermal chambers",),
        triggers=("sensor disagreement",),
        required_behavior=required_behavior,
        prohibited_behavior=("trust a lone unhealthy sensor",),
        exceptions=exceptions,
        decision_boundary=decision_boundary,
        precedence_rule_ids=(),
        source_incident_ids=tuple(sorted(item.incident_id for item in incidents)),
        evidence_ids=tuple(sorted({item.evidence_ids[0] for item in incidents})),
        counterexamples=("two independently calibrated sensors disagree",),
        regression_test_ids=regression_test_ids,
        retrieval_terms=("thermal", "sensor", "disagreement"),
        aliases=(),
        related_rule_ids=(),
        conflict_rule_ids=(),
        supersedes_rule_version_ids=(),
        status=RuleStatus.UNDER_REVIEW,
        creator=creator,
        approver=None,
        created_at=NOW,
        approved_at=None,
        governing_policy_hash=governing_policy_hash,
    )


def _regression(
    regression_case_id: str,
    test_id: str,
    incident_ids: tuple[str, ...],
    rule: BehavioralRuleVersion,
    creator: ActorIdentity,
) -> RuleRegressionCase:
    return RuleRegressionCase(
        regression_case_id=regression_case_id,
        rule_version_id=rule.rule_version_id,
        incident_ids=incident_ids,
        test_id=test_id,
        scenario="Synthetic sensor disagreement at the thermal boundary.",
        expected_behavior="Use only independently calibrated agreement or quarantine.",
        created_by=creator,
        created_at=NOW,
        governing_policy_hash=rule.governing_policy_hash,
    )


def _evaluation_budget() -> EvaluationBudget:
    return EvaluationBudget(
        model_id="deterministic-fixture",
        model_version="v1",
        adapter_id=None,
        feedback_mode=FeedbackMode.NONE,
        tool_ids=(),
        attempts=1,
        token_limit=100,
        reasoning_limit=50,
        evaluator_call_limit=1,
        wall_clock_seconds=Decimal("10"),
        cost_limit=Decimal("1"),
        human_intervention_limit=0,
    )


def _campaign(
    governing_policy_hash: str,
    producer: ActorIdentity,
    authority: ActorIdentity,
) -> HarnessCampaign:
    variants = (
        HarnessVariant.UNCHANGED_HARNESS_SINGLE_ATTEMPT,
        HarnessVariant.EVOLVED_HARNESS,
    )
    partitions = tuple(
        CampaignPartitionManifest(
            partition_manifest_id=f"manifest-{partition.value.lower()}",
            campaign_id="thermal-campaign",
            campaign_version=1,
            partition=partition,
            task_ids=(f"{partition.value.lower()}-task",),
            manifest_hash=partition_manifest_hash(
                campaign_id="thermal-campaign",
                campaign_version=1,
                partition=partition,
                task_ids=(f"{partition.value.lower()}-task",),
            ),
            protected_content_hash=(
                None
                if partition is HarnessPartition.HARNESS_DISCOVERY_TASKS
                else sha256_hex(partition.value.encode("utf-8"))
            ),
            created_at=NOW,
            governing_policy_hash=governing_policy_hash,
        )
        for partition in HarnessPartition
    )
    budget = _evaluation_budget()
    return HarnessCampaign(
        campaign_id="thermal-campaign",
        version=1,
        variants=variants,
        baseline_variant=variants[0],
        candidate_variant=variants[1],
        baseline_harness_version_id="harness-v1",
        candidate_harness_version_id="harness-v2",
        rollback_harness_version_id="harness-v1",
        model_id=budget.model_id,
        model_version=budget.model_version,
        adapter_id=None,
        evaluator=_model_actor("harness-evaluator"),
        evaluator_version_id="harness-evaluator-v1",
        candidate_producer=producer,
        coordinator=authority,
        partitions=partitions,
        budgets=(
            VariantEvaluationBudget(
                budget_id="baseline-budget",
                variant=variants[0],
                budget=budget,
            ),
            VariantEvaluationBudget(
                budget_id="candidate-budget",
                variant=variants[1],
                budget=budget,
            ),
        ),
        created_at=NOW,
        governing_policy_hash=governing_policy_hash,
    )


def _budget_comparisons(campaign: HarnessCampaign) -> tuple[object, ...]:
    baseline = campaign.budgets[0].budget
    return tuple(compare_evaluation_budgets(baseline, item.budget) for item in campaign.budgets[1:])


def _campaign_report(
    campaign: HarnessCampaign,
    authority: ActorIdentity,
    *,
    transfer: Decimal,
    admission_requested: bool,
) -> HarnessCampaignReport:
    metrics = tuple(
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
                    in {
                        HarnessPartition.HARNESS_REGRESSION_TASKS,
                        HarnessPartition.HARNESS_SAFETY_TASKS,
                    }
                    else Decimal("0.8")
                )
            ),
            higher_is_better=True,
            catastrophic_regression=False,
            result_ids=(f"result-{partition.value.lower()}",),
            evaluator_version_id=campaign.evaluator_version_id,
        )
        for partition in HarnessPartition
    )
    return HarnessCampaignReport(
        campaign=campaign,
        expected_iteration_count=0,
        iterations=(),
        negative_observation_ids=(),
        budget_comparisons=_budget_comparisons(campaign),
        metrics=metrics,
        confounds=(),
        evaluator_audit_id="harness-audit",
        evaluator_audit_passed=True,
        measurement_id="harness-measurement",
        measurement_accepted=True,
        rollback=None,
        admission_requested=admission_requested,
        decision_authority=authority,
        reported_at=NOW,
        governing_policy_hash=campaign.governing_policy_hash,
    )


def _human_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity(actor_id=identifier, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity.model(identifier, identifier, identifier, None, NOW)


def _required[ValueT](value: ValueT | None) -> ValueT:
    if value is None:
        raise RuntimeError("vertical-slice step dependency is unavailable")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    arguments = parser.parse_args()
    result = VerticalSlice(arguments.workspace).run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
