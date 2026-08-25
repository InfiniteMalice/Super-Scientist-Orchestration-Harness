from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from super_scientist.application.cognitive.integrity import (
    expected_cognitive_snapshot,
    expected_evaluation_extension_snapshot,
)
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.cognition import (
    CapabilityProfile,
    CapabilityProfileReceiptRef,
    CapabilityRequirement,
    CohortPlanReceiptRef,
    CohortRequest,
    assess_diversity,
    build_cohort,
)
from super_scientist.domain.cognition.grounding import assess_capability
from super_scientist.domain.collaboration import CollaborationSession
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.procedures import (
    AcceptedSourceReceiptRef,
    CompiledProgressPlanBinding,
    GroundedCapabilityAssessment,
    OpaqueProcedureCompilationEnvelope,
    ProcedureCompilationReceiptRef,
    ProcedureCompilationRecord,
    ProcedureCompilationRequest,
    canonical_model_hash,
    compile_method,
    procedure_to_progress_plan,
)
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.audit.models import AuditEvent, json_compatible_payload
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AppendGuidanceEvaluationCell,
    AppendModelHarnessCell,
    AppendPeerRequest,
    Approval,
    BindCompiledProgressPlan,
    HarnessExecutionTraceEnvelope,
    HarnessTraceRecordMetadata,
    RecordCapabilityProfile,
    RecordCohortPlan,
    RecordCollaborationSession,
    RecordDiversityAssessment,
    RecordGuidanceEvaluationProtocol,
    RecordHarnessExecutionTrace,
    RecordModelHarnessProtocol,
    RecordProcedureCompilation,
    RecordRewardAssessment,
    TransactionDecision,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.cognitive_records import CapabilityProfileRepository
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.repositories import RepositorySet, StoredTransaction
from tests.integration.application import test_procedure_service as procedure_service_tests
from tests.integration.application.test_procedure_service import _retain_exact_sources
from tests.unit.collaboration.conftest import POLICY_HASH, profile
from tests.unit.collaboration.test_engine import _request as peer_request
from tests.unit.harness_eval.test_guidance import _cell, _protocol
from tests.unit.harness_eval.test_model_harness_matrix import _cells as matrix_cells
from tests.unit.harness_eval.test_model_harness_matrix import _protocol as matrix_protocol
from tests.unit.harness_eval.test_rewards import assess_reward_validity
from tests.unit.harness_eval.test_traces import valid_trace
from tests.unit.procedures.test_compiler import valid_request

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
pytest_plugins = (
    "tests.integration.application.test_progress_service",
    "tests.unit.collaboration.conftest",
)


def _stored(
    proposal: (
        RecordCapabilityProfile
        | AddEvidence
        | AppendModelHarnessCell
        | AppendPeerRequest
        | BindCompiledProgressPlan
        | RecordCollaborationSession
        | RecordCohortPlan
        | RecordDiversityAssessment
        | RecordProcedureCompilation
        | RecordGuidanceEvaluationProtocol
        | RecordHarnessExecutionTrace
        | RecordModelHarnessProtocol
        | RecordRewardAssessment
        | AppendGuidanceEvaluationCell
    ),
) -> StoredTransaction:
    return StoredTransaction(
        proposal=proposal,
        proposal_hash="a" * 64,
        decision=TransactionDecision(proposal_id=proposal.proposal_id, accepted=True),
        intent_fingerprint=None,
        created_at=NOW,
    )


def _accepted_events(*stored: StoredTransaction) -> tuple[AuditEvent, ...]:
    events = []
    prior = None
    for transaction in stored:
        payload = {
            "proposal": transaction.proposal.model_dump(mode="json"),
            "decision": transaction.decision.model_dump(mode="json"),
            "policy_hash": POLICY_HASH,
            "stored_policy_hash": POLICY_HASH,
            "transaction_persisted": True,
        }
        proposal = transaction.proposal
        snapshot_families = {
            "evidence-snapshot-requirement-1": "evidence-snapshot-requirement-1",
            "procedure-session-snapshot-1": "procedure-session-snapshot-1",
            "procedure-session-snapshot-2": "procedure-session-snapshot-1",
        }
        if type(proposal) is AddEvidence and proposal.evidence.evidence_id in snapshot_families:
            snapshot_id = proposal.evidence.evidence_id
            payload["procedure_source_snapshot"] = {
                "schema_version": 1,
                "snapshot_family_id": snapshot_families[snapshot_id],
                "snapshot_id": snapshot_id,
                "evidence_id": snapshot_id,
                "artifact_hash": proposal.evidence.content_hash,
            }
        event = append_event(
            prior,
            "transaction_decision",
            payload,
            NOW,
        )
        events.append(event)
        prior = event
    return tuple(events)


