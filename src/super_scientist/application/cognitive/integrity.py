from __future__ import annotations

from collections import defaultdict

from super_scientist.application.collaboration.service import (
    CollaborationHistoryRecord,
    rebuild_collaboration_state,
)
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CohortPlan,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.collaboration import (
    CollaborationSession,
    evaluate_termination,
)
from super_scientist.domain.harness_eval.evidence_chains import (
    HarnessCellEvidenceChain,
    HarnessEvidenceSnapshotIndex,
    HarnessEvidenceSnapshotRecord,
    harness_cell_evidence_chain_receipt,
    project_harness_evidence_snapshots,
)
from super_scientist.domain.harness_eval.matrix import (
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessProtocol,
    analyze_model_harness,
)
from super_scientist.domain.harness_eval.rewards import (
    RewardValidityAssessment,
    assess_reward_validity,
)
from super_scientist.domain.harness_eval.traces import HarnessExecutionTrace, trace_freshness
from super_scientist.domain.procedures import (
    ProcedureCompilationRecord,
    compile_method,
    procedure_to_progress_plan,
)
from super_scientist.kernel.transactions.models import (
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    AppendPeerContribution,
    AppendPeerRequest,
    AppendTopologyEvent,
    BindCompiledProgressPlan,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordCollaborationSession,
    RecordCollaborationTermination,
    RecordDiversityAssessment,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordMethodDirectionOutcome,
    RecordModelHarnessAnalysis,
    RecordModelHarnessProtocol,
    RecordProcedureCompilation,
    RecordRewardAssessment,
)
from super_scientist.providers.storage.integrity_records import (
    CognitiveIntegritySnapshot,
    EvaluationExtensionIntegritySnapshot,
)
from super_scientist.providers.storage.repositories import StoredTransaction


def _accepted(transactions: tuple[StoredTransaction, ...]) -> tuple[StoredTransaction, ...]:
    return tuple(item for item in transactions if item.decision.accepted)


