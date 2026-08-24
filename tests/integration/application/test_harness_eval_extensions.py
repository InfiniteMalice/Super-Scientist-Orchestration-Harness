from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from sqlalchemy import Engine

from super_scientist.application.harness_eval.extensions import (
    AppendGuidanceEvaluationCellHandler,
    AppendModelHarnessCellHandler,
    HarnessTraceProposalAdapter,
    RecordGuidanceEvaluationProtocolHandler,
    RecordHarnessExecutionTraceHandler,
    RecordModelHarnessAnalysisHandler,
    RecordModelHarnessProtocolHandler,
    RecordRewardAssessmentHandler,
    RewardAssessmentCapabilities,
)
from super_scientist.application.transactions.harness_extensions import (
    HarnessTraceCapabilities,
    ModelHarnessAnalysisCapabilities,
    _guidance_cell_evidence_is_current,
    _guidance_cell_evidence_matches,
    _receipt_is_current,
    harness_extension_capabilities,
)
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.harness_eval.evidence_chains import (
    harness_cell_evidence_chain_receipt,
)
from super_scientist.domain.harness_eval.guidance import (
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
)
from super_scientist.domain.harness_eval.matrix import (
    HarnessIdentity,
    ModelBudgetBinding,
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessProtocol,
    ModelIdentity,
)
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.harness_eval.rewards import (
    RewardValidityAssessment,
    RewardValidityStatus,
    valid_reward_evidence,
)
from super_scientist.domain.harness_eval.traces import HarnessExecutionTrace
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    Approval,
    HarnessTraceRecordMetadata,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordRewardAssessment,
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.evaluation_records import (
    GuidanceEvaluationProtocolRepository,
)
from tests.integration.application.test_harness_eval_service import _policy
from tests.unit.harness_eval.test_guidance import _cell as guidance_cell
from tests.unit.harness_eval.test_guidance import _protocol as guidance_protocol
from tests.unit.harness_eval.test_harness_security_contracts import _matrix_evidence_chain
from tests.unit.harness_eval.test_model_harness_matrix import (
    _budget as matrix_budget,
)
from tests.unit.harness_eval.test_model_harness_matrix import (
    _cells as matrix_cells,
)
from tests.unit.harness_eval.test_model_harness_matrix import (
    _evidence_fixtures as matrix_evidence_fixtures,
)
from tests.unit.harness_eval.test_model_harness_matrix import (
    _evidence_index as matrix_evidence_index,
)
from tests.unit.harness_eval.test_model_harness_matrix import _grid as matrix_grid
from tests.unit.harness_eval.test_model_harness_matrix import _metrics as matrix_metrics
from tests.unit.harness_eval.test_model_harness_matrix import (
    _protocol as matrix_protocol,
)
from tests.unit.harness_eval.test_model_harness_matrix import (
    analyze_model_harness as matrix_analysis,
)
from tests.unit.harness_eval.test_rewards import assess_reward_validity
from tests.unit.harness_eval.test_traces import valid_trace

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def extension_engine(tmp_path: Path) -> Engine:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'extensions.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    with DatabaseUnitOfWork(engine) as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(_policy(), NOW)
    try:
        yield engine
    finally:
        engine.dispose()


def _actor() -> ActorIdentity:
    return ActorIdentity(actor_id="harness-authority", kind=ActorKind.HUMAN, created_at=NOW)


def _approval() -> Approval:
    return Approval(approver=_actor(), approved_at=NOW)


def _proposal(model: type[BaseModel], **values: object) -> BaseModel:
    record_id = str(values.pop("record_id"))
    return model(
        proposal_id=f"proposal-{record_id}",
        idempotency_key=f"key-{record_id}",
        proposer=_actor(),
        approval=_approval(),
        **values,
    )