def _profile_receipt(
    transaction: StoredTransaction,
    event: AuditEvent,
) -> CapabilityProfileReceiptRef:
    return CapabilityProfileReceiptRef(
        proposal_id=transaction.proposal.proposal_id,
        proposal_hash=transaction.proposal_hash,
        audit_event_id=event.event_id,
        audit_event_hash=event.event_hash,
    )


def _cohort_receipt(
    transaction: StoredTransaction,
    event: AuditEvent,
) -> CohortPlanReceiptRef:
    return CohortPlanReceiptRef(
        proposal_id=transaction.proposal.proposal_id,
        proposal_hash=transaction.proposal_hash,
        audit_event_id=event.event_id,
        audit_event_hash=event.event_hash,
    )


def _unverified_evidence(
    evidence_id: str,
    content_hash: str,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="procedure-source",
        source_locator=f"fixture:{evidence_id}",
        retrieved_at=NOW,
        artifact=ArtifactRef(
            sha256=content_hash,
            size_bytes=1,
            media_type="application/json",
            relative_path=f"sha256/{content_hash}",
        ),
        provenance={"fixture": "task-15-integrity"},
        ingestion_actor_id="source-recorder",
    )


def _procedure_source_history() -> tuple[
    tuple[StoredTransaction, ...],
    tuple[AuditEvent, ...],
    ProcedureCompilationRequest,
]:
    base = valid_request()
    original_grounded = base.capability_assessments[0]
    original_profile = original_grounded.profile
    profile_values = original_profile.model_dump(mode="python", exclude={"content_hash"})
    profile_values["governing_policy_hash"] = POLICY_HASH
    retained_profile = CapabilityProfile.build(**profile_values)

    capability_snapshot = AddEvidence(
        proposal_id="integrity-capability-snapshot-proposal",
        idempotency_key="integrity-capability-snapshot-proposal",
        proposer=_actor(),
        evidence=_unverified_evidence(
            original_grounded.profile_receipt.source_snapshot_id,
            original_grounded.profile_receipt.source_snapshot_hash,
        ),
    )
    profile_proposal = RecordCapabilityProfile(
        proposal_id="integrity-procedure-profile-proposal",
        idempotency_key="integrity-procedure-profile-proposal",
        proposer=_actor(),
        profile=retained_profile,
    )
    catalog_snapshot = AddEvidence(
        proposal_id="integrity-catalog-snapshot-proposal",
        idempotency_key="integrity-catalog-snapshot-proposal",
        proposer=_actor(),
        evidence=_unverified_evidence(
            base.artifact_catalog_receipt.source_snapshot_id,
            base.artifact_catalog_receipt.source_snapshot_hash,
        ),
    )
    catalog_receipts = (
        base.artifact_catalog_receipt,
        base.tool_catalog_receipt,
        base.validator_catalog_receipt,
    )
    catalog_proposals = tuple(
        AddEvidence(
            proposal_id=f"integrity-catalog-source-{index}",
            idempotency_key=f"integrity-catalog-source-{index}",
            proposer=_actor(),
            evidence=_unverified_evidence(
                receipt.source_record_id,
                receipt.source_content_hash,
            ),
        )
        for index, receipt in enumerate(catalog_receipts, start=1)
    )
    prefix = tuple(
        _stored(item)
        for item in (
            capability_snapshot,
            profile_proposal,
            catalog_snapshot,
            *catalog_proposals,
        )
    )
    prefix_events = _accepted_events(*prefix)

    profile_receipt_values = original_grounded.profile_receipt.model_dump(
        mode="python",
        exclude={"content_hash"},
    )
    profile_receipt_values.update(
        source_record_id=retained_profile.profile_id,
        source_schema_version=retained_profile.schema_version,
        source_content_hash=retained_profile.content_hash,
        proposal_id=profile_proposal.proposal_id,
        proposal_hash=prefix[1].proposal_hash,
        audit_event_id=prefix_events[1].event_id,
        audit_event_hash=prefix_events[1].event_hash,
    )
    profile_receipt = AcceptedSourceReceiptRef.build(**profile_receipt_values)
    grounded = GroundedCapabilityAssessment.build(
        profile=retained_profile,
        assessment=assess_capability(
            retained_profile,
            original_grounded.assessment.requirement,
        ),
        profile_receipt=profile_receipt,
    )

    rebound_catalog_receipts = []
    for index, (receipt, proposal) in enumerate(
        zip(catalog_receipts, catalog_proposals, strict=True),
        start=3,
    ):
        values = receipt.model_dump(mode="python", exclude={"content_hash"})
        values.update(
            proposal_id=proposal.proposal_id,
            proposal_hash=prefix[index].proposal_hash,
            audit_event_id=prefix_events[index].event_id,
            audit_event_hash=prefix_events[index].event_hash,
        )
        rebound_catalog_receipts.append(AcceptedSourceReceiptRef.build(**values))

    request_values = base.model_dump(mode="python")
    request_values.update(
        capability_assessments=(grounded,),
        artifact_catalog_receipt=rebound_catalog_receipts[0],
        tool_catalog_receipt=rebound_catalog_receipts[1],
        validator_catalog_receipt=rebound_catalog_receipts[2],
    )
    request = ProcedureCompilationRequest.model_validate(request_values, strict=True)
    return prefix, prefix_events, request