def _profile_is_canonical(profile: CapabilityProfile) -> bool:
    try:
        return (
            CapabilityProfile.build(
                **profile.model_dump(mode="python", exclude={"content_hash"}, warnings=False)
            )
            == profile
        )
    except (MemoryError, OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        return False


def _compilation_record(
    proposal: RecordProcedureCompilation,
) -> ProcedureCompilationRecord:
    record = ProcedureCompilationRecord.build_from_untrusted_envelope(proposal.compilation)
    request = record.result.parse_request()
    if compile_method(request) != record.result:
        raise ValueError("procedure compilation does not match deterministic recomputation")
    return record


def expected_cognitive_snapshot(
    transactions: tuple[StoredTransaction, ...],
) -> CognitiveIntegritySnapshot:
    accepted = _accepted(transactions)
    profiles: list[CapabilityProfile] = []
    profile_transactions: dict[str, tuple[str, CapabilityProfile]] = {}
    cohorts = []
    cohort_transactions: dict[str, tuple[str, CohortPlan]] = {}
    diversity = []
    sessions: list[CollaborationSession] = []
    requests = []
    contributions = []
    topology = []
    terminations = []
    compilations: list[ProcedureCompilationRecord] = []
    compilation_by_id: dict[str, ProcedureCompilationRecord] = {}
    outcomes = []
    bindings = []
    collaboration_history: dict[str, list[CollaborationHistoryRecord]] = defaultdict(list)

    for transaction in accepted:
        proposal = transaction.proposal
        if isinstance(proposal, RecordCapabilityProfile):
            if not _profile_is_canonical(proposal.profile):
                raise ValueError("capability profile does not match canonical reconstruction")
            profiles.append(proposal.profile)
            profile_transactions[proposal.proposal_id] = (
                transaction.proposal_hash,
                proposal.profile,
            )
        elif isinstance(proposal, RecordCohortPlan):
            resolved_profiles = []
            for receipt in proposal.profile_receipts:
                resolved = profile_transactions.get(receipt.proposal_id)
                if resolved is None or resolved[0] != receipt.proposal_hash:
                    raise ValueError("cohort plan profile receipt is unavailable")
                resolved_profiles.append(resolved[1])
            if build_cohort(proposal.request, tuple(resolved_profiles)) != proposal.plan:
                raise ValueError("cohort plan does not match deterministic recomputation")
            cohorts.append(proposal.plan)
            cohort_transactions[proposal.proposal_id] = (
                transaction.proposal_hash,
                proposal.plan,
            )
        elif isinstance(proposal, RecordDiversityAssessment):
            resolved_cohort = cohort_transactions.get(proposal.cohort_plan_receipt.proposal_id)
            if (
                resolved_cohort is None
                or resolved_cohort[0] != proposal.cohort_plan_receipt.proposal_hash
            ):
                raise ValueError("diversity cohort receipt is unavailable")
            resolved_profiles = []
            for receipt in proposal.profile_receipts:
                resolved = profile_transactions.get(receipt.proposal_id)
                if resolved is None or resolved[0] != receipt.proposal_hash:
                    raise ValueError("diversity profile receipt is unavailable")
                resolved_profiles.append(resolved[1])
            expected = assess_diversity(
                resolved_cohort[1],
                tuple(resolved_profiles),
                proposal.error_correlations,
            )
            if expected != proposal.assessment:
                raise ValueError("diversity assessment does not match deterministic recomputation")
            diversity.append(proposal.assessment)
        elif isinstance(proposal, RecordCollaborationSession):
            try:
                rebuilt_session = CollaborationSession.build(
                    **proposal.session.model_dump(
                        mode="python", exclude={"content_hash"}, warnings=False
                    )
                )
            except (
                MemoryError,
                OverflowError,
                RecursionError,
                TypeError,
                UnicodeError,
                ValueError,
            ):
                rebuilt_session = None
            if rebuilt_session != proposal.session:
                raise ValueError("collaboration session does not match canonical reconstruction")
            sessions.append(proposal.session)
        elif isinstance(proposal, AppendPeerRequest):
            collaboration_history[proposal.request.session_id].append(proposal.request)
            requests.append(proposal.request)
        elif isinstance(proposal, AppendPeerContribution):
            collaboration_history[proposal.contribution.session_id].append(proposal)
            contributions.append(proposal.contribution)
        elif isinstance(proposal, AppendTopologyEvent):
            collaboration_history[proposal.event.session_id].append(proposal.event)
            topology.append(proposal.event)
        elif isinstance(proposal, RecordCollaborationTermination):
            session = next(
                (item for item in sessions if item.session_id == proposal.session_id),
                None,
            )
            rebuilt = (
                None
                if session is None
                else rebuild_collaboration_state(
                    session,
                    tuple(collaboration_history[proposal.session_id]),
                )
            )
            if rebuilt is None or evaluate_termination(rebuilt.state) != proposal.termination:
                raise ValueError("collaboration termination does not match recomputed state")
            terminations.append((proposal.session_id, proposal.termination))
        elif isinstance(proposal, RecordProcedureCompilation):
            compilation = _compilation_record(proposal)
            compilations.append(compilation)
            compilation_by_id[compilation.compilation_id] = compilation
        elif isinstance(proposal, RecordMethodDirectionOutcome):
            if proposal.compilation_id not in compilation_by_id:
                raise ValueError("method outcome compilation is unavailable")
            outcomes.append(proposal.outcome)
        elif isinstance(proposal, BindCompiledProgressPlan):
            retained_compilation = compilation_by_id.get(proposal.binding.compilation_id)
            if (
                retained_compilation is None
                or retained_compilation.content_hash != proposal.binding.compilation_hash
            ):
                raise ValueError("compiled progress binding compilation is unavailable")
            expected_plan = procedure_to_progress_plan(
                retained_compilation.result,
                run_id=proposal.plan.run_id,
                plan_version_id=proposal.plan.plan_version_id,
                version=proposal.plan.version,
                created_at=proposal.plan.created_at,
                governing_policy_hash=proposal.binding.governing_policy_hash,
            )
            if expected_plan != proposal.plan or proposal.binding.plan != proposal.plan:
                raise ValueError("compiled progress plan does not match deterministic mapping")
            bindings.append(proposal.binding)

    for session in sessions:
        if (
            rebuild_collaboration_state(
                session,
                tuple(collaboration_history[session.session_id]),
            )
            is None
        ):
            raise ValueError("collaboration history does not reconstruct")

    return CognitiveIntegritySnapshot(
        capability_profiles=tuple(sorted(profiles, key=lambda item: item.profile_id)),
        cohort_plans=tuple(sorted(cohorts, key=lambda item: item.cohort_plan_id)),
        diversity_assessments=tuple(
            sorted(diversity, key=lambda item: item.diversity_assessment_id)
        ),
        collaboration_sessions=tuple(sorted(sessions, key=lambda item: item.session_id)),
        peer_requests=tuple(sorted(requests, key=lambda item: item.request_id)),
        peer_contributions=tuple(sorted(contributions, key=lambda item: item.contribution_id)),
        topology_events=tuple(sorted(topology, key=lambda item: item.event_id)),
        terminations=tuple(item[1] for item in sorted(terminations, key=lambda item: item[0])),
        compilations=tuple(sorted(compilations, key=lambda item: item.compilation_id)),
        method_outcomes=tuple(sorted(outcomes, key=lambda item: item.outcome_id)),
        bindings=tuple(sorted(bindings, key=lambda item: item.binding_id)),
    )


def _coordinate_key(value: ModelHarnessCell) -> tuple[object, ...]:
    coordinate = value.coordinate
    return (
        coordinate.model,
        coordinate.harness,
        coordinate.partition,
    )


def _binding_coordinate_key(trace: HarnessExecutionTrace) -> tuple[object, ...]:
    binding = trace.observed_binding
    return (binding.model, binding.harness, binding.partition)


def _recomputed_model_analysis(
    analysis: ModelHarnessAnalysis,
    protocols: tuple[ModelHarnessProtocol, ...],
    cells: tuple[ModelHarnessCell, ...],
    traces: tuple[HarnessExecutionTrace, ...],
    rewards: tuple[RewardValidityAssessment, ...],
) -> ModelHarnessAnalysis:
    protocol = next(
        (item for item in protocols if item.protocol_id == analysis.protocol_id),
        None,
    )
    if protocol is None:
        raise ValueError("model-harness analysis protocol is unavailable")
    selected_cells = tuple(item for item in cells if item.protocol_id == analysis.protocol_id)
    chains: list[HarnessCellEvidenceChain] = []
    snapshots: list[HarnessEvidenceSnapshotRecord] = []
    for cell in selected_cells:
        matches: list[tuple[HarnessCellEvidenceChain, HarnessEvidenceSnapshotRecord]] = []
        for trace in traces:
            if (
                trace.observed_binding.protocol_id != analysis.protocol_id
                or _binding_coordinate_key(trace) != _coordinate_key(cell)
            ):
                continue
            for assessment in rewards:
                if assessment.trace_id != trace.trace_id:
                    continue
                pair = project_harness_evidence_snapshots(
                    protocol=protocol,
                    coordinate=cell.coordinate,
                    trace=trace,
                    freshness=assessment.freshness,
                    assessment=assessment,
                )
                if harness_cell_evidence_chain_receipt(pair[0]) == cell.evidence_chain_receipt:
                    matches.append(pair)
        if len(matches) != 1:
            raise ValueError("model-harness cell evidence chain is unavailable or ambiguous")
        chains.append(matches[0][0])
        snapshots.append(matches[0][1])
    index = HarnessEvidenceSnapshotIndex.build(
        records=tuple(sorted(snapshots, key=lambda item: item.chain_receipt.record_id))
    )
    return analyze_model_harness(
        protocol,
        selected_cells,
        evidence_chains=tuple(chains),
        evidence_index=index,
    )


def expected_evaluation_extension_snapshot(
    transactions: tuple[StoredTransaction, ...],
) -> EvaluationExtensionIntegritySnapshot:
    accepted = _accepted(transactions)
    guidance_protocols = tuple(
        item.proposal.protocol
        for item in accepted
        if isinstance(item.proposal, RecordGuidanceEvaluationProtocol)
    )
    guidance_cells = tuple(
        item.proposal.cell
        for item in accepted
        if isinstance(item.proposal, AppendGuidanceEvaluationCell)
    )
    model_protocols = tuple(
        item.proposal.protocol
        for item in accepted
        if isinstance(item.proposal, RecordModelHarnessProtocol)
    )
    model_cells = tuple(
        item.proposal.cell for item in accepted if isinstance(item.proposal, AppendModelHarnessCell)
    )
    traces = tuple(
        item.proposal.envelope.trace
        for item in accepted
        if isinstance(item.proposal, RecordHarnessExecutionTrace)
    )
    rewards = tuple(
        item.proposal.assessment
        for item in accepted
        if isinstance(item.proposal, RecordRewardAssessment)
    )

    protocols_by_id = {item.protocol_id: item for item in model_protocols}
    for cell in model_cells:
        protocol = protocols_by_id.get(cell.protocol_id)
        if protocol is None:
            raise ValueError("model-harness cell protocol is unavailable")
        expected_cell = ModelHarnessCell.from_protocol(
            cell_id=cell.cell_id,
            protocol=protocol,
            coordinate=cell.coordinate,
            metrics=cell.metrics,
            evidence_chain_receipt=cell.evidence_chain_receipt,
            observed_at=cell.observed_at,
        )
        if expected_cell != cell:
            raise ValueError("model-harness cell does not match deterministic reconstruction")

    for transaction in accepted:
        proposal = transaction.proposal
        if not isinstance(proposal, RecordRewardAssessment):
            continue
        assessment = proposal.assessment
        trace = next((item for item in traces if item.trace_id == assessment.trace_id), None)
        if trace is None:
            raise ValueError("reward assessment trace is unavailable")
        freshness = trace_freshness(
            assessment.expectation,
            trace,
            inventory=assessment.evidence_inventory,
        )
        expected_assessment = assess_reward_validity(
            proposal.observation,
            trace,
            proposal.findings,
            expectation=assessment.expectation,
            verification=assessment.verification,
            diagnostic_coverage=assessment.diagnostic_coverage,
            inventory=assessment.evidence_inventory,
        )
        if freshness != assessment.freshness or expected_assessment != assessment:
            raise ValueError("reward assessment does not match deterministic reconstruction")

    analyses = []
    for transaction in accepted:
        proposal = transaction.proposal
        if not isinstance(proposal, RecordModelHarnessAnalysis):
            continue
        expected_analysis = _recomputed_model_analysis(
            proposal.analysis,
            model_protocols,
            model_cells,
            traces,
            rewards,
        )
        if expected_analysis != proposal.analysis:
            raise ValueError("model-harness analysis does not match deterministic reconstruction")
        analyses.append(proposal.analysis)

    return EvaluationExtensionIntegritySnapshot(
        guidance_protocols=tuple(sorted(guidance_protocols, key=lambda item: item.protocol_id)),
        guidance_cells=tuple(sorted(guidance_cells, key=lambda item: item.cell_id)),
        model_harness_protocols=tuple(sorted(model_protocols, key=lambda item: item.protocol_id)),
        model_harness_cells=tuple(sorted(model_cells, key=lambda item: item.cell_id)),
        model_harness_analyses=tuple(sorted(analyses, key=lambda item: item.protocol_id)),
        harness_execution_traces=tuple(sorted(traces, key=lambda item: item.trace_id)),
        reward_assessments=tuple(sorted(rewards, key=lambda item: item.assessment_id)),
    )


__all__ = [
    "expected_cognitive_snapshot",
    "expected_evaluation_extension_snapshot",
]