def _persist_accepted_provenance(
    unit_of_work: DatabaseUnitOfWork,
    proposal: BaseModel,
    decision: TransactionDecision,
) -> None:
    repositories = unit_of_work.repositories()
    repositories.transactions.add(proposal, decision, NOW)
    repositories.audit.add(
        append_event(
            repositories.audit.last(),
            "transaction_decision",
            {
                "proposal": proposal.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
                "policy_hash": _policy().policy_hash,
                "stored_policy_hash": _policy().policy_hash,
                "transaction_persisted": True,
            },
            NOW,
        )
    )


@dataclass
class _Capabilities:
    policy: PolicySnapshot = field(default_factory=_policy)
    guidance_protocol: GuidanceEvaluationProtocol | None = None
    guidance_cell: GuidanceEvaluationCell | None = None
    matrix_protocol: ModelHarnessProtocol | None = None
    matrix_cells: tuple[ModelHarnessCell, ...] = ()
    matrix_analysis: ModelHarnessAnalysis | None = None
    trace: HarnessExecutionTrace | None = None
    reward: RewardValidityAssessment | None = None
    current: bool = True
    matched: bool = True
    projected: list[BaseModel] = field(default_factory=list)

    def policy_snapshot(self) -> PolicySnapshot:
        return self.policy

    def get_guidance_protocol(self, protocol_id: str) -> GuidanceEvaluationProtocol | None:
        if self.guidance_protocol is not None and self.guidance_protocol.protocol_id == protocol_id:
            return self.guidance_protocol
        return None

    def get_guidance_cell(self, cell_id: str) -> GuidanceEvaluationCell | None:
        if self.guidance_cell is not None and self.guidance_cell.cell_id == cell_id:
            return self.guidance_cell
        return None

    def guidance_cell_evidence_is_current(self, cell: GuidanceEvaluationCell) -> bool:
        del cell
        return self.current

    def guidance_cell_evidence_matches(self, cell: GuidanceEvaluationCell) -> bool:
        del cell
        return self.matched

    def get_model_harness_protocol(self, protocol_id: str) -> ModelHarnessProtocol | None:
        if self.matrix_protocol is not None and self.matrix_protocol.protocol_id == protocol_id:
            return self.matrix_protocol
        return None

    def get_model_harness_cell(self, cell_id: str) -> ModelHarnessCell | None:
        return next((item for item in self.matrix_cells if item.cell_id == cell_id), None)

    def model_harness_cell_evidence_is_current(self, cell: ModelHarnessCell) -> bool:
        del cell
        return self.current

    def get_model_harness_analysis(self, protocol_id: str) -> ModelHarnessAnalysis | None:
        if self.matrix_analysis is not None and self.matrix_analysis.protocol_id == protocol_id:
            return self.matrix_analysis
        return None

    def list_model_harness_cells(self, protocol_id: str) -> tuple[ModelHarnessCell, ...]:
        return tuple(item for item in self.matrix_cells if item.protocol_id == protocol_id)

    def resolve_model_harness_evidence(self, protocol_id: str) -> object | None:
        if not self.current or self.matrix_protocol is None:
            return None
        fixtures = matrix_evidence_fixtures(self.matrix_protocol)
        return (
            tuple(item.chain for item in fixtures),
            matrix_evidence_index(self.matrix_protocol),
        )

    def get_harness_execution_trace(self, trace_id: str) -> HarnessExecutionTrace | None:
        if self.trace is not None and self.trace.trace_id == trace_id:
            return self.trace
        return None

    def trace_evidence_is_current(self, trace: HarnessExecutionTrace) -> bool:
        del trace
        return self.current

    def get_reward_assessment(self, assessment_id: str) -> RewardValidityAssessment | None:
        if self.reward is not None and self.reward.assessment_id == assessment_id:
            return self.reward
        return None

    def resolve_reward_assessment_capabilities(
        self,
        *,
        trace_receipt: object,
        assessment_receipt: object,
        assessment: RewardValidityAssessment,
    ) -> RewardAssessmentCapabilities | None:
        del trace_receipt, assessment_receipt
        if not self.current:
            return None
        return RewardAssessmentCapabilities(
            expectation=assessment.expectation,
            verification=assessment.verification,
            diagnostic_coverage=assessment.diagnostic_coverage,
            inventory=assessment.evidence_inventory,
        )

    def append_authoritative(self, record: BaseModel) -> None:
        self.projected.append(record)

    def update_projection(self, record: BaseModel) -> None:
        raise AssertionError(f"extension evidence cannot update projections: {record!r}")


