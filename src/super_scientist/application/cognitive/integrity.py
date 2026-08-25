from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import cast

from super_scientist.application.collaboration.service import (
    CollaborationHistoryRecord,
    rebuild_collaboration_state,
)
from super_scientist.application.harness_eval.extensions import (
    _trace_matches_guidance,
    _trace_matches_matrix,
    _trace_within_budget,
)
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CohortPlan,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.cognition.grounding import assess_capability
from super_scientist.domain.collaboration import (
    CollaborationSession,
    evaluate_termination,
    next_peer,
)
from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.harness_eval.evidence_chains import (
    HarnessCellEvidenceChain,
    HarnessEvidenceSnapshotIndex,
    HarnessEvidenceSnapshotRecord,
    harness_cell_evidence_chain_receipt,
    project_harness_evidence_snapshots,
)
from super_scientist.domain.harness_eval.guidance import (
    GuidanceEvaluationCell,
    GuidanceEvaluationProtocol,
)
from super_scientist.domain.harness_eval.matrix import (
    ModelHarnessAnalysis,
    ModelHarnessCell,
    ModelHarnessProtocol,
    analyze_model_harness,
)
from super_scientist.domain.harness_eval.receipts import EvidenceReceipt
from super_scientist.domain.harness_eval.rewards import (
    RewardValidityAssessment,
    assess_reward_validity,
    reward_validity_receipt,
)
from super_scientist.domain.harness_eval.traces import (
    HarnessExecutionTrace,
    trace_freshness,
    trace_freshness_receipt,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.procedures import (
    AcceptedSourceReceiptRef,
    OpaqueProcedureCompilationEnvelope,
    ProcedureCompilationRecord,
    ProcedureEvidenceSourceKind,
    compile_method,
    procedure_to_progress_plan,
)
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
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
    RecordRunBudget,
    TransactionDecision,
    _governed_proposal_state_is_safe,
    expected_hash_verified_evidence,
    parse_untrusted_proposal_json,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.integrity_records import (
    CognitiveIntegritySnapshot,
    EvaluationExtensionIntegritySnapshot,
)
from super_scientist.providers.storage.procedure_sources import ProcedureSourceSnapshot
from super_scientist.providers.storage.repositories import StoredTransaction


def _accepted(transactions: tuple[StoredTransaction, ...]) -> tuple[StoredTransaction, ...]:
    return tuple(item for item in transactions if item.decision.accepted)


@dataclass(frozen=True, slots=True)
class _AcceptedBinding:
    transaction: StoredTransaction
    event: AuditEvent
    governing_policy_hash: str


@dataclass(frozen=True, slots=True)
class _SourceSnapshotBinding:
    snapshot_family_id: str
    snapshot_id: str
    artifact_hash: str
    audit_sequence: int
    source_bindings: frozenset[tuple[str, str]] | None


def _accepted_bindings(
    transactions: tuple[StoredTransaction, ...],
    events: tuple[AuditEvent, ...],
) -> dict[str, _AcceptedBinding]:
    transactions_by_id = {
        item.proposal.proposal_id: item for item in transactions if item.decision.accepted
    }
    bindings: dict[str, _AcceptedBinding] = {}
    for event in events:
        payload = json_compatible_payload(event.payload)
        if payload.get("transaction_persisted") is not True:
            continue
        try:
            proposal = parse_untrusted_proposal_json(canonical_json_bytes(payload["proposal"]))
            decision = TransactionDecision.model_validate_json(
                canonical_json_bytes(payload["decision"]),
                strict=True,
            )
            policy_hash = payload["policy_hash"]
        except (KeyError, MemoryError, OverflowError, RecursionError, TypeError, ValueError):
            continue
        transaction = transactions_by_id.get(proposal.proposal_id)
        if (
            transaction is None
            or transaction.proposal != proposal
            or transaction.decision != decision
            or type(policy_hash) is not str
            or proposal.proposal_id in bindings
        ):
            continue
        bindings[proposal.proposal_id] = _AcceptedBinding(transaction, event, policy_hash)
    return bindings


def _source_snapshot_bindings(
    bindings: dict[str, _AcceptedBinding],
    artifact_store: ArtifactStore | None,
    declared_snapshot_keys: frozenset[tuple[str, str]],
) -> tuple[_SourceSnapshotBinding, ...]:
    snapshots: list[_SourceSnapshotBinding] = []
    for binding in bindings.values():
        proposal = binding.transaction.proposal
        if type(proposal) is not AddEvidence:
            continue
        metadata = json_compatible_payload(binding.event.payload).get("procedure_source_snapshot")
        if type(metadata) is not dict or set(metadata) != {
            "schema_version",
            "snapshot_family_id",
            "snapshot_id",
            "evidence_id",
            "artifact_hash",
        }:
            continue
        try:
            evidence = expected_hash_verified_evidence(proposal)
            schema_version = metadata["schema_version"]
            snapshot_family_id = metadata["snapshot_family_id"]
            snapshot_id = metadata["snapshot_id"]
            evidence_id = metadata["evidence_id"]
            artifact_hash = metadata["artifact_hash"]
        except (KeyError, MemoryError, OverflowError, RecursionError, TypeError, ValueError):
            continue
        if (
            schema_version != 1
            or type(snapshot_family_id) is not str
            or type(snapshot_id) is not str
            or type(evidence_id) is not str
            or type(artifact_hash) is not str
            or snapshot_id != evidence.evidence_id
            or evidence_id != evidence.evidence_id
            or artifact_hash != evidence.content_hash
        ):
            continue
        decoded_bindings: frozenset[tuple[str, str]] | None = None
        if artifact_store is not None and (snapshot_id, artifact_hash) in declared_snapshot_keys:
            try:
                artifact_bytes = artifact_store.read(evidence.artifact)
                snapshot = ProcedureSourceSnapshot.model_validate_json(
                    artifact_bytes,
                    strict=True,
                )
                if (
                    len(artifact_bytes) != evidence.artifact.size_bytes
                    or sha256_hex(artifact_bytes) != artifact_hash
                    or canonical_json_bytes(snapshot.model_dump(mode="json")) != artifact_bytes
                    or snapshot.snapshot_family_id != snapshot_family_id
                    or snapshot.snapshot_id != snapshot_id
                ):
                    continue
                decoded_bindings = frozenset(
                    (item.source_record_id, item.source_content_hash)
                    for item in snapshot.source_bindings
                )
            except (
                MemoryError,
                OSError,
                OverflowError,
                RecursionError,
                TypeError,
                UnicodeError,
                ValueError,
            ):
                continue
        snapshots.append(
            _SourceSnapshotBinding(
                snapshot_family_id=snapshot_family_id,
                snapshot_id=snapshot_id,
                artifact_hash=artifact_hash,
                audit_sequence=binding.event.sequence,
                source_bindings=decoded_bindings,
            )
        )
    return tuple(snapshots)


def _source_snapshot_is_current(
    snapshot_id: str,
    snapshot_hash: str,
    *,
    snapshots: tuple[_SourceSnapshotBinding, ...],
    current_sequence: int,
    required_source: tuple[str, str] | None,
) -> bool:
    prior = tuple(item for item in snapshots if item.audit_sequence < current_sequence)
    targets = tuple(
        item
        for item in prior
        if item.snapshot_id == snapshot_id and item.artifact_hash == snapshot_hash
    )
    if len(targets) != 1:
        return False
    target = targets[0]
    family = tuple(item for item in prior if item.snapshot_family_id == target.snapshot_family_id)
    greatest_sequence = max(item.audit_sequence for item in family)
    greatest = tuple(item for item in family if item.audit_sequence == greatest_sequence)
    return (
        len(greatest) == 1
        and greatest[0] == target
        and (
            required_source is None
            or target.source_bindings is None
            or required_source in target.source_bindings
        )
    )


def _receipt_binding(
    *,
    proposal_id: str,
    proposal_hash: str,
    audit_event_id: str,
    audit_event_hash: str,
    expected_type: type[object],
    bindings: dict[str, _AcceptedBinding],
    current_sequence: int,
    governing_policy_hash: str | None,
) -> StoredTransaction | None:
    binding = bindings.get(proposal_id)
    if binding is None:
        return None
    transaction = binding.transaction
    return (
        transaction
        if type(transaction.proposal) is expected_type
        and transaction.proposal_hash == proposal_hash
        and binding.event.event_id == audit_event_id
        and binding.event.event_hash == audit_event_hash
        and binding.event.sequence < current_sequence
        and (
            governing_policy_hash is None or binding.governing_policy_hash == governing_policy_hash
        )
        else None
    )


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
    if not _governed_proposal_state_is_safe(proposal, RecordProcedureCompilation):
        raise ValueError("procedure compilation proposal state is unsafe")
    state = object.__getattribute__(proposal, "__dict__")
    envelope = state.get("compilation")
    if type(envelope) is not OpaqueProcedureCompilationEnvelope:
        raise ValueError("procedure compilation envelope must have its exact declared type")
    try:
        record = ProcedureCompilationRecord.build_from_untrusted_envelope(envelope)
        request = record.result.parse_request()
        if compile_method(request) != record.result:
            raise ValueError("procedure compilation does not match deterministic recomputation")
    except (AssertionError, MemoryError, OverflowError, RecursionError, TypeError, ValueError):
        raise ValueError("procedure compilation failed closed reconstruction") from None
    return record


def _source_receipt_transaction(
    receipt: AcceptedSourceReceiptRef,
    *,
    bindings: dict[str, _AcceptedBinding],
    current_sequence: int,
) -> StoredTransaction | None:
    expected_type: type[object] = (
        RecordCapabilityProfile
        if receipt.source_kind is ProcedureEvidenceSourceKind.CAPABILITY_PROFILE
        else AddEvidence
    )
    transaction = _receipt_binding(
        proposal_id=receipt.proposal_id,
        proposal_hash=receipt.proposal_hash,
        audit_event_id=receipt.audit_event_id,
        audit_event_hash=receipt.audit_event_hash,
        expected_type=expected_type,
        bindings=bindings,
        current_sequence=current_sequence,
        governing_policy_hash=None,
    )
    if transaction is None:
        return None
    proposal = transaction.proposal
    if type(proposal) is RecordCapabilityProfile:
        profile = proposal.profile
        return (
            transaction
            if receipt.source_record_id == profile.profile_id
            and receipt.source_schema_version == profile.schema_version
            and receipt.source_content_hash == profile.content_hash
            else None
        )
    evidence = expected_hash_verified_evidence(cast(AddEvidence, proposal))
    return (
        transaction
        if receipt.source_record_id == evidence.evidence_id
        and receipt.source_schema_version == 1
        and receipt.source_content_hash == evidence.content_hash
        else None
    )


def _require_compilation_source_receipts(
    compilation: ProcedureCompilationRecord,
    *,
    bindings: dict[str, _AcceptedBinding],
    current_sequence: int,
    available_evidence: dict[tuple[str, int, str], int],
    source_snapshots: tuple[_SourceSnapshotBinding, ...],
) -> None:
    request = compilation.result.parse_request()
    for grounded in request.capability_assessments:
        transaction = _source_receipt_transaction(
            grounded.profile_receipt,
            bindings=bindings,
            current_sequence=current_sequence,
        )
        if transaction is None:
            raise ValueError("procedure capability source is not exact prior history")
        source_proposal = transaction.proposal
        if (
            type(source_proposal) is not RecordCapabilityProfile
            or source_proposal.profile != grounded.profile
            or assess_capability(
                source_proposal.profile,
                grounded.assessment.requirement,
            )
            != grounded.assessment
        ):
            raise ValueError("procedure capability source does not recompute exactly")
    receipts = (
        *(item.profile_receipt for item in request.capability_assessments),
        request.artifact_catalog_receipt,
        request.tool_catalog_receipt,
        request.validator_catalog_receipt,
    )
    if any(
        _source_receipt_transaction(
            receipt,
            bindings=bindings,
            current_sequence=current_sequence,
        )
        is None
        or available_evidence.get(
            (
                receipt.source_snapshot_id,
                1,
                receipt.source_snapshot_hash,
            ),
            current_sequence,
        )
        >= current_sequence
        or not _source_snapshot_is_current(
            receipt.source_snapshot_id,
            receipt.source_snapshot_hash,
            snapshots=source_snapshots,
            current_sequence=current_sequence,
            required_source=(
                None
                if receipt.source_kind is ProcedureEvidenceSourceKind.CAPABILITY_PROFILE
                else (receipt.source_record_id, receipt.source_content_hash)
            ),
        )
        for receipt in receipts
    ):
        raise ValueError("procedure compilation source receipt is not exact prior accepted history")


def _declared_compilation_snapshot_keys(
    transactions: tuple[StoredTransaction, ...],
) -> frozenset[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for transaction in transactions:
        proposal = transaction.proposal
        if type(proposal) is not RecordProcedureCompilation:
            continue
        request = _compilation_record(proposal).result.parse_request()
        for receipt in (
            request.artifact_catalog_receipt,
            request.tool_catalog_receipt,
            request.validator_catalog_receipt,
        ):
            keys.add((receipt.source_snapshot_id, receipt.source_snapshot_hash))
    return frozenset(keys)


def expected_cognitive_snapshot(
    transactions: tuple[StoredTransaction, ...],
    events: tuple[AuditEvent, ...] = (),
    artifact_store: ArtifactStore | None = None,
) -> CognitiveIntegritySnapshot:
    accepted = _accepted(transactions)
    bindings_by_id = _accepted_bindings(transactions, events)
    source_snapshots = _source_snapshot_bindings(
        bindings_by_id,
        artifact_store,
        _declared_compilation_snapshot_keys(accepted),
    )
    profiles: list[CapabilityProfile] = []
    profile_transactions: dict[str, tuple[str, CapabilityProfile]] = {}
    cohorts = []
    cohort_transactions: dict[str, tuple[str, CohortPlan]] = {}
    cohorts_by_id: dict[str, CohortPlan] = {}
    diversity = []
    sessions: list[CollaborationSession] = []
    sessions_by_id: dict[str, CollaborationSession] = {}
    requests = []
    contributions = []
    topology = []
    terminations = []
    compilations: list[ProcedureCompilationRecord] = []
    compilation_by_id: dict[str, ProcedureCompilationRecord] = {}
    outcomes = []
    bindings = []
    collaboration_history: dict[str, list[CollaborationHistoryRecord]] = defaultdict(list)
    terminated_sessions: set[str] = set()
    available_evidence: dict[tuple[str, int, str], int] = {}
    available_artifacts: dict[str, ArtifactRef] = {}
    available_budgets: dict[str, int] = {}

    for transaction_index, transaction in enumerate(accepted):
        proposal = transaction.proposal
        current_binding = bindings_by_id.get(proposal.proposal_id)
        current_sequence = (
            current_binding.event.sequence if current_binding is not None else transaction_index
        )
        governing_policy_hash = (
            current_binding.governing_policy_hash if current_binding is not None else ""
        )
        if type(proposal) is AddEvidence:
            evidence = expected_hash_verified_evidence(proposal)
            available_evidence[(evidence.evidence_id, 1, evidence.content_hash)] = current_sequence
            available_artifacts[evidence.artifact.sha256] = evidence.artifact
        elif type(proposal) is RecordRunBudget:
            available_budgets[proposal.budget.budget_id] = current_sequence
        elif type(proposal) is RecordCapabilityProfile:
            if not _profile_is_canonical(proposal.profile) or (
                current_binding is not None
                and proposal.profile.governing_policy_hash != governing_policy_hash
            ):
                raise ValueError("capability profile does not match canonical reconstruction")
            profiles.append(proposal.profile)
            profile_transactions[proposal.proposal_id] = (
                transaction.proposal_hash,
                proposal.profile,
            )
        elif type(proposal) is RecordCohortPlan:
            if (
                current_binding is not None
                and proposal.request.governing_policy_hash != governing_policy_hash
            ):
                raise ValueError("cohort plan does not bind its governing audit policy")
            resolved_profiles = []
            for receipt in proposal.profile_receipts:
                resolved = profile_transactions.get(receipt.proposal_id)
                exact_transaction = _receipt_binding(
                    proposal_id=receipt.proposal_id,
                    proposal_hash=receipt.proposal_hash,
                    audit_event_id=receipt.audit_event_id,
                    audit_event_hash=receipt.audit_event_hash,
                    expected_type=RecordCapabilityProfile,
                    bindings=bindings_by_id,
                    current_sequence=current_sequence,
                    governing_policy_hash=governing_policy_hash,
                )
                if (
                    resolved is None
                    or resolved[0] != receipt.proposal_hash
                    or exact_transaction is None
                ):
                    raise ValueError("cohort plan profile receipt is unavailable")
                resolved_profiles.append(resolved[1])
            if build_cohort(proposal.request, tuple(resolved_profiles)) != proposal.plan:
                raise ValueError("cohort plan does not match deterministic recomputation")
            cohorts.append(proposal.plan)
            cohort_transactions[proposal.proposal_id] = (
                transaction.proposal_hash,
                proposal.plan,
            )
            cohorts_by_id[proposal.plan.cohort_plan_id] = proposal.plan
        elif type(proposal) is RecordDiversityAssessment:
            if (
                current_binding is not None
                and proposal.assessment.governing_policy_hash != governing_policy_hash
            ):
                raise ValueError("diversity assessment does not bind its audit policy")
            resolved_cohort = cohort_transactions.get(proposal.cohort_plan_receipt.proposal_id)
            if (
                resolved_cohort is None
                or resolved_cohort[0] != proposal.cohort_plan_receipt.proposal_hash
                or _receipt_binding(
                    proposal_id=proposal.cohort_plan_receipt.proposal_id,
                    proposal_hash=proposal.cohort_plan_receipt.proposal_hash,
                    audit_event_id=proposal.cohort_plan_receipt.audit_event_id,
                    audit_event_hash=proposal.cohort_plan_receipt.audit_event_hash,
                    expected_type=RecordCohortPlan,
                    bindings=bindings_by_id,
                    current_sequence=current_sequence,
                    governing_policy_hash=governing_policy_hash,
                )
                is None
            ):
                raise ValueError("diversity cohort receipt is unavailable")
            resolved_profiles = []
            for receipt in proposal.profile_receipts:
                resolved = profile_transactions.get(receipt.proposal_id)
                if (
                    resolved is None
                    or resolved[0] != receipt.proposal_hash
                    or _receipt_binding(
                        proposal_id=receipt.proposal_id,
                        proposal_hash=receipt.proposal_hash,
                        audit_event_id=receipt.audit_event_id,
                        audit_event_hash=receipt.audit_event_hash,
                        expected_type=RecordCapabilityProfile,
                        bindings=bindings_by_id,
                        current_sequence=current_sequence,
                        governing_policy_hash=governing_policy_hash,
                    )
                    is None
                ):
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
        elif type(proposal) is RecordCollaborationSession:
            retained_cohort = cohorts_by_id.get(proposal.session.cohort_plan.cohort_plan_id)
            if retained_cohort != proposal.session.cohort_plan:
                raise ValueError("collaboration session cohort must be prior accepted state")
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
            if rebuilt_session != proposal.session or (
                current_binding is not None
                and proposal.session.governing_policy_hash != governing_policy_hash
            ):
                raise ValueError("collaboration session does not match canonical reconstruction")
            sessions.append(proposal.session)
            sessions_by_id[proposal.session.session_id] = proposal.session
        elif type(proposal) is AppendPeerRequest:
            session = sessions_by_id.get(proposal.request.session_id)
            current_state = (
                None
                if session is None
                else rebuild_collaboration_state(
                    session,
                    tuple(collaboration_history[proposal.request.session_id]),
                )
            )
            if (
                session is None
                or current_state is None
                or (
                    current_binding is not None
                    and session.governing_policy_hash != governing_policy_hash
                )
                or proposal.request.session_id in terminated_sessions
                or current_state.pending_request is not None
                or evaluate_termination(current_state.state).terminated
            ):
                raise ValueError("peer request session must be prior accepted state")
            request = proposal.request
            state = current_state.state
            expected_peer = next_peer(session, state)
            expected_sender = state.contributions[-1].peer_id if state.contributions else None
            allowed_capabilities = {
                requirement.capability_id
                for requirement in (
                    session.cohort_plan.request_snapshot.required_capabilities
                    + session.cohort_plan.request_snapshot.preferred_capabilities
                )
            }
            if not (
                request.sequence == len(state.contributions) + 1
                and request.recipient_id == expected_peer
                and request.sender_id == expected_sender
                and request.requested_capability_id in allowed_capabilities
                and set(request.tool_ids).issubset(session.budget.allowed_tool_ids)
                and set(request.artifact_refs).issubset(session.allowed_artifacts)
                and (
                    request.parent_contribution_id is None
                    or request.parent_contribution_id
                    in {item.contribution_id for item in state.contributions}
                )
                and request.remaining_budget == session.remaining_resources(state.usage_history)
            ):
                raise ValueError("peer request does not match prior collaboration state")
            collaboration_history[proposal.request.session_id].append(proposal.request)
            if (
                rebuild_collaboration_state(
                    session, tuple(collaboration_history[proposal.request.session_id])
                )
                is None
            ):
                raise ValueError("peer request does not reconstruct from prior accepted state")
            requests.append(proposal.request)
        elif type(proposal) is AppendPeerContribution:
            session = sessions_by_id.get(proposal.contribution.session_id)
            if (
                session is None
                or (
                    current_binding is not None
                    and session.governing_policy_hash != governing_policy_hash
                )
                or proposal.contribution.session_id in terminated_sessions
            ):
                raise ValueError("peer contribution session must be prior accepted state")
            collaboration_history[proposal.contribution.session_id].append(proposal)
            if (
                rebuild_collaboration_state(
                    session, tuple(collaboration_history[proposal.contribution.session_id])
                )
                is None
            ):
                raise ValueError("peer contribution does not reconstruct from prior state")
            contributions.append(proposal.contribution)
        elif type(proposal) is AppendTopologyEvent:
            session = sessions_by_id.get(proposal.event.session_id)
            if (
                session is None
                or (
                    current_binding is not None
                    and session.governing_policy_hash != governing_policy_hash
                )
                or proposal.event.session_id in terminated_sessions
            ):
                raise ValueError("topology event session must be prior accepted state")
            collaboration_history[proposal.event.session_id].append(proposal.event)
            if (
                rebuild_collaboration_state(
                    session, tuple(collaboration_history[proposal.event.session_id])
                )
                is None
            ):
                raise ValueError("topology event does not reconstruct from prior state")
            topology.append(proposal.event)
        elif type(proposal) is RecordCollaborationTermination:
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
            if (
                rebuilt is None
                or (
                    current_binding is not None
                    and session is not None
                    and session.governing_policy_hash != governing_policy_hash
                )
                or proposal.session_id in terminated_sessions
                or evaluate_termination(rebuilt.state) != proposal.termination
            ):
                raise ValueError("collaboration termination does not match recomputed state")
            terminations.append((proposal.session_id, proposal.termination))
            terminated_sessions.add(proposal.session_id)
        elif type(proposal) is RecordProcedureCompilation:
            compilation = _compilation_record(proposal)
            if (
                current_binding is not None
                and compilation.governing_policy_hash != governing_policy_hash
            ):
                raise ValueError("procedure compilation does not bind its audit policy")
            _require_compilation_source_receipts(
                compilation,
                bindings=bindings_by_id,
                current_sequence=current_sequence,
                available_evidence=available_evidence,
                source_snapshots=source_snapshots,
            )
            compilations.append(compilation)
            compilation_by_id[compilation.compilation_id] = compilation
        elif type(proposal) is RecordMethodDirectionOutcome:
            outcome_compilation = compilation_by_id.get(proposal.compilation_id)
            outcome = proposal.outcome
            if outcome_compilation is None:
                raise ValueError("method outcome compilation is unavailable")
            if (
                (
                    current_binding is not None
                    and outcome_compilation.governing_policy_hash != governing_policy_hash
                )
                or (
                    current_binding is not None
                    and outcome.governing_policy_hash != governing_policy_hash
                )
                or any(
                    available_artifacts.get(reference.sha256) != reference
                    for reference in outcome.evidence_refs
                )
                or any(
                    method_id != outcome_compilation.result.procedure.source_candidate.method_id
                    for method_id in outcome.failed_method_ids
                )
                or any(
                    procedure_id != outcome_compilation.result.procedure.procedure_id
                    for procedure_id in outcome.rejected_procedure_ids
                )
                or any(
                    available_budgets.get(budget_id, current_sequence) >= current_sequence
                    for budget_id in outcome.budget_reference_ids
                )
            ):
                raise ValueError("method outcome does not bind exact prior accepted evidence")
            outcomes.append(outcome)
        elif type(proposal) is BindCompiledProgressPlan:
            retained_compilation = compilation_by_id.get(proposal.binding.compilation_id)
            if (
                retained_compilation is None
                or retained_compilation.content_hash != proposal.binding.compilation_hash
            ):
                raise ValueError("compiled progress binding compilation is unavailable")
            _require_compilation_source_receipts(
                retained_compilation,
                bindings=bindings_by_id,
                current_sequence=current_sequence,
                available_evidence=available_evidence,
                source_snapshots=source_snapshots,
            )
            compilation_receipt = proposal.compilation_receipt
            receipt_transaction = _receipt_binding(
                proposal_id=compilation_receipt.proposal_id,
                proposal_hash=compilation_receipt.proposal_hash,
                audit_event_id=compilation_receipt.audit_event_id,
                audit_event_hash=compilation_receipt.audit_event_hash,
                expected_type=RecordProcedureCompilation,
                bindings=bindings_by_id,
                current_sequence=current_sequence,
                governing_policy_hash=governing_policy_hash,
            )
            if (
                receipt_transaction is None
                or proposal.binding.compilation_receipt != compilation_receipt
                or (
                    current_binding is not None
                    and proposal.binding.governing_policy_hash != governing_policy_hash
                )
            ):
                raise ValueError("compiled progress binding receipt is unavailable")
            receipt_proposal = cast(RecordProcedureCompilation, receipt_transaction.proposal)
            if _compilation_record(receipt_proposal) != retained_compilation:
                raise ValueError("compiled progress binding receipt does not match compilation")
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
        chain, snapshot = _resolve_model_cell_evidence(protocol, cell, traces, rewards)
        chains.append(chain)
        snapshots.append(snapshot)
    index = HarnessEvidenceSnapshotIndex.build(
        records=tuple(sorted(snapshots, key=lambda item: item.chain_receipt.record_id))
    )
    return analyze_model_harness(
        protocol,
        selected_cells,
        evidence_chains=tuple(chains),
        evidence_index=index,
    )


def _resolve_model_cell_evidence(
    protocol: ModelHarnessProtocol,
    cell: ModelHarnessCell,
    traces: tuple[HarnessExecutionTrace, ...],
    rewards: tuple[RewardValidityAssessment, ...],
) -> tuple[HarnessCellEvidenceChain, HarnessEvidenceSnapshotRecord]:
    matches: list[tuple[HarnessCellEvidenceChain, HarnessEvidenceSnapshotRecord]] = []
    for trace in traces:
        if trace.observed_binding.protocol_id != protocol.protocol_id or _binding_coordinate_key(
            trace
        ) != _coordinate_key(cell):
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
    return matches[0]


def _guidance_cell_matches_prior_evidence(
    cell: GuidanceEvaluationCell,
    traces: dict[str, HarnessExecutionTrace],
    rewards: dict[str, RewardValidityAssessment],
) -> bool:
    trace = None if cell.trace_id is None else traces.get(cell.trace_id)
    assessment = (
        None if cell.reward_assessment_id is None else rewards.get(cell.reward_assessment_id)
    )
    trace_dependent_references_exist = any(
        item is not None
        for item in (
            cell.output_artifact_id,
            cell.verifier_result_id,
            cell.reward_assessment_id,
        )
    )
    if trace is None:
        return cell.trace_id is None and not trace_dependent_references_exist
    binding = trace.observed_binding
    if (
        binding.guidance_protocol != cell.protocol
        or binding.protocol_id != cell.protocol_id
        or binding.protocol_version != cell.protocol_version
        or binding.protocol_hash != cell.protocol_hash
        or binding.guidance_condition is not cell.condition
    ):
        return False
    if cell.output_artifact_id is not None and cell.output_artifact_id not in {
        item.artifact_id for item in trace.output_artifacts
    }:
        return False
    if cell.verifier_result_id is not None and cell.verifier_result_id != trace.verifier_result_id:
        return False
    return assessment is None or (
        assessment.trace_id == trace.trace_id and assessment.trace_hash == trace.content_hash
    )


def _receipt_key(receipt: EvidenceReceipt) -> tuple[str, int, str]:
    return (receipt.record_id, receipt.schema_version, receipt.content_hash)


def _exact_evidence_receipt(
    record_id: str, schema_version: int, content_hash: str
) -> EvidenceReceipt:
    return EvidenceReceipt(
        record_id=record_id,
        schema_version=schema_version,
        content_hash=content_hash,
    )


def expected_evaluation_extension_snapshot(
    transactions: tuple[StoredTransaction, ...],
    events: tuple[AuditEvent, ...] = (),
) -> EvaluationExtensionIntegritySnapshot:
    accepted = _accepted(transactions)
    bindings_by_id = _accepted_bindings(transactions, events)
    guidance_protocols: list[GuidanceEvaluationProtocol] = []
    guidance_cells: list[GuidanceEvaluationCell] = []
    model_protocols: list[ModelHarnessProtocol] = []
    model_cells: list[ModelHarnessCell] = []
    traces: list[HarnessExecutionTrace] = []
    rewards: list[RewardValidityAssessment] = []
    analyses: list[ModelHarnessAnalysis] = []
    guidance_by_id = {}
    model_by_id: dict[str, ModelHarnessProtocol] = {}
    traces_by_id: dict[str, HarnessExecutionTrace] = {}
    rewards_by_id: dict[str, RewardValidityAssessment] = {}
    available_receipts: dict[tuple[str, int, str], int] = {}
    available_record_ids: dict[str, int] = {}

    for transaction_index, transaction in enumerate(accepted):
        proposal = transaction.proposal
        binding = bindings_by_id.get(proposal.proposal_id)
        sequence = binding.event.sequence if binding is not None else transaction_index
        governing_policy_hash = binding.governing_policy_hash if binding is not None else ""
        if type(proposal) is AddEvidence:
            evidence = expected_hash_verified_evidence(proposal)
            receipt = _exact_evidence_receipt(
                evidence.evidence_id,
                1,
                evidence.content_hash,
            )
            available_receipts[_receipt_key(receipt)] = sequence
            available_record_ids[evidence.evidence_id] = sequence
        elif type(proposal) is RecordGuidanceEvaluationProtocol:
            guidance_protocol = proposal.protocol
            guidance_protocols.append(guidance_protocol)
            guidance_by_id[guidance_protocol.protocol_id] = guidance_protocol
            receipt = _exact_evidence_receipt(
                guidance_protocol.protocol_id,
                guidance_protocol.schema_version,
                guidance_protocol.content_hash,
            )
            available_receipts[_receipt_key(receipt)] = sequence
        elif type(proposal) is AppendGuidanceEvaluationCell:
            guidance_cell = proposal.cell
            retained_guidance_protocol = guidance_by_id.get(guidance_cell.protocol_id)
            if (
                retained_guidance_protocol is None
                or guidance_cell.protocol != retained_guidance_protocol
            ):
                raise ValueError("guidance cell protocol must be exact prior accepted state")
            for record_id in (
                guidance_cell.output_artifact_id,
                guidance_cell.verifier_result_id,
            ):
                if record_id is not None and record_id not in available_record_ids:
                    raise ValueError("guidance cell evidence must be prior accepted state")
            if not _guidance_cell_matches_prior_evidence(
                guidance_cell,
                traces_by_id,
                rewards_by_id,
            ):
                raise ValueError(
                    "guidance cell evidence must match the exact prior trace and reward chain"
                )
            guidance_cells.append(guidance_cell)
        elif type(proposal) is RecordModelHarnessProtocol:
            model_protocol = proposal.protocol
            if (
                binding is not None
                and model_protocol.governing_policy_hash != governing_policy_hash
            ):
                raise ValueError("model-harness protocol does not bind its audit policy")
            model_protocols.append(model_protocol)
            model_by_id[model_protocol.protocol_id] = model_protocol
            receipt = _exact_evidence_receipt(
                model_protocol.protocol_id,
                model_protocol.schema_version,
                model_protocol.content_hash,
            )
            available_receipts[_receipt_key(receipt)] = sequence
        elif type(proposal) is RecordHarnessExecutionTrace:
            trace = proposal.envelope.trace
            protocol_id = trace.observed_binding.protocol_id
            trace_guidance_protocol = guidance_by_id.get(protocol_id)
            trace_model_protocol = model_by_id.get(protocol_id)
            guidance_matched = trace_guidance_protocol is not None and _trace_matches_guidance(
                trace,
                trace_guidance_protocol,
            )
            model_matched = trace_model_protocol is not None and _trace_matches_matrix(
                trace,
                trace_model_protocol,
            )
            if not guidance_matched and not model_matched:
                raise ValueError("harness trace protocol must be prior accepted state")
            budget = (
                trace_guidance_protocol.evaluation_budget
                if guidance_matched and trace_guidance_protocol is not None
                else next(
                    (
                        item.budget
                        for item in trace_model_protocol.model_budgets
                        if item.model == trace.observed_binding.model
                    ),
                    None,
                )
                if model_matched and trace_model_protocol is not None
                else None
            )
            if budget is None or not _trace_within_budget(trace, budget):
                raise ValueError("harness trace exceeds its exact prior protocol budget")
            from super_scientist.application.transactions.harness_extensions import (
                _trace_hash_bound_evidence,
                _trace_id_only_evidence,
            )

            if any(
                (record_id, 1, content_hash) not in available_receipts
                for record_id, content_hash in _trace_hash_bound_evidence(trace)
            ) or any(
                record_id not in available_record_ids
                for record_id in _trace_id_only_evidence(trace)
            ):
                raise ValueError("harness trace evidence must be exact prior accepted state")
            traces.append(trace)
            traces_by_id[trace.trace_id] = trace
            receipt = _exact_evidence_receipt(
                trace.trace_id, trace.schema_version, trace.content_hash
            )
            available_receipts[_receipt_key(receipt)] = sequence
        elif type(proposal) is RecordRewardAssessment:
            reward_proposal = proposal
            assessment = reward_proposal.assessment
            retained_trace = traces_by_id.get(assessment.trace_id)
            if retained_trace is None:
                raise ValueError("reward assessment trace must be prior accepted state")
            if (
                assessment.trace != retained_trace
                or reward_proposal.observation != retained_trace.reward_observation
                or reward_proposal.observation != assessment.observation
                or reward_proposal.findings != assessment.findings
            ):
                raise ValueError("reward assessment must bind the exact prior trace inputs")
            required_receipts = (
                assessment.evidence_inventory.resolved_by,
                assessment.expectation.resolution.expectation_source,
                assessment.expectation.resolution.resolver,
                *assessment.expectation.resolution.provenance,
                *(item.receipt for item in assessment.evidence_inventory.records),
            )
            if any(_receipt_key(item) not in available_receipts for item in required_receipts):
                raise ValueError("reward evidence receipt is not exact prior accepted state")
            freshness = trace_freshness(
                assessment.expectation,
                retained_trace,
                inventory=assessment.evidence_inventory,
            )
            expected_assessment = assess_reward_validity(
                reward_proposal.observation,
                retained_trace,
                reward_proposal.findings,
                expectation=assessment.expectation,
                verification=assessment.verification,
                diagnostic_coverage=assessment.diagnostic_coverage,
                inventory=assessment.evidence_inventory,
            )
            if freshness != assessment.freshness or expected_assessment != assessment:
                raise ValueError("reward assessment does not match deterministic reconstruction")
            rewards.append(assessment)
            rewards_by_id[assessment.assessment_id] = assessment
            for receipt in (
                reward_validity_receipt(assessment),
                trace_freshness_receipt(assessment.freshness),
            ):
                available_receipts[_receipt_key(receipt)] = sequence
        elif type(proposal) is AppendModelHarnessCell:
            model_cell = proposal.cell
            retained_model_protocol = model_by_id.get(model_cell.protocol_id)
            if retained_model_protocol is None or (
                binding is not None
                and retained_model_protocol.governing_policy_hash != governing_policy_hash
            ):
                raise ValueError("model-harness cell protocol must be prior accepted state")
            expected_cell = ModelHarnessCell.from_protocol(
                cell_id=model_cell.cell_id,
                protocol=retained_model_protocol,
                coordinate=model_cell.coordinate,
                metrics=model_cell.metrics,
                evidence_chain_receipt=model_cell.evidence_chain_receipt,
                observed_at=model_cell.observed_at,
            )
            if expected_cell != model_cell:
                raise ValueError("model-harness cell does not match deterministic reconstruction")
            _resolve_model_cell_evidence(
                retained_model_protocol,
                model_cell,
                tuple(traces),
                tuple(rewards),
            )
            model_cells.append(model_cell)
        elif type(proposal) is RecordModelHarnessAnalysis:
            analysis = proposal.analysis
            expected_analysis = _recomputed_model_analysis(
                analysis,
                tuple(model_protocols),
                tuple(model_cells),
                tuple(traces),
                tuple(rewards),
            )
            if expected_analysis != analysis:
                raise ValueError(
                    "model-harness analysis does not match deterministic reconstruction"
                )
            analyses.append(analysis)

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