def _procedure_proposal(
    request: ProcedureCompilationRequest,
    suffix: str,
) -> RecordProcedureCompilation:
    return RecordProcedureCompilation(
        proposal_id=f"integrity-compilation-{suffix}",
        idempotency_key=f"integrity-compilation-{suffix}",
        proposer=_actor(),
        compilation=OpaqueProcedureCompilationEnvelope.build(
            compilation_id=f"integrity-compilation-{suffix}",
            result=compile_method(request),
            created_at=NOW,
            governing_policy_hash=POLICY_HASH,
        ),
    )


def _actor() -> ActorIdentity:
    return ActorIdentity(actor_id="service", kind=ActorKind.SERVICE, created_at=NOW)


def _governed_policy() -> PolicySnapshot:
    policy = GovernancePolicyV2(
        required_claim_checks=("source_exists",),
        human_approval_for=frozenset(),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.RESEARCH_PROCESS,
                persistence=PersistenceScope.RUN_LOCAL,
                minimum_verification=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
                permitted_grounding=frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=False,
                rollback_required=False,
            ),
        ),
    )
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _profile_for_policy(policy: PolicySnapshot) -> CapabilityProfile:
    retained = profile("peer-a")
    values = retained.model_dump(mode="python", exclude={"content_hash"})
    values["governing_policy_hash"] = policy.policy_hash
    return type(retained).build(**values)


def test_expected_cognitive_snapshot_recomputes_cohort_plan() -> None:
    retained = profile("peer-a")
    profile_proposal = RecordCapabilityProfile(
        proposal_id="profile-proposal",
        idempotency_key="profile-proposal",
        proposer=_actor(),
        profile=retained,
    )
    request = CohortRequest.build(
        request_id="request",
        task_id="research",
        min_members=1,
        max_members=1,
        candidate_actor_ids=("peer-a",),
        required_capabilities=(
            CapabilityRequirement(
                requirement_id="analysis",
                capability_id="analysis",
                task_family_id="research",
                evidence_snapshot_hash="e" * 64,
            ),
        ),
        preferred_capabilities=(),
        prohibited_combinations=(),
        governing_policy_hash=POLICY_HASH,
    )
    canonical = build_cohort(request, (retained,))
    forged = canonical.model_copy(update={"minimum_size_met": not canonical.minimum_size_met})
    cohort_proposal = RecordCohortPlan.model_construct(
        proposal_id="cohort-proposal",
        idempotency_key="cohort-proposal",
        proposer=_actor(),
        approval=None,
        proposal_type="record_cohort_plan",
        request=request,
        profile_receipts=(
            CapabilityProfileReceiptRef(
                proposal_id="profile-proposal",
                proposal_hash="a" * 64,
                audit_event_id="audit-profile",
                audit_event_hash="b" * 64,
            ),
        ),
        plan=forged,
    )

    with pytest.raises(ValueError, match="cohort plan"):
        expected_cognitive_snapshot((_stored(profile_proposal), _stored(cohort_proposal)))