def _run(handler: object, proposal: BaseModel, capabilities: _Capabilities):
    context = handler.build_context(proposal, capabilities)  # type: ignore[attr-defined]
    decision = handler.decide(proposal, context)  # type: ignore[attr-defined]
    if decision.accepted:
        handler.project(proposal, decision, capabilities)  # type: ignore[attr-defined]
    return decision


def test_guidance_protocol_and_cell_require_exact_current_protocol() -> None:
    protocol = guidance_protocol()
    protocol_proposal = _proposal(
        RecordGuidanceEvaluationProtocol,
        record_id="guidance-protocol",
        protocol=protocol,
    )
    capabilities = _Capabilities()

    assert _run(RecordGuidanceEvaluationProtocolHandler(), protocol_proposal, capabilities).accepted
    assert capabilities.projected == [protocol]

    cell = guidance_cell(protocol=protocol)
    cell_proposal = _proposal(
        AppendGuidanceEvaluationCell,
        record_id="guidance-cell",
        cell=cell,
    )
    missing = _run(AppendGuidanceEvaluationCellHandler(), cell_proposal, _Capabilities())
    stale = _run(
        AppendGuidanceEvaluationCellHandler(),
        cell_proposal,
        _Capabilities(guidance_protocol=protocol, current=False),
    )
    accepted_capabilities = _Capabilities(guidance_protocol=protocol)
    accepted = _run(AppendGuidanceEvaluationCellHandler(), cell_proposal, accepted_capabilities)

    assert missing.reasons[0].code is RejectionCode.MISSING_ENTITY
    assert stale.reasons[0].code is RejectionCode.STALE_REFERENCE
    assert accepted.accepted is True
    assert accepted_capabilities.projected == [cell]


def test_guidance_cell_rejects_cross_protocol_evidence_as_unmatched() -> None:
    protocol = guidance_protocol()
    cell = guidance_cell(protocol=protocol)
    proposal = _proposal(
        AppendGuidanceEvaluationCell,
        record_id="cross-protocol-guidance-cell",
        cell=cell,
    )

    decision = _run(
        AppendGuidanceEvaluationCellHandler(),
        proposal,
        _Capabilities(guidance_protocol=protocol, matched=False),
    )

    assert decision.reasons[0].code is RejectionCode.UNMATCHED_EVALUATION


class _LookupRepository:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, record_id: str) -> object | None:
        return self._values.get(record_id)


def test_guidance_cell_currentness_rejects_a_trace_from_another_protocol() -> None:
    cell = guidance_cell(protocol=guidance_protocol(protocol_id="another-protocol"))
    trace = valid_trace(trace_id=cell.trace_id or "unexpected-missing-trace")
    retained = _LookupRepository(
        {
            cell.output_artifact_id or "unexpected-missing-output": SimpleNamespace(),
            cell.verifier_result_id or "unexpected-missing-verifier": SimpleNamespace(),
        }
    )

    current = _guidance_cell_evidence_is_current(
        cell,
        traces=_LookupRepository({trace.trace_id: trace}),
        rewards=_LookupRepository(
            {cell.reward_assessment_id or "unexpected-missing-reward": SimpleNamespace()}
        ),
        evidence=retained,
    )

    assert trace.observed_binding.protocol_id != cell.protocol_id
    assert current is False


