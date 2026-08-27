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
from super_scientist.application.hypothesis_testing.service import (
    FIXED_HYPOTHESIS_CLASSIFICATION,
    HypothesisTestingService,
)
from super_scientist.application.hypothesis_testing.simulators import SimulatorRegistry
from super_scientist.application.rules.service import FIXED_RULE_CLASSIFICATION
from super_scientist.application.trails.service import FIXED_TRAIL_CLASSIFICATION
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
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import EvidenceRecord, EvidenceSpan, VerificationState
from super_scientist.domain.evidence_trails.authority import (
    TRUSTED_TRAIL_CHECKER_ID,
    TRUSTED_TRAIL_CHECKER_VERSION,
    build_source_first_provenance,
    canonical_node_set_hash,
    required_assessment_scope,
    trusted_assessment_id,
    trusted_check_id,
)
from super_scientist.domain.evidence_trails.models import (
    AddEvidenceReceiptRef,
    AssessmentCategory,
    ClaimModality,
    ConstructionMethod,
    EvidenceTrailNode,
    EvidenceTrailNodeStageReceiptRef,
    EvidenceTrailRelation,
    EvidenceTrailRelationStageReceiptRef,
    EvidenceTrailVersion,
    ExactSourceSpan,
    ProposeClaimReceiptRef,
    RelationType,
    StructuralLocation,
    StructuralLocationKind,
    TrailAssessment,
    TrailCheckCategory,
    TrailCheckResult,
    TrailGeometry,
    TrailNodeRole,
    TrailOrderingConstraint,
    TrailOutcome,
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
    HarnessDecisionStatus,
    HarnessPartition,
    HarnessVariant,
    PartitionMetric,
    VariantEvaluationBudget,
    compare_evaluation_budgets,
    fixed_checker_configuration_hash,
    partition_manifest_hash,
)
from super_scientist.domain.hypotheses.models import (
    AdmissionOutcome,
    CounterexampleReceiptRef,
    CounterexampleRecord,
    DeterministicCheckerSpec,
    DeterministicCheckResult,
    EvaluatorAuditReceiptRef,
    ExecutableModelSpec,
    ExecutionMode,
    HypothesisAdmissionDecision,
    HypothesisSpec,
    HypothesisVersionReceiptRef,
    ImportedPatternStatus,
    ModelInput,
    ModelSpecReceiptRef,
    ModelType,
    NumericField,
    RevisionRecord,
    SelfImprovementMeasurementReceiptRef,
    SimulationResultReceiptRef,
    VerificationMechanismReceiptRef,
    VerificationOutcome,
    VerificationResultReceiptRef,
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
    BudgetAllocation,
    BudgetReserves,
    BudgetUsage,
    CompletionChecklistItem,
    CompletionChecklistStep,
    CompletionDecision,
    CompletionProposal,
    ExecutionTelemetry,
    ProgressPlan,
    ProgressStatus,
    ProgressSubtask,
    ProgressValidationEvent,
    TerminationReason,
)
from super_scientist.domain.research_runs.models import (
    ResearchRun,
    ResearchRunEvent,
    ResearchRunEventType,
    RunBudgetAllocation,
)
from super_scientist.handbook.models import RuleBehaviorLink, SourceBehaviorLink
from super_scientist.kernel.audit.models import json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AdmitHypothesis,
    AppendProgressEvent,
    AppendResearchRunEvent,
    Approval,
    ConsolidateBehavioralRule,
    CreateHarnessCampaign,
    CreateResearchRun,
    DecideCompletion,
    DecideHarnessCampaign,
    ImportReviewerAssessment,
    ProposeBehavioralRule,
    ProposeClaim,
    ProposeEvidenceTrailNodes,
    ProposeEvidenceTrailRelations,
    ProposeGovernancePolicyTransition,
    ProposeHypothesisVersion,
    RecordCounterexample,
    RecordEvaluatorAudit,
    RecordEvidenceTrailVersion,
    RecordHarnessIteration,
    RecordHarnessProtectedResult,
    RecordProgressPlan,
    RecordRuleIncident,
    RecordRunBudget,
    RecordSelfImprovementMeasurement,
    RecordSimulationResult,
    RecordVerificationResult,
    RegisterExecutableModel,
    RegisterVerificationMechanism,
    RejectionCode,
    ReviseHypothesis,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.protected_evaluation import (
    MetricValue,
    ProtectedCheckerResult,
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
        database_url = f"sqlite:///{(workspace / 'scientist-harness.db').as_posix()}"
        upgrade_database(database_url)
        self.engine = create_database_engine(database_url)
        self.artifacts = FileArtifactStore(workspace / "artifacts")
        self.clock = DeterministicClock()
        self.steps: list[Step] = []
        self.prior = _v1_policy()
        self.candidate = _v2_policy()
        self.coordinator = TransactionCoordinator(
            self.uow_factory,
            self.candidate,
            self.clock,
            self.artifacts,
        )
        self.proposer = _model_actor("thermal-scientist")
        self.approver = _human_actor("governance-approver")
        self.validator = _human_actor("thermal-validator")
        self.evidence: EvidenceRecord | None = None
        self.hypothesis_evidence: EvidenceRecord | None = None
        self.transition: ProposeGovernancePolicyTransition | None = None
        self.research_run: ResearchRun | None = None
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
        self.failed_hypothesis_preserved = False
        self.policy_versions: tuple[int, ...] = ()
        self.audit_valid = False

    def uow_factory(self) -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(self.engine)

    def _submit(self, proposal: object) -> None:
        decision = self.coordinator.submit(proposal)
        if not decision.accepted:
            raise RuntimeError(f"durable proposal was rejected: {decision}")

    def _submit_batch(self, proposals: tuple[object, ...]) -> None:
        decisions = self.coordinator.submit_batch(proposals)
        rejected = tuple(decision for decision in decisions if not decision.accepted)
        if rejected:
            raise RuntimeError(f"durable proposal batch was rejected: {rejected[0]}")

    def _approval(self, identifier: str) -> Approval:
        return Approval(
            approver=_human_actor(identifier),
            approved_at=self.clock.now(),
        )

    def _receipt[ReceiptT](
        self,
        proposal_id: str,
        receipt_type: type[ReceiptT],
    ) -> ReceiptT:
        with self.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            transaction = repositories.transactions.get_by_proposal_id(proposal_id)
            if transaction is None or not transaction.decision.accepted:
                raise RuntimeError(f"accepted transaction {proposal_id!r} is unavailable")
            events = tuple(
                event
                for event in repositories.audit.list_all()
                if json_compatible_payload(event.payload).get("proposal", {}).get("proposal_id")
                == proposal_id
            )
        if len(events) != 1:
            raise RuntimeError(f"transaction {proposal_id!r} has no exact audit receipt")
        return receipt_type(
            proposal_id=proposal_id,
            proposal_hash=transaction.proposal_hash,
            audit_event_id=events[0].event_id,
            audit_event_hash=events[0].event_hash,
        )

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
                "policy_versions": list(self.policy_versions),
                "false_finish_rejected": self.false_finish_rejected,
                "failed_hypothesis_preserved": self.failed_hypothesis_preserved,
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
            extracted_span=EvidenceSpan(
                start=0,
                end=len(content.decode("utf-8")),
                text=content.decode("utf-8"),
            ),
            structured_observation={
                "source_structure": {
                    "schema_version": 1,
                    "locations": tuple(
                        {
                            "kind": StructuralLocationKind.EVENT_SEQUENCE.value,
                            "locator": f"event-{position}",
                            "start": content.decode("utf-8").index(text),
                            "end": content.decode("utf-8").index(text) + len(text),
                        }
                        for text, position in (
                            ("10 chamber peaked at 28.4 C.", 10),
                            (
                                "20 sensor A drifted while sensor B remained calibrated.",
                                20,
                            ),
                        )
                    ),
                }
            },
            provenance={
                "collector": "governed-adaptation-example",
                "external_grounding": ExternalGrounding.PRIMARY_SOURCE.value,
            },
            license="SSOH synthetic fixture",
            ingestion_actor_id=self.proposer.actor_id,
        )
        coordinator = self.coordinator
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
        hypothesis_content = b"controlled thermal simulation observation"
        hypothesis_artifact = self.artifacts.put(hypothesis_content, "text/plain")
        hypothesis_evidence = EvidenceRecord(
            evidence_id="thermal-controlled-observation",
            evidence_type="controlled-experiment-observation",
            source_locator="fixture://ssoh/thermal-controlled-observation",
            retrieved_at=NOW,
            artifact=hypothesis_artifact,
            provenance={
                "collector": "governed-adaptation-example",
                "external_grounding": ExternalGrounding.CONTROLLED_EXPERIMENT.value,
            },
            ingestion_actor_id=self.proposer.actor_id,
            verification_state=VerificationState.UNVERIFIED,
        )
        decision = coordinator.submit(
            AddEvidence(
                proposal_id="03-add-controlled-observation",
                idempotency_key="03-add-controlled-observation-key",
                proposer=self.proposer,
                evidence=hypothesis_evidence,
            )
        )
        if not decision.accepted:
            raise RuntimeError(f"controlled observation admission failed: {decision}")
        with self.uow_factory() as unit_of_work:
            stored_hypothesis_evidence = unit_of_work.repositories().evidence.get(
                hypothesis_evidence.evidence_id
            )
        if stored_hypothesis_evidence is None:
            raise RuntimeError("accepted controlled observation was not projected")
        self.hypothesis_evidence = stored_hypothesis_evidence
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
        coordinator = self.coordinator
        for proposal in (
            CreateResearchRun(
                proposal_id="04-create-thermal-run",
                idempotency_key="04-create-thermal-run-key",
                proposer=self.proposer,
                approval=Approval(approver=self.approver, approved_at=NOW),
                run=run,
            ),
            RecordProgressPlan(
                proposal_id="04-record-thermal-plan",
                idempotency_key="04-record-thermal-plan-key",
                proposer=self.proposer,
                approval=Approval(approver=self.approver, approved_at=NOW),
                plan=self.progress_plan,
            ),
        ):
            decision = coordinator.submit(proposal)
            if not decision.accepted:
                raise RuntimeError(f"research workflow admission failed: {decision}")
        self._submit(
            AppendResearchRunEvent(
                proposal_id="04-start-thermal-run",
                idempotency_key="04-start-thermal-run-key",
                proposer=self.proposer,
                approval=self._approval("research-run-event-approver"),
                event=ResearchRunEvent(
                    run_event_id="thermal-run-started",
                    run_id=run.run_id,
                    sequence=1,
                    event_type=ResearchRunEventType.STARTED,
                    actor=self.proposer,
                    detail="The governed thermal investigation started.",
                    final_validation=None,
                    occurred_at=self.clock.now(),
                    governing_policy_hash=self.candidate.policy_hash,
                ),
            )
        )
        self.research_run = run
        self._complete(4)

    def _step_5_hypotheses(self) -> None:
        evidence = _required(self.hypothesis_evidence)
        common = {
            "scope": ("bounded synthetic thermal chamber",),
            "variables": ("temperature", "heater_delta", "cooling_rate"),
            "primitive_version_ids": (),
            "evidence_ids": (evidence.evidence_id,),
            "proposer": self.proposer,
            "created_at": self.clock.now(),
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
            imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
            **common,
        )
        self.hypotheses = (bounded, failed)
        for index, hypothesis in enumerate(self.hypotheses, start=1):
            self._submit(
                ProposeHypothesisVersion(
                    proposal_id=f"05-propose-{hypothesis.hypothesis_version_id}",
                    idempotency_key=f"05-propose-{hypothesis.hypothesis_version_id}-key",
                    proposer=self.proposer,
                    approval=self._approval(f"hypothesis-stage-approver-{index}"),
                    classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                    hypothesis=hypothesis,
                )
            )
        self._complete(5)

    def _step_6_simulator(self) -> None:
        _, failed = _required(self.hypotheses)
        registrar = _model_actor("thermal-model-registrar")
        model = ExecutableModelSpec(
            model_spec_id="thermal-model-v1",
            hypothesis_version_id=failed.hypothesis_version_id,
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
            registered_by=registrar,
            created_at=self.clock.now(),
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
        hypothesis_receipt = self._receipt(
            f"05-propose-{failed.hypothesis_version_id}",
            HypothesisVersionReceiptRef,
        )
        self._submit(
            RegisterExecutableModel(
                proposal_id="06-register-thermal-model",
                idempotency_key="06-register-thermal-model-key",
                proposer=registrar,
                approval=self._approval("model-stage-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=hypothesis_receipt,
                model_spec=model,
            )
        )
        checker = _model_actor("thermal-hypothesis-checker")
        mechanism = DeterministicCheckerSpec(
            mechanism_type="DETERMINISTIC_CHECKER",
            mechanism_spec_id="thermal-runaway-checker",
            hypothesis_version_id=failed.hypothesis_version_id,
            name="thermal runaway boundary checker",
            description="Checks the registered below-40-C falsification boundary.",
            specification_hash=sha256_hex(b"thermal-runaway-boundary-checker"),
            input_schema_id=model.output_schema_id,
            output_schema_id="thermal-verification-result-v1",
            created_by=checker,
            created_at=self.clock.now(),
            governing_policy_hash=self.candidate.policy_hash,
            checked_invariants=("peak-below-40-c",),
        )
        self._submit(
            RegisterVerificationMechanism(
                proposal_id="06-register-runaway-checker",
                idempotency_key="06-register-runaway-checker-key",
                proposer=checker,
                approval=self._approval("verification-mechanism-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=hypothesis_receipt,
                mechanism_spec=mechanism,
            )
        )
        self.model = model
        self.mechanism = mechanism
        self.model_input = model_input
        self._complete(6)

    def _step_7_predictions(self) -> None:
        bounded, failed = _required(self.hypotheses)
        service = HypothesisTestingService(
            self.coordinator,
            SimulatorRegistry(),
        )
        simulation = service.simulate(
            self.model,
            self.model_input,
            simulation_result_id="thermal-simulation-v1",
            output_id="thermal-output-v1",
            governing_policy_hash=self.candidate.policy_hash,
            completed_at=self.clock.now(),
        )
        self._submit(
            RecordSimulationResult(
                proposal_id="07-record-thermal-simulation",
                idempotency_key="07-record-thermal-simulation-key",
                proposer=self.model.registered_by,
                approval=self._approval("simulation-stage-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=self._receipt(
                    f"05-propose-{failed.hypothesis_version_id}",
                    HypothesisVersionReceiptRef,
                ),
                model_receipt=self._receipt(
                    "06-register-thermal-model",
                    ModelSpecReceiptRef,
                ),
                simulation_result=simulation,
            )
        )
        self.simulation = simulation
        self.model_output = simulation.model_output
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
        trail_nodes = tuple(nodes)
        builder = _model_actor("thermal-trail-builder")
        trail_approver = _human_actor("thermal-trail-approver")
        claim_author = _human_actor("thermal-trail-claim-author")
        claim = AtomicClaim(
            claim_id="thermal-trail-claim",
            version=1,
            proposition="A drifting sensor, rather than runaway heating, caused the high reading.",
            scope="synthetic thermal incident",
            population_or_system="synthetic thermal chamber",
            epistemic_modality=ClaimModality.ASSERTED.value,
            status=ClaimStatus.PROPOSED,
            created_at=self.clock.now(),
            created_by=claim_author.actor_id,
        )
        source_receipt = self._receipt(
            "03-add-thermal-evidence",
            AddEvidenceReceiptRef,
        )
        self._submit(
            ProposeEvidenceTrailNodes(
                proposal_id="08-propose-trail-nodes",
                idempotency_key="08-propose-trail-nodes-key",
                proposer=builder,
                approval=Approval(approver=trail_approver, approved_at=self.clock.now()),
                trail_id="thermal-trail",
                trail_version_id="thermal-trail-v1",
                classification=FIXED_TRAIL_CLASSIFICATION,
                source_receipts=(source_receipt,),
                nodes=trail_nodes,
            )
        )
        relations = (
            EvidenceTrailRelation(
                relation_id="thermal-trail-support",
                trail_version_id="thermal-trail-v1",
                source_node_id=trail_nodes[1].node_id,
                target_node_id=trail_nodes[0].node_id,
                relation_type=RelationType.SUPPORTS,
                evidence_ids=(evidence.evidence_id,),
                modality=ClaimModality.ASSERTED,
            ),
            EvidenceTrailRelation(
                relation_id="thermal-trail-order",
                trail_version_id="thermal-trail-v1",
                source_node_id=trail_nodes[0].node_id,
                target_node_id=trail_nodes[1].node_id,
                relation_type=RelationType.PRECEDES,
                evidence_ids=(evidence.evidence_id,),
                modality=ClaimModality.ASSERTED,
            ),
        )
        self._submit(
            ProposeEvidenceTrailRelations(
                proposal_id="08-propose-trail-relations",
                idempotency_key="08-propose-trail-relations-key",
                proposer=builder,
                approval=Approval(approver=trail_approver, approved_at=self.clock.now()),
                trail_id="thermal-trail",
                trail_version_id="thermal-trail-v1",
                classification=FIXED_TRAIL_CLASSIFICATION,
                node_stage_receipt=self._receipt(
                    "08-propose-trail-nodes",
                    EvidenceTrailNodeStageReceiptRef,
                ),
                node_ids=tuple(node.node_id for node in trail_nodes),
                nodes_hash=canonical_node_set_hash(trail_nodes),
                relations=relations,
            )
        )
        claim = claim.model_copy(update={"created_at": self.clock.now()})
        self._submit(
            ProposeClaim(
                proposal_id="08-propose-trail-claim",
                idempotency_key="08-propose-trail-claim-key",
                proposer=claim_author,
                claim=claim,
            )
        )
        check_ids = tuple(
            trusted_check_id("thermal-trail-v1", category) for category in TrailCheckCategory
        )
        checked_at = self.clock.now()
        checks = tuple(
            TrailCheckResult(
                check_id=check_id,
                trail_version_id="thermal-trail-v1",
                claim_version_id="thermal-trail-claim:1",
                governing_policy_hash=self.candidate.policy_hash,
                category=category,
                passed=True,
                finding_codes=(),
                node_ids=tuple(node.node_id for node in trail_nodes),
                relation_ids=tuple(relation.relation_id for relation in relations),
                evidence_ids=(evidence.evidence_id,),
                checker_id=TRUSTED_TRAIL_CHECKER_ID,
                checker_version=TRUSTED_TRAIL_CHECKER_VERSION,
                checked_at=checked_at,
            )
            for category, check_id in zip(TrailCheckCategory, check_ids, strict=True)
        )
        assessed_at = self.clock.now()
        assessments = tuple(
            TrailAssessment(
                assessment_id=trusted_assessment_id("thermal-trail-v1", category),
                trail_version_id="thermal-trail-v1",
                claim_version_id="thermal-trail-claim:1",
                governing_policy_hash=self.candidate.policy_hash,
                category=category,
                provenance=AssessmentProvenance(
                    actor=_model_actor(f"thermal-trail-assessor-{index}"),
                    actor_version="thermal-trail-assessor-v1",
                    category=VerificationLevel.INDEPENDENT_LEARNED_JUDGE,
                    deterministic_or_learned="LEARNED",
                    proposer_relationship=ActorRelationship.INDEPENDENT,
                    assumptions=("retained synthetic source bytes are authoritative",),
                    evidence_ids=required_assessment_scope(
                        category,
                        trail_nodes,
                        relations,
                    ).evidence_ids,
                    checks_run=check_ids,
                    limitations=("one bounded offline incident",),
                    result=AssessmentOutcome.PASSED,
                    meaningful_confidence=0.95,
                    assessed_at=assessed_at,
                    governing_policy_hash=self.candidate.policy_hash,
                ),
                node_ids=required_assessment_scope(
                    category,
                    trail_nodes,
                    relations,
                ).node_ids,
                relation_ids=required_assessment_scope(
                    category,
                    trail_nodes,
                    relations,
                ).relation_ids,
                evidence_ids=required_assessment_scope(
                    category,
                    trail_nodes,
                    relations,
                ).evidence_ids,
                finding_codes=(),
            )
            for index, category in enumerate(AssessmentCategory)
        )
        source_first = build_source_first_provenance(
            source_receipts=(source_receipt,),
            node_stage_receipt=self._receipt(
                "08-propose-trail-nodes",
                EvidenceTrailNodeStageReceiptRef,
            ),
            relation_stage_receipt=self._receipt(
                "08-propose-trail-relations",
                EvidenceTrailRelationStageReceiptRef,
            ),
            claim_stage_receipt=self._receipt(
                "08-propose-trail-claim",
                ProposeClaimReceiptRef,
            ),
        )
        version = EvidenceTrailVersion(
            trail_version_id="thermal-trail-v1",
            trail_id="thermal-trail",
            claim_version_id="thermal-trail-claim:1",
            version=1,
            source_ids=("thermal-incident-note",),
            required_node_ids=(trail_nodes[0].node_id,),
            supporting_node_ids=(trail_nodes[1].node_id,),
            opposing_node_ids=(),
            redundant_node_ids=(),
            ordering_constraints=(
                TrailOrderingConstraint(
                    constraint_id="thermal-trail-ordering",
                    before_node_id=trail_nodes[0].node_id,
                    after_node_id=trail_nodes[1].node_id,
                ),
            ),
            geometry=TrailGeometry.LINEAR,
            status=TrailOutcome.SUFFICIENT,
            construction_method=ConstructionMethod.SOURCE_FIRST,
            source_first_provenance=source_first,
            check_ids=check_ids,
            assessment_ids=tuple(item.assessment_id for item in assessments),
            constructed_by=builder,
            created_at=self.clock.now(),
            governing_policy_hash=self.candidate.policy_hash,
        )
        self._submit(
            RecordEvidenceTrailVersion(
                proposal_id="08-record-thermal-trail",
                idempotency_key="08-record-thermal-trail-key",
                proposer=builder,
                approval=Approval(approver=trail_approver, approved_at=self.clock.now()),
                trail_version=version,
                nodes=trail_nodes,
                relations=relations,
                checks=checks,
                assessments=assessments,
            )
        )
        self.trail_nodes = trail_nodes
        self.trail_version = version
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
                occurred_at=self.clock.now(),
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
                occurred_at=self.clock.now(),
                governing_policy_hash=self.candidate.policy_hash,
            ),
        )
        summary = calculate_progress(plan, self.progress_events)
        if summary.official_weight != Decimal("0.5") or summary.provisional_weight != Decimal(
            "0.5"
        ):
            raise RuntimeError("progress separation changed")
        for index, event in enumerate(self.progress_events, start=1):
            self._submit(
                AppendProgressEvent(
                    proposal_id=f"09-append-progress-event-{index}",
                    idempotency_key=f"09-append-progress-event-{index}-key",
                    proposer=event.completion_proposer,
                    approval=self._approval(f"progress-event-approver-{index}"),
                    event=event,
                )
            )
        reserve = _resource_budget(20)
        zero_usage = _usage(0)
        self._submit(
            RecordRunBudget(
                proposal_id="09-record-run-budget",
                idempotency_key="09-record-run-budget-key",
                proposer=self.proposer,
                approval=self._approval("run-budget-approver"),
                budget=BudgetAllocation(
                    budget_id="thermal-progress-budget",
                    run_id=plan.run_id,
                    plan_version_id=plan.plan_version_id,
                    reserves=BudgetReserves(
                        exploration=reserve,
                        implementation=reserve,
                        verification=reserve,
                        recovery=reserve,
                        finalization=reserve,
                    ),
                    usage=BudgetUsage(
                        exploration=zero_usage,
                        implementation=zero_usage,
                        verification=zero_usage,
                        recovery=zero_usage,
                        finalization=zero_usage,
                    ),
                    telemetry=ExecutionTelemetry(
                        episodes=1,
                        model_calls=1,
                        input_tokens=1,
                        output_tokens=1,
                        tool_calls=1,
                        operations=1,
                        files_changed=0,
                        elapsed_seconds=1.0,
                        verification_seconds=1.0,
                        repeated_actions=0,
                        reverted_actions=0,
                        checkpoints=0,
                        timed_out=False,
                        termination_reason=None,
                        estimated_cost_usd=1.0,
                    ),
                    recorded_at=self.clock.now(),
                    governing_policy_hash=self.candidate.policy_hash,
                ),
            )
        )
        self.progress_summary = summary
        self._complete(9)

    def _step_10_false_finish(self) -> None:
        plan = _required(self.progress_plan)
        finding = detect_false_finish(
            voluntary_termination=True,
            claims_completion=True,
            final_validator_result=AssessmentOutcome.FAILED,
            plan=plan,
            events=self.progress_events,
            unused_budget=True,
        )
        evidence_id = _required(self.evidence).evidence_id
        checklist = tuple(
            CompletionChecklistItem(
                step=step,
                completed=True,
                detail=f"Attempted {step.value}",
                evidence_ids=(evidence_id,),
            )
            for step in CompletionChecklistStep
        )
        final_validation = AssessmentProvenance(
            actor=self.validator,
            actor_version="thermal-validator-v1",
            category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            deterministic_or_learned="DETERMINISTIC",
            proposer_relationship=ActorRelationship.INDEPENDENT,
            assumptions=("retained source bytes are authoritative",),
            evidence_ids=(evidence_id,),
            checks_run=("final-thermal-check",),
            limitations=("held-out transfer remains incomplete",),
            result=AssessmentOutcome.FAILED,
            meaningful_confidence=None,
            assessed_at=self.clock.now(),
            governing_policy_hash=self.candidate.policy_hash,
        )
        completion = CompletionProposal(
            completion_proposal_id="thermal-false-finish-proposal",
            run_id=plan.run_id,
            plan_version_id=plan.plan_version_id,
            proposer=self.proposer,
            voluntary_termination=True,
            claims_completion=True,
            termination_reason=TerminationReason.SUCCESS,
            checklist=checklist,
            final_validation=final_validation,
            relationship_to_run_creator=ActorRelationship.INDEPENDENT,
            relationship_to_completion_proposer=ActorRelationship.INDEPENDENT,
            are_independent=True,
            submitted_at=self.clock.now(),
            governing_policy_hash=self.candidate.policy_hash,
        )
        decision_record = CompletionDecision(
            completion_decision_id="thermal-false-finish-decision",
            run_id=plan.run_id,
            plan_version_id=plan.plan_version_id,
            completion_proposal_id=completion.completion_proposal_id,
            decision_authority=self.validator,
            accepted=False,
            checklist=checklist,
            final_validator_result=AssessmentOutcome.FAILED,
            false_finish=finding,
            termination_reason=TerminationReason.SUCCESS,
            decided_at=self.clock.now(),
            governing_policy_hash=self.candidate.policy_hash,
        )
        transaction = DecideCompletion(
            proposal_id="10-reject-false-finish",
            idempotency_key="10-reject-false-finish-key",
            proposer=self.proposer,
            approval=self._approval("completion-stage-approver"),
            completion_proposal=completion,
            completion_decision=decision_record,
        )
        durable_decision = self.coordinator.submit(transaction)
        self.false_finish_rejected = not durable_decision.accepted and any(
            reason.code is RejectionCode.FALSE_FINISH for reason in durable_decision.reasons
        )
        if not self.false_finish_rejected:
            raise RuntimeError("false finish was not rejected")
        self._complete(10)

    def _admit_hypothesis_predecessor(
        self,
        failed: HypothesisSpec,
        hypothesis_receipt: HypothesisVersionReceiptRef,
    ) -> None:
        checker = self.mechanism.created_by
        passing = DeterministicCheckResult(
            mechanism_type="DETERMINISTIC_CHECKER",
            verification_result_id="thermal-runaway-initial-check",
            hypothesis_version_id=failed.hypothesis_version_id,
            mechanism_spec_id=self.mechanism.mechanism_spec_id,
            model_spec_id=self.model.model_spec_id,
            simulation_result_ids=(self.simulation.simulation_result_id,),
            outcome=VerificationOutcome.PASS,
            findings=("The registered bounded run and schemas passed the initial gate.",),
            provenance=AssessmentProvenance(
                actor=checker,
                actor_version="thermal-hypothesis-checker-v1",
                category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                deterministic_or_learned="DETERMINISTIC",
                proposer_relationship=ActorRelationship.INDEPENDENT,
                assumptions=("the initial bounded search domain is complete",),
                evidence_ids=(_required(self.hypothesis_evidence).evidence_id,),
                checks_run=(self.mechanism.mechanism_spec_id,),
                limitations=("a later counterexample search can overturn this result",),
                result=AssessmentOutcome.PASSED,
                meaningful_confidence=None,
                assessed_at=self.clock.now(),
                governing_policy_hash=self.candidate.policy_hash,
            ),
            counterexample_search_performed=True,
            counterexample_found=False,
            checked_invariants=self.mechanism.checked_invariants,
        )
        self._submit(
            RecordVerificationResult(
                proposal_id="11-record-initial-runaway-check",
                idempotency_key="11-record-initial-runaway-check-key",
                proposer=checker,
                approval=self._approval("initial-verification-result-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=hypothesis_receipt,
                mechanism_receipt=self._receipt(
                    "06-register-runaway-checker",
                    VerificationMechanismReceiptRef,
                ),
                model_receipt=self._receipt(
                    "06-register-thermal-model",
                    ModelSpecReceiptRef,
                ),
                simulation_receipts=(
                    self._receipt(
                        "07-record-thermal-simulation",
                        SimulationResultReceiptRef,
                    ),
                ),
                verification_result=passing,
            )
        )
        evaluator = _model_actor("thermal-hypothesis-evaluator")
        auditor = _human_actor("thermal-hypothesis-auditor")
        admission_approver = _human_actor("thermal-hypothesis-admission-authority")
        transition = _required(self.transition)
        audit = transition.evaluator_audit.model_copy(
            update={
                "evaluator_audit_id": "thermal-hypothesis-audit",
                "auditor": auditor,
                "auditor_version": "thermal-hypothesis-auditor-v1",
                "evaluator": evaluator,
                "evaluator_version": "thermal-hypothesis-evaluator-v1",
                "proposer": self.proposer,
                "candidate_producer": self.proposer,
                "evidence_ids": (_required(self.hypothesis_evidence).evidence_id,),
                "checks_run": (passing.verification_result_id,),
                "audited_at": self.clock.now(),
                "governing_policy_hash": self.candidate.policy_hash,
            }
        )
        self._submit(
            RecordEvaluatorAudit(
                proposal_id="11-record-hypothesis-audit",
                idempotency_key="11-record-hypothesis-audit-key",
                proposer=auditor,
                approval=self._approval("hypothesis-audit-approver"),
                evaluator_audit=audit,
            )
        )
        evidence_id = _required(self.hypothesis_evidence).evidence_id
        measurement = transition.measurement.model_copy(
            update={
                "measurement_id": "thermal-hypothesis-measurement",
                "run_id": _required(self.research_run).run_id,
                "classification": FIXED_HYPOTHESIS_CLASSIFICATION,
                "proposer": self.proposer,
                "evaluator": evaluator,
                "evaluator_version": audit.evaluator_version,
                "baseline_version_id": failed.hypothesis_version_id,
                "candidate_version_id": failed.hypothesis_version_id,
                "protected_metrics": tuple(
                    item.model_copy(update={"source_id": evidence_id})
                    for item in transition.measurement.protected_metrics
                ),
                "countermetrics": tuple(
                    item.model_copy(update={"source_id": evidence_id})
                    for item in transition.measurement.countermetrics
                ),
                "execution_budget": _resource_budget(10),
                "search_budget": _resource_budget(10),
                "evaluation_budget": _resource_budget(10),
                "judging_budget": _resource_budget(10),
                "human_budget": _resource_budget(10),
                "unmeasured_coverage_gaps": ("out-of-distribution hypotheses remained unmeasured",),
                "rollback_target_id": failed.hypothesis_version_id,
                "evaluator_audit_id": audit.evaluator_audit_id,
                "decision_authority": admission_approver,
                "decided_at": self.clock.now(),
                "governing_policy_hash": self.candidate.policy_hash,
            }
        )
        self._submit(
            RecordSelfImprovementMeasurement(
                proposal_id="11-record-hypothesis-measurement",
                idempotency_key="11-record-hypothesis-measurement-key",
                proposer=self.proposer,
                approval=Approval(
                    approver=admission_approver,
                    approved_at=self.clock.now(),
                ),
                measurement=measurement,
            )
        )
        integrator = _model_actor("thermal-hypothesis-integrator")
        integrated_at = self.clock.now()
        admission = HypothesisAdmissionDecision(
            admission_decision_id="thermal-runaway-v1-admission",
            hypothesis_version_id=failed.hypothesis_version_id,
            hypothesis_id=failed.hypothesis_id,
            version=failed.version,
            imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
            model_spec_ids=(self.model.model_spec_id,),
            verification_result_ids=(passing.verification_result_id,),
            counterexample_search_result_ids=(passing.verification_result_id,),
            counterexample_ids=(),
            revision_ids=(),
            evaluator_audit_id=audit.evaluator_audit_id,
            measurement_id=measurement.measurement_id,
            rollback_hypothesis_version_id=None,
            outcome=AdmissionOutcome.ACCEPT,
            rationale="The initial retained deterministic and transfer gates passed.",
            decided_by=integrator,
            decided_at=integrated_at,
            governing_policy_hash=self.candidate.policy_hash,
        )
        self._submit(
            AdmitHypothesis(
                proposal_id="11-admit-runaway-predecessor",
                idempotency_key="11-admit-runaway-predecessor-key",
                proposer=integrator,
                approval=Approval(
                    approver=admission_approver,
                    approved_at=self.clock.now(),
                ),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=hypothesis_receipt,
                model_receipts=(self._receipt("06-register-thermal-model", ModelSpecReceiptRef),),
                verification_result_receipts=(
                    self._receipt(
                        "11-record-initial-runaway-check",
                        VerificationResultReceiptRef,
                    ),
                ),
                counterexample_search_receipts=(
                    self._receipt(
                        "11-record-initial-runaway-check",
                        VerificationResultReceiptRef,
                    ),
                ),
                revision_receipts=(),
                evaluator_audit_receipt=self._receipt(
                    "11-record-hypothesis-audit",
                    EvaluatorAuditReceiptRef,
                ),
                measurement_receipt=self._receipt(
                    "11-record-hypothesis-measurement",
                    SelfImprovementMeasurementReceiptRef,
                ),
                rollback_hypothesis_version_id=None,
                integrated_at=integrated_at,
                admission_decision=admission,
            )
        )

    def _step_11_revision(self) -> None:
        _, failed = _required(self.hypotheses)
        mechanism = self.mechanism
        checker = mechanism.created_by
        hypothesis_receipt = self._receipt(
            f"05-propose-{failed.hypothesis_version_id}",
            HypothesisVersionReceiptRef,
        )
        self._admit_hypothesis_predecessor(failed, hypothesis_receipt)
        verification = DeterministicCheckResult(
            mechanism_type="DETERMINISTIC_CHECKER",
            verification_result_id="thermal-runaway-failed-check",
            hypothesis_version_id=failed.hypothesis_version_id,
            mechanism_spec_id=mechanism.mechanism_spec_id,
            model_spec_id=self.model.model_spec_id,
            simulation_result_ids=(self.simulation.simulation_result_id,),
            outcome=VerificationOutcome.FAIL,
            findings=("The retained simulation peak remains below 40 C.",),
            provenance=AssessmentProvenance(
                actor=checker,
                actor_version="thermal-hypothesis-checker-v1",
                category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                deterministic_or_learned="DETERMINISTIC",
                proposer_relationship=ActorRelationship.INDEPENDENT,
                assumptions=("registered simulator output is retained exactly",),
                evidence_ids=(_required(self.hypothesis_evidence).evidence_id,),
                checks_run=(mechanism.mechanism_spec_id,),
                limitations=("bounded deterministic fixture",),
                result=AssessmentOutcome.FAILED,
                meaningful_confidence=None,
                assessed_at=self.clock.now(),
                governing_policy_hash=self.candidate.policy_hash,
            ),
            counterexample_search_performed=True,
            counterexample_found=True,
            checked_invariants=mechanism.checked_invariants,
        )
        self._submit(
            RecordVerificationResult(
                proposal_id="11-record-runaway-failure",
                idempotency_key="11-record-runaway-failure-key",
                proposer=checker,
                approval=self._approval("verification-result-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=hypothesis_receipt,
                mechanism_receipt=self._receipt(
                    "06-register-runaway-checker",
                    VerificationMechanismReceiptRef,
                ),
                model_receipt=self._receipt(
                    "06-register-thermal-model",
                    ModelSpecReceiptRef,
                ),
                simulation_receipts=(
                    self._receipt(
                        "07-record-thermal-simulation",
                        SimulationResultReceiptRef,
                    ),
                ),
                verification_result=verification,
            )
        )
        counterexample = CounterexampleRecord(
            counterexample_id="thermal-peak-counterexample",
            hypothesis_version_id=failed.hypothesis_version_id,
            model_spec_id=self.model.model_spec_id,
            simulation_result_ids=(self.simulation.simulation_result_id,),
            verification_result_ids=(verification.verification_result_id,),
            evidence_ids=(_required(self.hypothesis_evidence).evidence_id,),
            description="The bounded simulation never reaches the predicted runaway threshold.",
            input_hash=sha256_hex(canonical_json_bytes(self.model_input.model_dump(mode="json"))),
            observed_output_hash=sha256_hex(
                canonical_json_bytes(self.model_output.model_dump(mode="json"))
            ),
            expected_output_hash=sha256_hex(b"peak-temperature-at-least-40-c"),
            discovered_by=checker,
            discovered_at=self.clock.now(),
            governing_policy_hash=self.candidate.policy_hash,
        )
        self._submit(
            RecordCounterexample(
                proposal_id="11-record-runaway-counterexample",
                idempotency_key="11-record-runaway-counterexample-key",
                proposer=checker,
                approval=self._approval("counterexample-stage-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                hypothesis_receipt=hypothesis_receipt,
                model_receipt=self._receipt(
                    "06-register-thermal-model",
                    ModelSpecReceiptRef,
                ),
                simulation_receipts=(
                    self._receipt(
                        "07-record-thermal-simulation",
                        SimulationResultReceiptRef,
                    ),
                ),
                verification_result_receipts=(
                    self._receipt(
                        "11-record-runaway-failure",
                        VerificationResultReceiptRef,
                    ),
                ),
                counterexample=counterexample,
            )
        )
        revision_author = _model_actor("thermal-hypothesis-reviser")
        revised_at = self.clock.now()
        revised = failed.model_copy(
            update={
                "hypothesis_version_id": "thermal-runaway-v2",
                "version": 2,
                "statement": "Sensor drift, not runaway heating, explains the high reading.",
                "assumptions": ("sensor A drifted", "sensor B remained calibrated"),
                "predictions": ("sensor B and chamber model remain below 30 C",),
                "falsification_conditions": ("sensor B independently reaches 30 C",),
                "proposer": revision_author,
                "created_at": revised_at,
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
            author=revision_author,
            revised_at=revised_at,
            governing_policy_hash=self.candidate.policy_hash,
        )
        self._submit(
            ReviseHypothesis(
                proposal_id="11-revise-runaway-hypothesis",
                idempotency_key="11-revise-runaway-hypothesis-key",
                proposer=revision_author,
                approval=self._approval("hypothesis-revision-approver"),
                classification=FIXED_HYPOTHESIS_CLASSIFICATION,
                prior_hypothesis_receipt=hypothesis_receipt,
                triggering_result_receipts=(
                    self._receipt(
                        "11-record-runaway-failure",
                        VerificationResultReceiptRef,
                    ),
                ),
                counterexample_receipts=(
                    self._receipt(
                        "11-record-runaway-counterexample",
                        CounterexampleReceiptRef,
                    ),
                ),
                resulting_hypothesis=revised,
                revision=self.revision,
            )
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
        for index, incident in enumerate(incidents, start=1):
            self._submit(
                RecordRuleIncident(
                    proposal_id=f"12-record-rule-incident-{index}",
                    idempotency_key=f"12-record-rule-incident-{index}-key",
                    proposer=incident.reported_by,
                    approval=self._approval(f"rule-incident-approver-{index}"),
                    classification=FIXED_RULE_CLASSIFICATION,
                    incident=incident,
                )
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
            "thermal-rule-v1",
            "thermal-rule",
            "Continue a chamber on sensor disagreement.",
            ("continue using the median calibrated sensor",),
            incidents,
            self.proposer,
            self.candidate.policy_hash,
        ).model_copy(update={"created_at": self.clock.now()})
        self._submit(
            ProposeBehavioralRule(
                proposal_id="thermal-rule-review",
                idempotency_key="thermal-rule-review-key",
                proposer=self.proposer,
                approval=self._approval("rule-proposal-approver"),
                classification=FIXED_RULE_CLASSIFICATION,
                rule_version=proposed,
            )
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
        evidence_ids = tuple(
            sorted({evidence_id for item in incidents for evidence_id in item.evidence_ids})
        )
        for role in ReviewerRole:
            reviewer = _human_actor(f"{role.value.lower()}-reviewer")
            conflict = (
                ConflictClassification.TRUE_LOGICAL_CONTRADICTION
                if role is ReviewerRole.CONFLICT
                else None
            )
            assessment = ReviewerAssessment(
                assessment_id=f"{role.value.lower()}-assessment",
                role=role,
                provenance=_review_provenance(
                    reviewer,
                    role,
                    evidence_ids,
                    self.candidate.policy_hash,
                ).model_copy(update={"assessed_at": self.clock.now()}),
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
            assessments.append(assessment)
            self._submit(
                ImportReviewerAssessment(
                    proposal_id=f"13-import-{role.value.lower()}-assessment",
                    idempotency_key=f"13-import-{role.value.lower()}-assessment-key",
                    proposer=reviewer,
                    approval=self._approval(f"{role.value.lower()}-assessment-approver"),
                    classification=FIXED_RULE_CLASSIFICATION,
                    assessment=assessment,
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
        classify_overlap(proposed, existing)
        integrator = _human_actor("rule-integrator")
        authority = _human_actor("rule-consolidation-authority")
        candidate_created_at = self.clock.now()
        incident_ids = tuple(sorted(item.incident_id for item in incidents))
        consolidated = _rule(
            "thermal-rule-v2",
            proposed.rule_id,
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
        ).model_copy(
            update={
                "semantic_version": "1.1.0",
                "supersedes_rule_version_ids": (proposed.rule_version_id,),
                "status": RuleStatus.ACTIVE,
                "approver": authority,
                "created_at": candidate_created_at,
                "approved_at": candidate_created_at,
            }
        )
        regression_time = self.clock.now()
        regressions = tuple(
            item.model_copy(update={"created_at": regression_time})
            for item in (
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
        evaluator = _model_actor("rule-consolidation-evaluator")
        auditor = _human_actor("rule-consolidation-auditor")
        transition = _required(self.transition)
        audit = transition.evaluator_audit.model_copy(
            update={
                "evaluator_audit_id": "thermal-rule-audit",
                "auditor": auditor,
                "auditor_version": "thermal-rule-auditor-v1",
                "evaluator": evaluator,
                "evaluator_version": "thermal-rule-evaluator-v1",
                "proposer": integrator,
                "candidate_producer": integrator,
                "evidence_ids": (_required(self.evidence).evidence_id,),
                "checks_run": tuple(item.assessment_id for item in self.assessments),
                "audited_at": self.clock.now(),
                "governing_policy_hash": self.candidate.policy_hash,
            }
        )
        self._submit(
            RecordEvaluatorAudit(
                proposal_id="14-record-rule-audit",
                idempotency_key="14-record-rule-audit-key",
                proposer=auditor,
                approval=self._approval("rule-audit-approver"),
                evaluator_audit=audit,
            )
        )
        evidence_id = _required(self.evidence).evidence_id
        trajectory = tuple(
            point.model_copy(
                update={
                    "change_id": "thermal-rule-change",
                    "grounding": (ExternalGrounding.PRIMARY_SOURCE,),
                }
            )
            for point in transition.measurement.trajectory
        )
        measurement = SelfImprovementMeasurementRecord.model_validate(
            transition.measurement.model_dump(mode="python")
            | {
                "measurement_id": "thermal-rule-measurement",
                "change_id": "thermal-rule-change",
                "run_id": _required(self.research_run).run_id,
                "classification": FIXED_RULE_CLASSIFICATION,
                "proposer": integrator,
                "evaluator": evaluator,
                "evaluator_version": audit.evaluator_version,
                "grounding": (ExternalGrounding.PRIMARY_SOURCE,),
                "baseline_version_id": proposed.rule_version_id,
                "candidate_version_id": consolidated.rule_version_id,
                "protected_metrics": tuple(
                    item.model_copy(update={"source_id": evidence_id})
                    for item in transition.measurement.protected_metrics
                ),
                "countermetrics": tuple(
                    item.model_copy(update={"source_id": evidence_id})
                    for item in transition.measurement.countermetrics
                ),
                "trajectory": trajectory,
                "peak_observation": transition.measurement.peak_observation.model_copy(
                    update={"metrics": trajectory[-1].metrics}
                ),
                "final_observation": transition.measurement.final_observation.model_copy(
                    update={"metrics": trajectory[-1].metrics}
                ),
                "execution_budget": _resource_budget(10),
                "search_budget": _resource_budget(10),
                "evaluation_budget": _resource_budget(10),
                "judging_budget": _resource_budget(10),
                "human_budget": _resource_budget(10),
                "unmeasured_coverage_gaps": ("cross-domain rule application remained unmeasured",),
                "rollback_target_id": proposed.rule_version_id,
                "evaluator_audit_id": audit.evaluator_audit_id,
                "decision_authority": authority,
                "decided_at": self.clock.now(),
                "governing_policy_hash": self.candidate.policy_hash,
            }
        )
        self._submit(
            RecordSelfImprovementMeasurement(
                proposal_id="14-record-rule-measurement",
                idempotency_key="14-record-rule-measurement-key",
                proposer=integrator,
                approval=Approval(
                    approver=authority,
                    approved_at=self.clock.now(),
                ),
                measurement=measurement,
            )
        )
        integrated_at = self.clock.now()
        consolidation_approval = Approval(
            approver=authority,
            approved_at=self.clock.now(),
        )
        consolidated = consolidated.model_copy(
            update={"approved_at": consolidation_approval.approved_at}
        )
        consolidation = build_candidate_diff(
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
            integrated_at=integrated_at,
            governing_policy_hash=self.candidate.policy_hash,
            overlap=None,
        )
        self._submit(
            ConsolidateBehavioralRule(
                proposal_id="14-consolidate-thermal-rule",
                idempotency_key="14-consolidate-thermal-rule-key",
                proposer=integrator,
                approval=consolidation_approval,
                classification=FIXED_RULE_CLASSIFICATION,
                consolidation=consolidation,
                measurement_id=measurement.measurement_id,
                evaluator_audit_id=audit.evaluator_audit_id,
                rollback_rule_version_id=proposed.rule_version_id,
            )
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
        mapping = canonical_json_bytes(
            {
                "source_link": source_link.model_dump(mode="json"),
                "rule_link": rule_link.model_dump(mode="json"),
            }
        )
        mapping_artifact = self.artifacts.put(mapping, "application/json")
        self._submit(
            AddEvidence(
                proposal_id="16-record-behavior-mapping",
                idempotency_key="16-record-behavior-mapping-key",
                proposer=self.proposer,
                evidence=EvidenceRecord(
                    evidence_id="behavior-mapping-evidence",
                    evidence_type="source-behavior-mapping",
                    source_locator="fixture://ssoh/behavior-mapping",
                    retrieved_at=self.clock.now(),
                    artifact=mapping_artifact,
                    provenance={
                        "collector": "governed-adaptation-example",
                        "external_grounding": ExternalGrounding.PRIMARY_SOURCE.value,
                    },
                    ingestion_actor_id=self.proposer.actor_id,
                    verification_state=VerificationState.UNVERIFIED,
                ),
            )
        )
        self._complete(16)

    def _step_17_campaign(self) -> HarnessCampaign:
        campaign = _campaign(
            self.candidate.policy_hash,
            self.proposer,
            self.approver,
            campaign_id="thermal-campaign-benchmark",
        )
        if any(not item.comparable for item in _budget_comparisons(campaign)):
            raise RuntimeError("matched-budget campaign is not comparable")
        self._submit(
            CreateHarnessCampaign(
                proposal_id="17-create-benchmark-campaign",
                idempotency_key="17-create-benchmark-campaign-key",
                proposer=campaign.candidate_producer,
                approval=Approval(
                    approver=campaign.coordinator,
                    approved_at=self.clock.now(),
                ),
                campaign=campaign,
            )
        )
        self._complete(17)
        return campaign

    def _record_harness_evidence(
        self,
        campaign: HarnessCampaign,
        *,
        transfer: Decimal,
    ) -> tuple[tuple[CampaignIteration, ...], tuple[PartitionMetric, ...]]:
        candidate_values = {
            HarnessPartition.HARNESS_DISCOVERY_TASKS: Decimal("0.8"),
            HarnessPartition.HARNESS_VALIDATION_TASKS: Decimal("0.8"),
            HarnessPartition.HARNESS_TRANSFER_TASKS: transfer,
            HarnessPartition.HARNESS_REGRESSION_TASKS: Decimal("0.5"),
            HarnessPartition.HARNESS_SAFETY_TASKS: Decimal("0.5"),
        }
        checker = _harness_checker(campaign)
        iterations: list[CampaignIteration] = []
        proposals: list[object] = []
        result_ids: dict[HarnessPartition, list[str]] = {
            partition: [] for partition in HarnessPartition
        }
        index = 0
        for partition in HarnessPartition:
            manifest = next(item for item in campaign.partitions if item.partition is partition)
            for variant in (campaign.baseline_variant, campaign.candidate_variant):
                budget = next(item for item in campaign.budgets if item.variant is variant)
                result_id = (
                    f"{campaign.campaign_id}-result-{partition.value.lower()}-"
                    f"{variant.value.lower()}"
                )
                output_hash = sha256_hex(result_id.encode("utf-8"))
                value = (
                    Decimal("0.5")
                    if variant is campaign.baseline_variant
                    else candidate_values[partition]
                )
                iteration = CampaignIteration(
                    iteration_index=index,
                    observation_id=f"{campaign.campaign_id}-observation-{index}",
                    partition_manifest_id=manifest.partition_manifest_id,
                    task_id=manifest.task_ids[0],
                    partition=partition,
                    variant=variant,
                    budget_id=budget.budget_id,
                    attempt=1,
                    candidate_output_hash=output_hash,
                    result_id=result_id,
                    outcome=AssessmentOutcome.PASSED,
                    negative_result=False,
                    evaluator_version_id=campaign.evaluator_version_id,
                    observed_at=self.clock.now(),
                )
                authority_approval = Approval(
                    approver=campaign.coordinator,
                    approved_at=self.clock.now(),
                )
                proposals.append(
                    RecordHarnessIteration(
                        proposal_id=f"record-{result_id}",
                        idempotency_key=f"record-{result_id}-key",
                        proposer=campaign.coordinator,
                        approval=authority_approval,
                        iteration=iteration,
                        governing_policy_hash=self.candidate.policy_hash,
                    )
                )
                result = ProtectedCheckerResult(
                    result_id=result_id,
                    campaign_id=campaign.campaign_id,
                    task_id=manifest.task_ids[0],
                    expected_output_hash="a" * 64,
                    candidate_output_hash=output_hash,
                    checker_id=checker.checker_id,
                    checker_version=checker.checker_version,
                    outcome=AssessmentOutcome.PASSED,
                    metric_values=(MetricValue(metric_id="correctness", value=value),),
                    evaluated_at=self.clock.now(),
                )
                proposals.append(
                    RecordHarnessProtectedResult(
                        proposal_id=f"protect-{result_id}",
                        idempotency_key=f"protect-{result_id}-key",
                        proposer=campaign.coordinator,
                        approval=Approval(
                            approver=campaign.coordinator,
                            approved_at=self.clock.now(),
                        ),
                        observation_id=iteration.observation_id,
                        partition_manifest_id=manifest.partition_manifest_id,
                        variant=variant,
                        evaluator_version_id=campaign.evaluator_version_id,
                        checker_configuration=checker,
                        result=result,
                        governing_policy_hash=self.candidate.policy_hash,
                    )
                )
                iterations.append(iteration)
                result_ids[partition].append(result_id)
                index += 1
        self._submit_batch(tuple(proposals))
        metrics = tuple(
            PartitionMetric(
                partition=partition,
                metric_id="correctness",
                baseline_value=Decimal("0.5"),
                candidate_value=candidate_values[partition],
                higher_is_better=True,
                catastrophic_regression=False,
                result_ids=tuple(result_ids[partition]),
                evaluator_version_id=campaign.evaluator_version_id,
            )
            for partition in HarnessPartition
        )
        return tuple(iterations), metrics

    def _step_18_benchmark_specific(self, campaign: HarnessCampaign) -> None:
        iterations, metrics = self._record_harness_evidence(
            campaign,
            transfer=Decimal("0.4"),
        )
        report = _campaign_report(
            campaign,
            self.approver,
            iterations=iterations,
            metrics=metrics,
            admission_requested=False,
            evaluator_audit_id="benchmark-audit-not-required",
            measurement_id="benchmark-measurement-not-required",
            reported_at=self.clock.now(),
        )
        decision = decide_campaign(report)
        self._submit(
            DecideHarnessCampaign(
                proposal_id="18-decide-benchmark-campaign",
                idempotency_key="18-decide-benchmark-campaign-key",
                proposer=self.approver,
                approval=Approval(
                    approver=self.approver,
                    approved_at=self.clock.now(),
                ),
                report=report,
                decision=decision,
            )
        )
        self.benchmark_iterations = iterations
        self.benchmark_metrics = metrics
        self.first_harness_status = decision.status
        if decision.status is not HarnessDecisionStatus.BENCHMARK_SPECIFIC:
            raise RuntimeError("discovery-only gain was not labeled benchmark-specific")
        self._complete(18)

    def _record_harness_admission_support(
        self,
        campaign: HarnessCampaign,
        result_ids: tuple[str, ...],
    ) -> tuple[EvaluatorAuditRecord, SelfImprovementMeasurementRecord]:
        auditor = _human_actor("harness-transfer-auditor")
        transition = _required(self.transition)
        audit = transition.evaluator_audit.model_copy(
            update={
                "evaluator_audit_id": "harness-transfer-audit",
                "auditor": auditor,
                "auditor_version": "harness-transfer-auditor-v1",
                "evaluator": campaign.evaluator,
                "evaluator_version": campaign.evaluator_version_id,
                "proposer": campaign.candidate_producer,
                "candidate_producer": campaign.candidate_producer,
                "evidence_ids": result_ids,
                "checks_run": result_ids,
                "audited_at": self.clock.now(),
                "governing_policy_hash": self.candidate.policy_hash,
            }
        )
        self._submit(
            RecordEvaluatorAudit(
                proposal_id="19-record-harness-audit",
                idempotency_key="19-record-harness-audit-key",
                proposer=auditor,
                approval=self._approval("harness-audit-approver"),
                evaluator_audit=audit,
            )
        )
        classification = ChangeClassification(
            target=ChangeTarget.ORCHESTRATION,
            loop_closure=LoopClosure.HUMAN_IN_LOOP,
            persistence=PersistenceScope.HARNESS_CODE,
            verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            grounding=ExternalGrounding.INDEPENDENT_TEST_SUITE,
            signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
        )
        trajectory = tuple(
            point.model_copy(
                update={
                    "change_id": "harness-transfer-change",
                    "grounding": (ExternalGrounding.INDEPENDENT_TEST_SUITE,),
                }
            )
            for point in transition.measurement.trajectory
        )
        protected_metrics = tuple(
            MetricObservation(
                metric_id=f"harness-transfer-protected-{index}",
                value=1.0,
                source_id=result_id,
                protected=True,
                external=True,
            )
            for index, result_id in enumerate(result_ids)
        )
        measurement = SelfImprovementMeasurementRecord.model_validate(
            transition.measurement.model_dump(mode="python")
            | {
                "measurement_id": "harness-transfer-measurement",
                "change_id": "harness-transfer-change",
                "run_id": _required(self.research_run).run_id,
                "classification": classification,
                "proposer": campaign.candidate_producer,
                "evaluator": campaign.evaluator,
                "evaluator_version": campaign.evaluator_version_id,
                "grounding": (ExternalGrounding.INDEPENDENT_TEST_SUITE,),
                "baseline_version_id": campaign.baseline_harness_version_id,
                "candidate_version_id": campaign.candidate_harness_version_id,
                "protected_metrics": protected_metrics,
                "countermetrics": tuple(
                    item.model_copy(update={"source_id": result_ids[0]})
                    for item in transition.measurement.countermetrics
                ),
                "trajectory": trajectory,
                "peak_observation": transition.measurement.peak_observation.model_copy(
                    update={"metrics": trajectory[-1].metrics}
                ),
                "final_observation": transition.measurement.final_observation.model_copy(
                    update={"metrics": trajectory[-1].metrics}
                ),
                "execution_budget": _resource_budget(10),
                "search_budget": _resource_budget(10),
                "evaluation_budget": _resource_budget(10),
                "judging_budget": _resource_budget(10),
                "human_budget": _resource_budget(10),
                "unmeasured_coverage_gaps": ("live provider transfer remained unmeasured",),
                "rollback_target_id": campaign.rollback_harness_version_id,
                "evaluator_audit_id": audit.evaluator_audit_id,
                "decision_authority": campaign.coordinator,
                "decided_at": self.clock.now(),
                "governing_policy_hash": self.candidate.policy_hash,
            }
        )
        self._submit(
            RecordSelfImprovementMeasurement(
                proposal_id="19-record-harness-measurement",
                idempotency_key="19-record-harness-measurement-key",
                proposer=campaign.candidate_producer,
                approval=Approval(
                    approver=campaign.coordinator,
                    approved_at=self.clock.now(),
                ),
                measurement=measurement,
            )
        )
        self.harness_measurement = measurement
        return audit, measurement

    def _step_19_admit_transfer(self, campaign: HarnessCampaign) -> None:
        manifest = next(
            item
            for item in campaign.partitions
            if item.partition is HarnessPartition.HARNESS_TRANSFER_TASKS
        )
        checker = _harness_checker(campaign)
        followup_iterations: list[CampaignIteration] = []
        proposals: list[object] = []
        for offset, variant in enumerate((campaign.candidate_variant,)):
            budget = next(item for item in campaign.budgets if item.variant is variant)
            result_id = f"{campaign.campaign_id}-held-out-transfer-{variant.value.lower()}"
            output_hash = sha256_hex(result_id.encode("utf-8"))
            iteration = CampaignIteration(
                iteration_index=len(self.benchmark_iterations) + offset,
                observation_id=f"{campaign.campaign_id}-held-out-observation-{offset}",
                partition_manifest_id=manifest.partition_manifest_id,
                task_id=manifest.task_ids[0],
                partition=manifest.partition,
                variant=variant,
                budget_id=budget.budget_id,
                attempt=2,
                candidate_output_hash=output_hash,
                result_id=result_id,
                outcome=AssessmentOutcome.PASSED,
                negative_result=False,
                evaluator_version_id=campaign.evaluator_version_id,
                observed_at=self.clock.now(),
            )
            proposals.append(
                RecordHarnessIteration(
                    proposal_id=f"record-{result_id}",
                    idempotency_key=f"record-{result_id}-key",
                    proposer=campaign.coordinator,
                    approval=Approval(
                        approver=campaign.coordinator,
                        approved_at=self.clock.now(),
                    ),
                    iteration=iteration,
                    governing_policy_hash=self.candidate.policy_hash,
                )
            )
            value = Decimal("0.8")
            proposals.append(
                RecordHarnessProtectedResult(
                    proposal_id=f"protect-{result_id}",
                    idempotency_key=f"protect-{result_id}-key",
                    proposer=campaign.coordinator,
                    approval=Approval(
                        approver=campaign.coordinator,
                        approved_at=self.clock.now(),
                    ),
                    observation_id=iteration.observation_id,
                    partition_manifest_id=manifest.partition_manifest_id,
                    variant=variant,
                    evaluator_version_id=campaign.evaluator_version_id,
                    checker_configuration=checker,
                    result=ProtectedCheckerResult(
                        result_id=result_id,
                        campaign_id=campaign.campaign_id,
                        task_id=manifest.task_ids[0],
                        expected_output_hash="a" * 64,
                        candidate_output_hash=output_hash,
                        checker_id=checker.checker_id,
                        checker_version=checker.checker_version,
                        outcome=AssessmentOutcome.PASSED,
                        metric_values=(MetricValue(metric_id="correctness", value=value),),
                        evaluated_at=self.clock.now(),
                    ),
                    governing_policy_hash=self.candidate.policy_hash,
                )
            )
            followup_iterations.append(iteration)
        self._submit_batch(tuple(proposals))
        iterations = (*self.benchmark_iterations, *followup_iterations)
        followup_result_ids = tuple(
            item.result_id for item in followup_iterations if item.result_id is not None
        )
        metrics = tuple(
            metric.model_copy(
                update={
                    "candidate_value": Decimal("0.6"),
                    "result_ids": (*metric.result_ids, *followup_result_ids),
                }
            )
            if metric.partition is HarnessPartition.HARNESS_TRANSFER_TASKS
            else metric
            for metric in self.benchmark_metrics
        )
        result_ids = tuple(result_id for metric in metrics for result_id in metric.result_ids)
        audit, measurement = self._record_harness_admission_support(
            campaign,
            result_ids,
        )
        report = _campaign_report(
            campaign,
            self.approver,
            iterations=iterations,
            metrics=metrics,
            admission_requested=True,
            evaluator_audit_id=audit.evaluator_audit_id,
            measurement_id=measurement.measurement_id,
            reported_at=self.clock.now(),
        )
        decision = decide_campaign(report)
        self._submit(
            DecideHarnessCampaign(
                proposal_id="19-decide-transfer-campaign",
                idempotency_key="19-decide-transfer-campaign-key",
                proposer=self.approver,
                approval=Approval(
                    approver=self.approver,
                    approved_at=self.clock.now(),
                ),
                report=report,
                decision=decision,
            )
        )
        self.second_harness_status = decision.status
        if decision.status is not HarnessDecisionStatus.ADMITTED:
            raise RuntimeError("held-out transferred candidate was not admitted")
        self._complete(19)

    def _step_20_measurement_report(self) -> None:
        measurement = self.harness_measurement
        report = canonical_json_bytes(measurement.model_dump(mode="json"))
        reference = self.artifacts.put(report, "application/json")
        if self.artifacts.read(reference) != report:
            raise RuntimeError("measurement report did not round-trip content-addressably")
        self._submit(
            AddEvidence(
                proposal_id="20-record-measurement-report",
                idempotency_key="20-record-measurement-report-key",
                proposer=self.proposer,
                evidence=EvidenceRecord(
                    evidence_id="harness-measurement-report",
                    evidence_type="self-improvement-measurement-report",
                    source_locator="fixture://ssoh/harness-measurement-report",
                    retrieved_at=self.clock.now(),
                    artifact=reference,
                    provenance={
                        "collector": "governed-adaptation-example",
                        "external_grounding": (ExternalGrounding.INDEPENDENT_TEST_SUITE.value),
                    },
                    ingestion_actor_id=self.proposer.actor_id,
                    verification_state=VerificationState.UNVERIFIED,
                ),
            )
        )
        self.measurement_report_hash = reference.sha256
        self._complete(20)

    def _step_21_verify(self) -> None:
        with self.uow_factory() as unit_of_work:
            repositories = unit_of_work.repositories()
            result = verify_workspace(repositories, self.artifacts)
            self.policy_versions = tuple(
                snapshot.policy.schema_version for snapshot in repositories.policies.list_all()
            )
            transactions = {
                item.proposal.proposal_id: item for item in repositories.transactions.list_all()
            }
            adaptation = repositories.adaptation_integrity_snapshot()
            progress = repositories.progress_integrity_snapshot()
            trail = repositories.trail_integrity_snapshot()
            rules = repositories.rule_integrity_snapshot()
            hypotheses = repositories.hypothesis_integrity_snapshot()
            evidence_ids = {item.evidence_id for item in repositories.evidence.list_all()}
        false_finish = transactions.get("10-reject-false-finish")
        revision = transactions.get("11-revise-runaway-hypothesis")
        benchmark = transactions.get("18-decide-benchmark-campaign")
        transfer = transactions.get("19-decide-transfer-campaign")
        self.false_finish_rejected = bool(
            false_finish is not None
            and not false_finish.decision.accepted
            and any(
                reason.code is RejectionCode.FALSE_FINISH
                for reason in false_finish.decision.reasons
            )
        )
        self.failed_hypothesis_preserved = bool(
            revision is not None
            and revision.decision.accepted
            and any(item.revision_id == "thermal-revision-v2" for item in hypotheses.revisions)
        )
        if (
            benchmark is None
            or not benchmark.decision.accepted
            or not isinstance(benchmark.proposal, DecideHarnessCampaign)
            or transfer is None
            or not transfer.decision.accepted
            or not isinstance(transfer.proposal, DecideHarnessCampaign)
        ):
            raise RuntimeError("durable harness decisions are unavailable")
        self.first_harness_status = benchmark.proposal.decision.status
        self.second_harness_status = transfer.proposal.decision.status
        self.audit_valid = bool(
            result.valid
            and self.policy_versions == (1, 2)
            and adaptation.research_run_heads
            and progress.heads
            and trail.heads
            and rules.heads
            and hypotheses.heads
            and self.false_finish_rejected
            and self.failed_hypothesis_preserved
            and self.first_harness_status is HarnessDecisionStatus.BENCHMARK_SPECIFIC
            and self.second_harness_status is HarnessDecisionStatus.ADMITTED
            and {
                "behavior-mapping-evidence",
                "harness-measurement-report",
            }
            <= evidence_ids
        )
        if not self.audit_valid:
            raise RuntimeError(result.reason or "mixed-policy workspace verification failed")
        self._complete(21)


def _v1_policy() -> PolicySnapshot:
    policy = GovernancePolicy(required_claim_checks=("source_exists",))
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _v2_policy() -> PolicySnapshot:
    policy = GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset({"governance_change", "harness_admission"}),
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
            AdaptationRequirement(
                change_target=ChangeTarget.RESEARCH_PROCESS,
                persistence=PersistenceScope.RUN_LOCAL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset(
                    {
                        ExternalGrounding.HUMAN_JUDGMENT,
                        ExternalGrounding.PRIMARY_SOURCE,
                        ExternalGrounding.CONTROLLED_EXPERIMENT,
                    }
                ),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=False,
                rollback_required=False,
            ),
            AdaptationRequirement(
                change_target=ChangeTarget.BEHAVIORAL_RULE,
                persistence=PersistenceScope.PERSISTENT_RULE,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.PRIMARY_SOURCE}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=False,
                rollback_required=False,
            ),
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
        unmeasured_coverage_gaps=("live production behavior remained unmeasured",),
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
        attempts=2,
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
    *,
    campaign_id: str = "thermal-campaign",
) -> HarnessCampaign:
    variants = (
        HarnessVariant.UNCHANGED_HARNESS_SINGLE_ATTEMPT,
        HarnessVariant.EVOLVED_HARNESS,
    )
    partitions = tuple(
        CampaignPartitionManifest(
            partition_manifest_id=f"{campaign_id}-manifest-{partition.value.lower()}",
            campaign_id=campaign_id,
            campaign_version=1,
            partition=partition,
            task_ids=(f"{campaign_id}-{partition.value.lower()}-task",),
            manifest_hash=partition_manifest_hash(
                campaign_id=campaign_id,
                campaign_version=1,
                partition=partition,
                task_ids=(f"{campaign_id}-{partition.value.lower()}-task",),
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
        campaign_id=campaign_id,
        version=1,
        variants=variants,
        baseline_variant=variants[0],
        candidate_variant=variants[1],
        baseline_harness_version_id=f"{campaign_id}-harness-v1",
        candidate_harness_version_id=f"{campaign_id}-harness-v2",
        rollback_harness_version_id=f"{campaign_id}-harness-v1",
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
                budget_id=f"{campaign_id}-baseline-budget",
                variant=variants[0],
                budget=budget,
            ),
            VariantEvaluationBudget(
                budget_id=f"{campaign_id}-candidate-budget",
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


def _harness_checker(campaign: HarnessCampaign) -> FixedCheckerConfiguration:
    checker_id = "thermal-harness-checker"
    checker_version = "thermal-harness-checker-v1"
    checker_kind = FixedCheckerKind.EXACT_BYTES
    metric_ids = ("correctness",)
    metric_directions = (True,)
    return FixedCheckerConfiguration(
        checker_id=checker_id,
        checker_version=checker_version,
        checker_kind=checker_kind,
        configuration_hash=fixed_checker_configuration_hash(
            checker_id=checker_id,
            checker_version=checker_version,
            checker_kind=checker_kind,
            metric_ids=metric_ids,
            evaluator_id=campaign.evaluator.actor_id,
            evaluator_version_id=campaign.evaluator_version_id,
            metric_higher_is_better=metric_directions,
        ),
        metric_ids=metric_ids,
        metric_higher_is_better=metric_directions,
        evaluator_id=campaign.evaluator.actor_id,
        evaluator_version_id=campaign.evaluator_version_id,
    )


def _campaign_report(
    campaign: HarnessCampaign,
    authority: ActorIdentity,
    *,
    iterations: tuple[CampaignIteration, ...],
    metrics: tuple[PartitionMetric, ...],
    admission_requested: bool,
    evaluator_audit_id: str,
    measurement_id: str,
    reported_at: datetime,
) -> HarnessCampaignReport:
    return HarnessCampaignReport(
        campaign=campaign,
        expected_iteration_count=len(iterations),
        iterations=iterations,
        negative_observation_ids=tuple(
            item.observation_id for item in iterations if item.negative_result
        ),
        budget_comparisons=_budget_comparisons(campaign),
        metrics=metrics,
        confounds=(),
        evaluator_audit_id=evaluator_audit_id,
        evaluator_audit_passed=True,
        measurement_id=measurement_id,
        measurement_accepted=True,
        rollback=None,
        admission_requested=admission_requested,
        decision_authority=authority,
        reported_at=reported_at,
        governing_policy_hash=campaign.governing_policy_hash,
    )


def _human_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity(actor_id=identifier, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity.model(identifier, identifier, identifier, None, NOW).model_copy(
        update={"configuration_hash": sha256_hex(identifier.encode("utf-8"))}
    )


def _required[ValueT](value: ValueT | None) -> ValueT:
    if value is None:
        raise RuntimeError("vertical-slice step dependency is unavailable")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args()
    result = VerticalSlice(arguments.root).run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