def test_expected_cognitive_snapshot_rejects_forged_receipt_audit_binding() -> None:
    retained = profile("peer-a")
    profile_proposal = RecordCapabilityProfile(
        proposal_id="profile-proposal",
        idempotency_key="profile-proposal",
        proposer=_actor(),
        profile=retained,
    )
    request = CohortRequest.build(
        request_id="request",
        task_id="research",
        min_members=1,
        max_members=1,
        candidate_actor_ids=("peer-a",),
        required_capabilities=(
            CapabilityRequirement(
                requirement_id="analysis",
                capability_id="analysis",
                task_family_id="research",
                evidence_snapshot_hash="e" * 64,
            ),
        ),
        preferred_capabilities=(),
        prohibited_combinations=(),
        governing_policy_hash=POLICY_HASH,
    )
    cohort_proposal = RecordCohortPlan(
        proposal_id="cohort-proposal",
        idempotency_key="cohort-proposal",
        proposer=_actor(),
        request=request,
        profile_receipts=(
            CapabilityProfileReceiptRef(
                proposal_id="profile-proposal",
                proposal_hash="a" * 64,
                audit_event_id="forged-audit",
                audit_event_hash="b" * 64,
            ),
        ),
        plan=build_cohort(request, (retained,)),
    )
    stored = (_stored(profile_proposal), _stored(cohort_proposal))

    with pytest.raises(ValueError, match="profile receipt"):
        expected_cognitive_snapshot(stored, _accepted_events(*stored))


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("proposal_id", "forged-profile-proposal"),
        ("proposal_hash", "c" * 64),
        ("audit_event_id", "forged-profile-audit"),
        ("audit_event_hash", "d" * 64),
    ),
)
def test_cohort_reconstruction_binds_every_profile_receipt_field(
    field_name: str,
    forged_value: str,
) -> None:
    retained = profile("peer-a")
    profile_transaction = _stored(
        RecordCapabilityProfile(
            proposal_id="matrix-profile-proposal",
            idempotency_key="matrix-profile-proposal",
            proposer=_actor(),
            profile=retained,
        )
    )
    profile_event = _accepted_events(profile_transaction)[0]
    request = CohortRequest.build(
        request_id="matrix-request",
        task_id="research",
        min_members=1,
        max_members=1,
        candidate_actor_ids=("peer-a",),
        required_capabilities=(
            CapabilityRequirement(
                requirement_id="analysis",
                capability_id="analysis",
                task_family_id="research",
                evidence_snapshot_hash="e" * 64,
            ),
        ),
        preferred_capabilities=(),
        prohibited_combinations=(),
        governing_policy_hash=POLICY_HASH,
    )
    receipt = _profile_receipt(profile_transaction, profile_event)
    proposal = RecordCohortPlan(
        proposal_id="matrix-cohort-proposal",
        idempotency_key="matrix-cohort-proposal",
        proposer=_actor(),
        request=request,
        profile_receipts=(receipt,),
        plan=build_cohort(request, (retained,)),
    )
    cohort_transaction = _stored(proposal)
    exact_history = (profile_transaction, cohort_transaction)
    exact_events = _accepted_events(*exact_history)
    assert len(expected_cognitive_snapshot(exact_history, exact_events).cohort_plans) == 1

    forged_receipt = receipt.model_copy(update={field_name: forged_value})
    forged_transaction = _stored(
        proposal.model_copy(update={"profile_receipts": (forged_receipt,)})
    )
    forged_history = (profile_transaction, forged_transaction)
    with pytest.raises(ValueError, match="profile receipt"):
        expected_cognitive_snapshot(forged_history, _accepted_events(*forged_history))


@pytest.mark.parametrize("receipt_kind", ("cohort", "profile"))
@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("proposal_id", "forged-upstream-proposal"),
        ("proposal_hash", "c" * 64),
        ("audit_event_id", "forged-upstream-audit"),
        ("audit_event_hash", "d" * 64),
    ),
)
def test_diversity_reconstruction_binds_every_receipt_field(
    receipt_kind: str,
    field_name: str,
    forged_value: str,
) -> None:
    retained = profile("peer-a")
    profile_transaction = _stored(
        RecordCapabilityProfile(
            proposal_id="diversity-profile-proposal",
            idempotency_key="diversity-profile-proposal",
            proposer=_actor(),
            profile=retained,
        )
    )
    profile_event = _accepted_events(profile_transaction)[0]
    request = CohortRequest.build(
        request_id="diversity-request",
        task_id="research",
        min_members=1,
        max_members=1,
        candidate_actor_ids=("peer-a",),
        required_capabilities=(
            CapabilityRequirement(
                requirement_id="analysis",
                capability_id="analysis",
                task_family_id="research",
                evidence_snapshot_hash="e" * 64,
            ),
        ),
        preferred_capabilities=(),
        prohibited_combinations=(),
        governing_policy_hash=POLICY_HASH,
    )
    profile_receipt = _profile_receipt(profile_transaction, profile_event)
    cohort = build_cohort(request, (retained,))
    cohort_proposal = RecordCohortPlan(
        proposal_id="diversity-cohort-proposal",
        idempotency_key="diversity-cohort-proposal",
        proposer=_actor(),
        request=request,
        profile_receipts=(profile_receipt,),
        plan=cohort,
    )
    cohort_transaction = _stored(cohort_proposal)
    prefix_events = _accepted_events(profile_transaction, cohort_transaction)
    cohort_receipt = _cohort_receipt(cohort_transaction, prefix_events[1])
    proposal = RecordDiversityAssessment(
        proposal_id="diversity-assessment-proposal",
        idempotency_key="diversity-assessment-proposal",
        proposer=_actor(),
        cohort_plan_receipt=cohort_receipt,
        profile_receipts=(profile_receipt,),
        error_correlations=(),
        assessment=assess_diversity(cohort, (retained,), ()),
    )
    exact_history = (profile_transaction, cohort_transaction, _stored(proposal))
    assert (
        len(
            expected_cognitive_snapshot(
                exact_history,
                _accepted_events(*exact_history),
            ).diversity_assessments
        )
        == 1
    )

    if receipt_kind == "cohort":
        forged = proposal.model_copy(
            update={
                "cohort_plan_receipt": cohort_receipt.model_copy(update={field_name: forged_value})
            }
        )
    else:
        forged = proposal.model_copy(
            update={
                "profile_receipts": (profile_receipt.model_copy(update={field_name: forged_value}),)
            }
        )
    forged_history = (profile_transaction, cohort_transaction, _stored(forged))
    with pytest.raises(ValueError, match="receipt"):
        expected_cognitive_snapshot(forged_history, _accepted_events(*forged_history))