def _matched_guidance_cell_evidence() -> tuple[
    GuidanceEvaluationCell,
    HarnessExecutionTrace,
    RewardValidityAssessment,
]:
    trace = valid_trace()
    assert trace.observed_binding.guidance_protocol is not None
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (),
        verifier_succeeded=True,
    )
    original = guidance_cell(protocol=trace.observed_binding.guidance_protocol)
    values = original.model_dump(mode="python", exclude={"content_hash"})
    values.update(
        output_artifact_id=trace.output_artifacts[0].artifact_id,
        trace_id=trace.trace_id,
        verifier_result_id=trace.verifier_result_id,
        reward_assessment_id=assessment.assessment_id,
    )
    return GuidanceEvaluationCell.build(**values), trace, assessment


def _guidance_evidence_current(
    cell: GuidanceEvaluationCell,
    trace: HarnessExecutionTrace,
    assessments: tuple[RewardValidityAssessment, ...],
) -> bool:
    evidence_ids = tuple(
        item for item in (cell.output_artifact_id, cell.verifier_result_id) if item is not None
    )
    return _guidance_cell_evidence_is_current(
        cell,
        traces=_LookupRepository({trace.trace_id: trace}),
        rewards=_LookupRepository({item.assessment_id: item for item in assessments}),
        evidence=_LookupRepository(
            {item: SimpleNamespace(content_hash="a" * 64) for item in evidence_ids}
        ),
    )


def test_guidance_cell_rejects_output_from_outside_its_trace() -> None:
    cell, trace, assessment = _matched_guidance_cell_evidence()
    values = cell.model_dump(mode="python", exclude={"content_hash"})
    values["output_artifact_id"] = "other-output"
    mismatched = GuidanceEvaluationCell.build(**values)

    assert _guidance_evidence_current(mismatched, trace, (assessment,)) is False


def test_guidance_cell_rejects_verifier_result_from_outside_its_trace() -> None:
    cell, trace, assessment = _matched_guidance_cell_evidence()
    values = cell.model_dump(mode="python", exclude={"content_hash"})
    values["verifier_result_id"] = "other-verifier-result"
    mismatched = GuidanceEvaluationCell.build(**values)

    assert _guidance_evidence_current(mismatched, trace, (assessment,)) is False


def test_guidance_cell_rejects_reward_assessment_for_another_trace() -> None:
    cell, trace, assessment = _matched_guidance_cell_evidence()
    other_trace = valid_trace(trace_id="other-trace")
    other_assessment = assess_reward_validity(
        other_trace.reward_observation,
        other_trace,
        (),
        verifier_succeeded=True,
    )
    values = cell.model_dump(mode="python", exclude={"content_hash"})
    values["reward_assessment_id"] = other_assessment.assessment_id
    mismatched = GuidanceEvaluationCell.build(**values)

    assert (
        _guidance_evidence_current(
            mismatched,
            trace,
            (assessment, other_assessment),
        )
        is False
    )


def test_guidance_cell_treats_a_missing_reward_record_as_stale_not_unmatched() -> None:
    cell, trace, _assessment = _matched_guidance_cell_evidence()
    missing_rewards = _LookupRepository({})

    matched = _guidance_cell_evidence_matches(
        cell,
        traces=_LookupRepository({trace.trace_id: trace}),
        rewards=missing_rewards,
    )
    current = _guidance_cell_evidence_is_current(
        cell,
        traces=_LookupRepository({trace.trace_id: trace}),
        rewards=missing_rewards,
        evidence=_LookupRepository(
            {
                cell.output_artifact_id or "unexpected-output": SimpleNamespace(),
                cell.verifier_result_id or "unexpected-verifier": SimpleNamespace(),
            }
        ),
    )

    assert matched is True
    assert current is False