def test_procedure_envelope_subclass_and_hostile_nested_state_fail_without_hooks() -> None:
    hooks = 0

    class HostileEnvelope(OpaqueProcedureCompilationEnvelope):
        def __getattribute__(self, name: str) -> object:
            nonlocal hooks
            hooks += 1
            raise AssertionError(name)

    class HostileValue:
        def __getattribute__(self, name: str) -> object:
            nonlocal hooks
            hooks += 1
            raise RuntimeError(name)

    def proposal(envelope: object, suffix: str) -> RecordProcedureCompilation:
        return RecordProcedureCompilation.model_construct(
            proposal_id=f"procedure-{suffix}",
            idempotency_key=f"procedure-{suffix}",
            proposer=_actor(),
            approval=None,
            proposal_type="record_procedure_compilation",
            compilation=envelope,
        )

    subclass = HostileEnvelope.model_construct()
    nested = OpaqueProcedureCompilationEnvelope.model_construct(
        schema_version=1,
        compilation_id="compilation",
        result_json_base64=HostileValue(),
        result_json_hash="a" * 64,
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    for item in (proposal(subclass, "subclass"), proposal(nested, "nested")):
        transaction = StoredTransaction.model_construct(
            proposal=item,
            proposal_hash="a" * 64,
            decision=TransactionDecision(proposal_id=item.proposal_id, accepted=True),
            intent_fingerprint=None,
            created_at=NOW,
        )
        with pytest.raises(ValueError, match=r"unsafe|exact declared type"):
            expected_cognitive_snapshot((transaction,))
    assert hooks == 0


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("proposal_id", "forged-catalog-proposal"),
        ("proposal_hash", "8" * 64),
        ("audit_event_id", "forged-catalog-audit"),
        ("audit_event_hash", "9" * 64),
        ("source_record_id", "forged-catalog-record"),
        ("upstream_source_hash", "7" * 64),
        ("upstream_snapshot_id", "forged-catalog-snapshot"),
        ("upstream_snapshot_hash", "6" * 64),
    ),
)
def test_procedure_reconstruction_binds_every_source_and_snapshot_receipt_field(
    field_name: str,
    forged_value: str,
) -> None:
    prefix, _prefix_events, request = _procedure_source_history()
    exact_proposal = _procedure_proposal(request, "exact")
    exact_history = (*prefix, _stored(exact_proposal))
    assert (
        len(
            expected_cognitive_snapshot(
                exact_history,
                _accepted_events(*exact_history),
            ).compilations
        )
        == 1
    )

    if field_name.startswith("upstream_"):
        upstream_index = 3 if field_name == "upstream_source_hash" else 2
        upstream = prefix[upstream_index].proposal
        assert type(upstream) is AddEvidence
        evidence_id = (
            forged_value if field_name == "upstream_snapshot_id" else upstream.evidence.evidence_id
        )
        content_hash = (
            forged_value
            if field_name in {"upstream_source_hash", "upstream_snapshot_hash"}
            else upstream.evidence.content_hash
        )
        forged_upstream = AddEvidence(
            proposal_id=upstream.proposal_id,
            idempotency_key=upstream.idempotency_key,
            proposer=upstream.proposer,
            evidence=_unverified_evidence(evidence_id, content_hash),
        )
        forged_prefix = list(prefix)
        forged_prefix[upstream_index] = _stored(forged_upstream)
        forged_proposal = _procedure_proposal(request, field_name)
        forged_history = (*forged_prefix, _stored(forged_proposal))
    else:
        receipt_values = request.artifact_catalog_receipt.model_dump(
            mode="python",
            exclude={"content_hash"},
        )
        receipt_values[field_name] = forged_value
        forged_receipt = AcceptedSourceReceiptRef.build(**receipt_values)
        request_values = request.model_dump(mode="python")
        request_values["artifact_catalog_receipt"] = forged_receipt
        forged_request = ProcedureCompilationRequest.model_validate(request_values, strict=True)
        forged_proposal = _procedure_proposal(forged_request, field_name)
        forged_history = (*prefix, _stored(forged_proposal))

    with pytest.raises(ValueError, match="source receipt"):
        expected_cognitive_snapshot(forged_history, _accepted_events(*forged_history))


def test_procedure_reconstruction_rejects_a_superseded_source_snapshot() -> None:
    prefix, _prefix_events, request = _procedure_source_history()
    newer_snapshot = AddEvidence(
        proposal_id="integrity-newer-catalog-snapshot",
        idempotency_key="integrity-newer-catalog-snapshot",
        proposer=_actor(),
        evidence=_unverified_evidence("procedure-session-snapshot-2", "5" * 64),
    )
    compilation = _procedure_proposal(request, "stale-snapshot")
    stale_history = (*prefix, _stored(newer_snapshot), _stored(compilation))

    with pytest.raises(ValueError, match="source receipt"):
        expected_cognitive_snapshot(stale_history, _accepted_events(*stale_history))


@pytest.mark.integration
def test_real_coordinator_procedure_sources_pass_full_workspace_reconstruction(
    v2_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_persist = procedure_service_tests._persist_accepted
    original_persist_at_policy = procedure_service_tests._persist_accepted_at_policy
    policy_fields = {
        "policy_hash": v2_runtime.policy.policy_hash,
        "stored_policy_hash": v2_runtime.policy.policy_hash,
    }

    def persist_with_active_policy(repositories, proposal, occurred_at, **kwargs):
        kwargs["audit_policy_fields"] = policy_fields
        return original_persist(repositories, proposal, occurred_at, **kwargs)

    def persist_at_active_policy(repositories, proposal, _policy_hash, **kwargs):
        kwargs["audit_policy_fields"] = policy_fields
        return original_persist_at_policy(
            repositories,
            proposal,
            v2_runtime.policy.policy_hash,
            **kwargs,
        )

    monkeypatch.setattr(procedure_service_tests, "_persist_accepted", persist_with_active_policy)
    monkeypatch.setattr(
        procedure_service_tests,
        "_persist_accepted_at_policy",
        persist_at_active_policy,
    )
    with v2_runtime.uow_factory() as unit_of_work:
        connection = unit_of_work.connection
        assert connection is not None
        request = _retain_exact_sources(
            unit_of_work.repositories(),
            connection,
            v2_runtime.artifact_store,
            v2_runtime.policy.policy_hash,
        )
    proposal = RecordProcedureCompilation(
        proposal_id="integrity-real-source-compilation",
        idempotency_key="integrity-real-source-compilation",
        proposer=v2_runtime.proposer,
        compilation=OpaqueProcedureCompilationEnvelope.build(
            compilation_id="integrity-real-source-compilation",
            result=compile_method(request),
            created_at=NOW,
            governing_policy_hash=v2_runtime.policy.policy_hash,
        ),
    )

    assert v2_runtime.coordinator.submit(proposal).accepted is True
    with v2_runtime.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored_compilation = repositories.transactions.get_by_proposal_id(proposal.proposal_id)
        assert stored_compilation is not None
        compilation_events = tuple(
            event
            for event in repositories.audit.list_all()
            if (
                type(json_compatible_payload(event.payload).get("proposal")) is dict
                and json_compatible_payload(event.payload)["proposal"].get("proposal_id")
                == proposal.proposal_id
            )
        )
    assert len(compilation_events) == 1
    compilation = ProcedureCompilationRecord.build_from_untrusted_envelope(proposal.compilation)
    receipt = ProcedureCompilationReceiptRef(
        proposal_id=proposal.proposal_id,
        proposal_hash=stored_compilation.proposal_hash,
        audit_event_id=compilation_events[0].event_id,
        audit_event_hash=compilation_events[0].event_hash,
    )
    plan = procedure_to_progress_plan(
        compilation.result,
        run_id="run-1",
        plan_version_id="integrity-real-source-plan",
        version=1,
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    binding = CompiledProgressPlanBinding.build(
        binding_id="integrity-real-source-binding",
        compilation_receipt=receipt,
        compilation_id=compilation.compilation_id,
        compilation_hash=compilation.content_hash,
        procedure_id=compilation.result.procedure.procedure_id,
        procedure_hash=compilation.result.procedure.content_hash,
        plan=plan,
        plan_hash=canonical_model_hash(plan),
        created_at=NOW,
        governing_policy_hash=v2_runtime.policy.policy_hash,
    )
    binding_proposal = BindCompiledProgressPlan(
        proposal_id="integrity-real-source-binding-proposal",
        idempotency_key="integrity-real-source-binding-proposal",
        proposer=v2_runtime.proposer,
        approval=v2_runtime.approval(),
        compilation_receipt=receipt,
        binding=binding,
        plan=plan,
    )
    assert v2_runtime.coordinator.submit(binding_proposal).accepted is True
    with v2_runtime.uow_factory() as unit_of_work:
        result = verify_workspace(unit_of_work.repositories(), v2_runtime.artifact_store)
    assert result.valid is True, result.reason


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("proposal_id", "forged-compilation-proposal"),
        ("proposal_hash", "8" * 64),
        ("audit_event_id", "forged-compilation-audit"),
        ("audit_event_hash", "9" * 64),
    ),
)
def test_progress_binding_reconstruction_binds_every_compilation_receipt_field(
    field_name: str,
    forged_value: str,
) -> None:
    prefix, _prefix_events, request = _procedure_source_history()
    compilation_proposal = _procedure_proposal(request, "binding-source")
    compilation_transaction = _stored(compilation_proposal)
    compilation_history = (*prefix, compilation_transaction)
    compilation_events = _accepted_events(*compilation_history)
    compilation = ProcedureCompilationRecord.build_from_untrusted_envelope(
        compilation_proposal.compilation
    )
    receipt = ProcedureCompilationReceiptRef(
        proposal_id=compilation_proposal.proposal_id,
        proposal_hash=compilation_transaction.proposal_hash,
        audit_event_id=compilation_events[-1].event_id,
        audit_event_hash=compilation_events[-1].event_hash,
    )
    plan = procedure_to_progress_plan(
        compilation.result,
        run_id="integrity-run",
        plan_version_id="integrity-plan",
        version=1,
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )

    def binding_proposal(
        compilation_receipt: ProcedureCompilationReceiptRef,
        suffix: str,
    ) -> BindCompiledProgressPlan:
        binding = CompiledProgressPlanBinding.build(
            binding_id=f"integrity-binding-{suffix}",
            compilation_receipt=compilation_receipt,
            compilation_id=compilation.compilation_id,
            compilation_hash=compilation.content_hash,
            procedure_id=compilation.result.procedure.procedure_id,
            procedure_hash=compilation.result.procedure.content_hash,
            plan=plan,
            plan_hash=canonical_model_hash(plan),
            created_at=NOW,
            governing_policy_hash=POLICY_HASH,
        )
        return BindCompiledProgressPlan(
            proposal_id=f"integrity-binding-proposal-{suffix}",
            idempotency_key=f"integrity-binding-proposal-{suffix}",
            proposer=_actor(),
            compilation_receipt=compilation_receipt,
            binding=binding,
            plan=plan,
        )

    exact_proposal = binding_proposal(receipt, "exact")
    exact_history = (*compilation_history, _stored(exact_proposal))
    assert (
        len(
            expected_cognitive_snapshot(
                exact_history,
                _accepted_events(*exact_history),
            ).bindings
        )
        == 1
    )

    forged_receipt = receipt.model_copy(update={field_name: forged_value})
    forged_proposal = binding_proposal(forged_receipt, field_name)
    forged_history = (*compilation_history, _stored(forged_proposal))
    with pytest.raises(ValueError, match="binding receipt"):
        expected_cognitive_snapshot(forged_history, _accepted_events(*forged_history))