def test_matrix_analysis_is_recomputed_from_current_complete_evidence() -> None:
    protocol = matrix_protocol(governing_policy_hash=_policy().policy_hash)
    cells = matrix_cells(protocol)
    analysis = matrix_analysis(protocol, cells)
    proposal = _proposal(
        RecordModelHarnessAnalysis,
        record_id="matrix-analysis",
        analysis=analysis,
    )
    capabilities = _Capabilities(matrix_protocol=protocol, matrix_cells=cells)

    accepted = _run(RecordModelHarnessAnalysisHandler(), proposal, capabilities)
    stale = _run(
        RecordModelHarnessAnalysisHandler(),
        proposal,
        _Capabilities(matrix_protocol=protocol, matrix_cells=cells, current=False),
    )
    forged = analysis.model_copy(update={"comparisons": ()})
    mismatch = _run(
        RecordModelHarnessAnalysisHandler(),
        proposal.model_copy(update={"analysis": forged}),
        _Capabilities(matrix_protocol=protocol, matrix_cells=cells),
    )

    assert accepted.accepted is True
    assert capabilities.projected == [analysis]
    assert stale.reasons[0].code is RejectionCode.STALE_REFERENCE
    assert mismatch.reasons[0].code is RejectionCode.DERIVATION_MISMATCH


def test_model_matrix_protocol_cells_bind_policy_budget_and_evidence() -> None:
    protocol = matrix_protocol(governing_policy_hash=_policy().policy_hash)
    protocol_proposal = _proposal(
        RecordModelHarnessProtocol,
        record_id="matrix-protocol",
        protocol=protocol,
    )
    assert _run(RecordModelHarnessProtocolHandler(), protocol_proposal, _Capabilities()).accepted

    cell = matrix_cells(protocol)[0]
    cell_proposal = _proposal(
        AppendModelHarnessCell,
        record_id="matrix-cell",
        cell=cell,
    )
    accepted = _run(
        AppendModelHarnessCellHandler(),
        cell_proposal,
        _Capabilities(matrix_protocol=protocol),
    )
    wrong_budget = cell.model_copy(
        update={
            "evaluation_budget": cell.evaluation_budget.model_copy(
                update={"token_limit": cell.evaluation_budget.token_limit + 1}
            )
        }
    )
    rejected = _run(
        AppendModelHarnessCellHandler(),
        cell_proposal.model_copy(update={"cell": wrong_budget}),
        _Capabilities(matrix_protocol=protocol),
    )

    assert accepted.accepted is True
    assert rejected.reasons[0].code is RejectionCode.UNMATCHED_EVALUATION


def test_trace_adapter_parses_untrusted_payload_and_handler_rejects_stale_runtime() -> None:
    trace = valid_trace()
    adapter = HarnessTraceProposalAdapter()
    proposal = adapter.from_untrusted_payload(
        trace.model_dump_json(),
        HarnessTraceRecordMetadata(received_at=NOW, source_id="runtime-harness"),
        "proposal-trace",
        "key-trace",
        _actor(),
    )

    accepted = _run(
        RecordHarnessExecutionTraceHandler(),
        proposal,
        _Capabilities(guidance_protocol=trace.observed_binding.guidance_protocol),
    )
    stale_trace = trace.model_copy(
        update={
            "observed_binding": trace.observed_binding.model_copy(
                update={
                    "model": trace.observed_binding.model.model_copy(
                        update={"model_version": "wrong-version"}
                    )
                }
            )
        }
    )
    stale_proposal = proposal.model_copy(
        update={"envelope": proposal.envelope.model_copy(update={"trace": stale_trace})}
    )
    stale = _run(
        RecordHarnessExecutionTraceHandler(),
        stale_proposal,
        _Capabilities(guidance_protocol=trace.observed_binding.guidance_protocol),
    )

    assert type(proposal) is RecordHarnessExecutionTrace
    assert accepted.accepted is True
    assert stale.reasons[0].code is RejectionCode.STALE_REFERENCE


def test_trace_handler_rejects_tools_outside_the_exact_protocol_budget() -> None:
    trace = valid_trace()
    proposal = HarnessTraceProposalAdapter().from_untrusted_payload(
        trace.model_dump_json(),
        HarnessTraceRecordMetadata(received_at=NOW, source_id="runtime-harness"),
        "proposal-budget-trace",
        "key-budget-trace",
        _actor(),
    )
    unauthorized_tool = trace.tool_observations[0].model_copy(update={"tool_id": "undeclared-tool"})
    copied_trace = trace.model_copy(update={"tool_observations": (unauthorized_tool,)})
    copied_proposal = proposal.model_copy(
        update={"envelope": proposal.envelope.model_copy(update={"trace": copied_trace})}
    )

    decision = _run(
        RecordHarnessExecutionTraceHandler(),
        copied_proposal,
        _Capabilities(guidance_protocol=trace.observed_binding.guidance_protocol),
    )

    assert decision.reasons[0].code is RejectionCode.UNMATCHED_BUDGETS