def test_evaluation_reconstruction_rejects_cell_before_protocol() -> None:
    protocol = _protocol()
    cell = _cell(protocol=protocol)
    protocol_proposal = RecordGuidanceEvaluationProtocol(
        proposal_id="guidance-protocol",
        idempotency_key="guidance-protocol",
        proposer=_actor(),
        protocol=protocol,
    )
    cell_proposal = AppendGuidanceEvaluationCell(
        proposal_id="guidance-cell",
        idempotency_key="guidance-cell",
        proposer=_actor(),
        cell=cell,
    )

    with pytest.raises(ValueError, match="prior accepted"):
        expected_evaluation_extension_snapshot((_stored(cell_proposal), _stored(protocol_proposal)))


def test_collaboration_reconstruction_rejects_peer_request_before_session(
    session_factory: Callable[..., CollaborationSession],
) -> None:
    session = session_factory("peer-a")
    request_proposal = AppendPeerRequest(
        proposal_id="ordered-peer-request",
        idempotency_key="ordered-peer-request",
        proposer=_actor(),
        request=peer_request(session, "peer-a"),
    )
    session_proposal = RecordCollaborationSession(
        proposal_id="ordered-session",
        idempotency_key="ordered-session",
        proposer=_actor(),
        session=session,
    )

    with pytest.raises(ValueError, match="prior accepted"):
        expected_cognitive_snapshot((_stored(request_proposal), _stored(session_proposal)))