def _complete_trace_evidence(trace: HarnessExecutionTrace) -> dict[str, object]:
    binding = trace.observed_binding
    exact_hashes = {
        binding.task_id: binding.task_input_hash,
        binding.model.model_id: binding.model_hash,
        binding.harness.harness_id: binding.harness_hash,
        binding.procedure_id: binding.procedure_hash,
        binding.environment_id: binding.environment_hash,
        binding.context_id: binding.context_hash,
        binding.validator_id: binding.validator_hash,
        binding.checker_id: binding.checker_hash,
        binding.output_schema_id: binding.output_schema_hash,
        trace.verifier_result_id: trace.verifier_result_hash,
        trace.checker_result_id: trace.checker_result_hash,
        **dict(zip(binding.artifact_ids, binding.artifact_hashes, strict=True)),
        **{item.artifact_id: item.sha256 for item in trace.output_artifacts},
        **{
            item.evidence_id: item.response_hash.value
            for item in trace.tool_observations
            if item.response_hash.value is not None
        },
    }
    if trace.reward_observation is not None:
        exact_hashes[trace.reward_observation.observation_id] = (
            trace.reward_observation.content_hash
        )
    sampling_hash = trace.generation_metadata.sampling_parameters_hash
    if sampling_hash.evidence_id is not None and sampling_hash.value is not None:
        exact_hashes[sampling_hash.evidence_id] = sampling_hash.value
    retained = {
        record_id: SimpleNamespace(content_hash=content_hash)
        for record_id, content_hash in exact_hashes.items()
    }
    id_only = (
        "artifact-integrity",
        "boundary-monitor",
        "capture-validity-evidence",
        "compaction-evidence",
        "environment-event-0",
        "environment-event-1",
        "evaluator-run",
        "fixture-run",
        "protocol-receipt",
        "reserialization-evidence",
        "response-envelope",
        "reward-evidence",
        "usage-meter",
    )
    for record_id in id_only:
        retained.setdefault(record_id, SimpleNamespace(content_hash="a" * 64))
    return retained


@pytest.mark.parametrize(
    "missing_evidence_id",
    (
        "artifact-integrity",
        "boundary-monitor",
        "candidate-output",
        "capture-validity-evidence",
        "compaction-evidence",
        "environment-event-0",
        "environment-event-1",
        "evaluator-run",
        "fixture-run",
        "protocol-receipt",
        "request-envelope",
        "reserialization-evidence",
        "response-envelope",
        "reward-evidence",
        "reward-observation-1",
        "tool-call-1",
        "usage-meter",
    ),
)
def test_trace_currentness_requires_every_present_evidence_reference(
    missing_evidence_id: str,
) -> None:
    trace = valid_trace(with_transformations=True)
    retained = _complete_trace_evidence(trace)
    retained.pop(missing_evidence_id)
    capability = SimpleNamespace(evidence=_LookupRepository(retained))

    assert HarnessTraceCapabilities.trace_evidence_is_current(capability, trace) is False


def test_trace_currentness_accepts_complete_retained_evidence() -> None:
    trace = valid_trace(with_transformations=True)
    capability = SimpleNamespace(evidence=_LookupRepository(_complete_trace_evidence(trace)))

    assert HarnessTraceCapabilities.trace_evidence_is_current(capability, trace) is True


@pytest.mark.parametrize(
    "mismatched_evidence_id",
    (
        "candidate-output",
        "request-envelope",
        "reward-observation-1",
        "tool-call-1",
    ),
)
def test_trace_currentness_requires_exact_hashes_for_hash_bound_evidence(
    mismatched_evidence_id: str,
) -> None:
    trace = valid_trace(with_transformations=True)
    retained = _complete_trace_evidence(trace)
    retained[mismatched_evidence_id] = SimpleNamespace(content_hash="f" * 64)
    capability = SimpleNamespace(evidence=_LookupRepository(retained))

    assert HarnessTraceCapabilities.trace_evidence_is_current(capability, trace) is False


def test_evidence_receipt_resolution_requires_the_exact_schema_version() -> None:
    receipt = EvidenceReceipt(
        record_id="retained-evidence",
        schema_version=2,
        content_hash="a" * 64,
    )
    evidence = _LookupRepository(
        {"retained-evidence": SimpleNamespace(content_hash=receipt.content_hash)}
    )

    assert _receipt_is_current(receipt, evidence=evidence) is False


def test_invalid_reward_is_retained_but_excluded_from_positive_evidence() -> None:
    trace = valid_trace(observed_binding_updates={"harness_hash": "d" * 64})
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (),
        verifier_succeeded=True,
    )
    assert trace.reward_observation is not None
    proposal = _proposal(
        RecordRewardAssessment,
        record_id="reward-assessment",
        observation=trace.reward_observation,
        findings=assessment.findings,
        assessment=assessment,
    )
    capabilities = _Capabilities(trace=trace)

    decision = _run(RecordRewardAssessmentHandler(), proposal, capabilities)

    assert decision.accepted is True
    assert assessment.status is RewardValidityStatus.INVALID
    assert capabilities.projected == [assessment]
    assert valid_reward_evidence((assessment,)) == ()


def test_reward_rejects_stale_resolution_and_claimed_derivation() -> None:
    trace = valid_trace()
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (),
        verifier_succeeded=True,
    )
    assert trace.reward_observation is not None
    proposal = _proposal(
        RecordRewardAssessment,
        record_id="reward-current",
        observation=trace.reward_observation,
        findings=assessment.findings,
        assessment=assessment,
    )
    stale = _run(
        RecordRewardAssessmentHandler(),
        proposal,
        _Capabilities(trace=trace, current=False),
    )
    forged_assessment = assessment.model_copy(update={"status": RewardValidityStatus.INVALID})
    forged = proposal.model_copy(update={"assessment": forged_assessment})
    mismatch = _run(
        RecordRewardAssessmentHandler(),
        forged,
        _Capabilities(trace=trace),
    )

    assert stale.reasons[0].code is RejectionCode.STALE_REFERENCE
    assert mismatch.reasons[0].code is RejectionCode.INVALID_REWARD


def test_capability_persists_only_its_exact_accepted_proposal(
    extension_engine: Engine,
) -> None:
    protocol = guidance_protocol()
    proposal = _proposal(
        RecordGuidanceEvaluationProtocol,
        record_id="persisted-guidance-protocol",
        protocol=protocol,
    )
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    with DatabaseUnitOfWork(extension_engine) as unit_of_work:
        assert unit_of_work.connection is not None
        capabilities = harness_extension_capabilities(
            proposal,
            unit_of_work.connection,
            _policy(),
            current_transaction_created_at=NOW,
        )
        capabilities.append_authoritative(protocol)
        _persist_accepted_provenance(unit_of_work, proposal, decision)
        with pytest.raises(RuntimeError, match="no mutable projection"):
            capabilities.update_projection(protocol)

    with DatabaseUnitOfWork(extension_engine) as unit_of_work:
        assert unit_of_work.connection is not None
        assert (
            GuidanceEvaluationProtocolRepository(unit_of_work.connection).get(protocol.protocol_id)
            == protocol
        )