def test_harness_reconstruction_rejects_trace_before_protocol() -> None:
    trace = valid_trace()
    protocol = trace.observed_binding.guidance_protocol
    assert protocol is not None
    trace_proposal = RecordHarnessExecutionTrace(
        proposal_id="ordered-trace",
        idempotency_key="ordered-trace",
        proposer=_actor(),
        envelope=HarnessExecutionTraceEnvelope(
            metadata=HarnessTraceRecordMetadata(
                received_at=NOW,
                source_id="ordered-runtime",
            ),
            trace=trace,
        ),
    )
    protocol_proposal = RecordGuidanceEvaluationProtocol(
        proposal_id="ordered-trace-protocol",
        idempotency_key="ordered-trace-protocol",
        proposer=_actor(),
        protocol=protocol,
    )

    with pytest.raises(ValueError, match="prior accepted"):
        expected_evaluation_extension_snapshot(
            (_stored(trace_proposal), _stored(protocol_proposal))
        )


def test_harness_reconstruction_rejects_reward_before_trace() -> None:
    trace = valid_trace()
    assert trace.reward_observation is not None
    assessment = assess_reward_validity(
        trace.reward_observation,
        trace,
        (),
        verifier_succeeded=True,
    )
    reward_proposal = RecordRewardAssessment(
        proposal_id="ordered-reward",
        idempotency_key="ordered-reward",
        proposer=_actor(),
        observation=assessment.observation,
        findings=assessment.findings,
        assessment=assessment,
    )
    trace_proposal = RecordHarnessExecutionTrace(
        proposal_id="ordered-reward-trace",
        idempotency_key="ordered-reward-trace",
        proposer=_actor(),
        envelope=HarnessExecutionTraceEnvelope(
            metadata=HarnessTraceRecordMetadata(
                received_at=NOW,
                source_id="ordered-reward-runtime",
            ),
            trace=trace,
        ),
    )

    with pytest.raises(ValueError, match="prior accepted"):
        expected_evaluation_extension_snapshot((_stored(reward_proposal), _stored(trace_proposal)))


def test_harness_reconstruction_rejects_model_cell_before_protocol() -> None:
    protocol = matrix_protocol(governing_policy_hash=POLICY_HASH)
    cell = matrix_cells(protocol)[0]
    cell_proposal = AppendModelHarnessCell(
        proposal_id="ordered-model-cell",
        idempotency_key="ordered-model-cell",
        proposer=_actor(),
        cell=cell,
    )
    protocol_proposal = RecordModelHarnessProtocol(
        proposal_id="ordered-model-protocol",
        idempotency_key="ordered-model-protocol",
        proposer=_actor(),
        protocol=protocol,
    )

    with pytest.raises(ValueError, match="prior accepted"):
        expected_evaluation_extension_snapshot((_stored(cell_proposal), _stored(protocol_proposal)))


def test_cognitive_row_tampering_fails_workspace_reconstruction(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'workspace.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    policy = _governed_policy()
    retained = _profile_for_policy(policy)
    proposal = RecordCapabilityProfile(
        proposal_id="profile-proposal",
        idempotency_key="profile-proposal",
        proposer=_actor(),
        approval=Approval(
            approver=ActorIdentity(
                actor_id="reviewer",
                kind=ActorKind.HUMAN,
                created_at=NOW,
            ),
            approved_at=NOW,
        ),
        profile=retained,
    )
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            repositories.policies.add_and_activate(policy, NOW)
            repositories.transactions.add(proposal, decision, NOW)
            repositories.audit.add(
                append_event(
                    None,
                    "transaction_decision",
                    {
                        "proposal": proposal.model_dump(mode="json"),
                        "decision": decision.model_dump(mode="json"),
                        "policy_hash": policy.policy_hash,
                        "stored_policy_hash": policy.policy_hash,
                        "transaction_persisted": True,
                    },
                    NOW,
                )
            )
            CapabilityProfileRepository(connection).add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=policy.policy_hash,
            )
        with engine.connect() as connection:
            assert verify_workspace(RepositorySet(connection), artifacts).valid is True
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("DROP TRIGGER capability_profiles_no_update")
            connection.execute(
                text(
                    "UPDATE capability_profiles "
                    "SET record_json = json_set(record_json, '$.profile_id', 'forged-profile')"
                )
            )
        with engine.connect() as connection:
            result = verify_workspace(RepositorySet(connection), artifacts)
            assert result.valid is False
            assert result.reason is not None and "workspace integrity error" in result.reason
    finally:
        engine.dispose()