def test_capability_write_rolls_back_atomically_with_failed_transaction(
    extension_engine: Engine,
) -> None:
    protocol = guidance_protocol(protocol_id="rollback-guidance-protocol")
    proposal = _proposal(
        RecordGuidanceEvaluationProtocol,
        record_id="rollback-guidance-protocol",
        protocol=protocol,
    )
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)

    with (
        pytest.raises(RuntimeError, match="abort extension transaction"),
        DatabaseUnitOfWork(extension_engine) as unit_of_work,
    ):
        assert unit_of_work.connection is not None
        capabilities = harness_extension_capabilities(
            proposal,
            unit_of_work.connection,
            _policy(),
            current_transaction_created_at=NOW,
        )
        capabilities.append_authoritative(protocol)
        _persist_accepted_provenance(unit_of_work, proposal, decision)
        raise RuntimeError("abort extension transaction")

    with DatabaseUnitOfWork(extension_engine) as unit_of_work:
        assert unit_of_work.connection is not None
        assert (
            GuidanceEvaluationProtocolRepository(unit_of_work.connection).get(protocol.protocol_id)
            is None
        )


def test_maximum_matrix_resolution_bulk_loads_each_evidence_repository_once() -> None:
    models = tuple(
        ModelIdentity(model_id=f"model-{index:02d}", model_version="v1") for index in range(16)
    )
    harnesses = tuple(
        HarnessIdentity(harness_id=f"harness-{index:02d}", harness_version="v1")
        for index in range(8)
    )
    grid = matrix_grid(models=models, harnesses=harnesses)
    protocol = matrix_protocol(
        models=models,
        harnesses=harnesses,
        expected_grid=grid,
        model_budgets=tuple(
            ModelBudgetBinding.build(model=model, budget=matrix_budget(model)) for model in models
        ),
    )
    fixtures = tuple(
        _matrix_evidence_chain(protocol, coordinate, index)
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    cells = tuple(
        ModelHarnessCell.from_protocol(
            cell_id=f"bulk-cell-{index:03d}",
            protocol=protocol,
            coordinate=coordinate,
            metrics=matrix_metrics(),
            evidence_chain_receipt=harness_cell_evidence_chain_receipt(fixtures[index].chain),
            observed_at=NOW,
        )
        for index, coordinate in enumerate(protocol.expected_grid)
    )
    calls = {"protocols": 0, "cells": 0, "traces": 0, "rewards": 0}

    class Protocols:
        def get(self, protocol_id: str) -> ModelHarnessProtocol | None:
            calls["protocols"] += 1
            return protocol if protocol_id == protocol.protocol_id else None

    class Cells:
        def list_for_protocol(self, protocol_id: str) -> tuple[ModelHarnessCell, ...]:
            calls["cells"] += 1
            assert protocol_id == protocol.protocol_id
            return cells

    class Traces:
        def list_for_protocol(self, protocol_id: str) -> tuple[HarnessExecutionTrace, ...]:
            calls["traces"] += 1
            assert protocol_id == protocol.protocol_id
            return tuple(item.trace for item in fixtures)

    class Rewards:
        def list_for_traces(
            self,
            trace_ids: tuple[str, ...],
        ) -> tuple[RewardValidityAssessment, ...]:
            calls["rewards"] += 1
            assert set(trace_ids) == {item.trace.trace_id for item in fixtures}
            return tuple(item.assessment for item in fixtures)

        def list_for_trace(self, trace_id: str) -> tuple[RewardValidityAssessment, ...]:
            raise AssertionError(f"per-cell reward lookup attempted for {trace_id}")

    capabilities = ModelHarnessAnalysisCapabilities(
        active_policy=_policy(),
        proposal=object(),  # type: ignore[arg-type]
        protocols=Protocols(),  # type: ignore[arg-type]
        cells=Cells(),  # type: ignore[arg-type]
        analyses=object(),  # type: ignore[arg-type]
        traces=Traces(),  # type: ignore[arg-type]
        rewards=Rewards(),  # type: ignore[arg-type]
        created_at=NOW,
    )

    resolved = capabilities.resolve_model_harness_evidence(protocol.protocol_id)

    assert resolved is not None
    assert len(resolved[0]) == 256
    assert calls == {"protocols": 1, "cells": 1, "traces": 1, "rewards": 1}
