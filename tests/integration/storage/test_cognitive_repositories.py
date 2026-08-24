from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from super_scientist.config.loader import policy_hash
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.domain.procedures.models import ProcedureCompilationRecord
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions import models as transaction_models
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
    RejectionCode,
    TransactionDecision,
)
from super_scientist.providers.storage.cognitive_records import (
    CapabilityProfileRepository,
    CohortPlanRepository,
    CollaborationSessionRepository,
    CollaborationTerminationRepository,
    CompiledProgressPlanBindingRepository,
    DiversityAssessmentRepository,
    MethodDirectionOutcomeRepository,
    PeerContributionRepository,
    PeerRequestRepository,
    ProcedureCompilationRepository,
    TopologyEventRepository,
)
from super_scientist.providers.storage.database import upgrade_database
from super_scientist.providers.storage.evaluation_records import (
    GuidanceCellRepository,
    GuidanceEvaluationProtocolRepository,
    HarnessExecutionTraceRepository,
    ModelHarnessAnalysisRepository,
    ModelHarnessCellRepository,
    ModelHarnessProtocolRepository,
    RewardAssessmentRepository,
)
from super_scientist.providers.storage.repositories import RepositorySet, StorageIntegrityError
from tests.unit.collaboration.conftest import actor, profile
from tests.unit.domain.test_strict_parsing import _governed_proposal_examples

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
POLICY_HASH = "f" * 64


@dataclass(frozen=True, slots=True)
class GovernedRepositoryCase:
    table_name: str
    identifier_column: str
    relationship_columns: tuple[str, ...]
    repository_type: type[Any]


ALL_18_GOVERNED_REPOSITORY_CASES = (
    GovernedRepositoryCase("capability_profiles", "profile_id", (), CapabilityProfileRepository),
    GovernedRepositoryCase("cohort_plans", "cohort_plan_id", ("request_id",), CohortPlanRepository),
    GovernedRepositoryCase(
        "diversity_assessments",
        "diversity_assessment_id",
        ("cohort_plan_id",),
        DiversityAssessmentRepository,
    ),
    GovernedRepositoryCase(
        "collaboration_sessions",
        "session_id",
        ("cohort_plan_id",),
        CollaborationSessionRepository,
    ),
    GovernedRepositoryCase("peer_requests", "request_id", ("session_id",), PeerRequestRepository),
    GovernedRepositoryCase(
        "peer_contributions",
        "contribution_id",
        ("session_id", "request_id"),
        PeerContributionRepository,
    ),
    GovernedRepositoryCase("topology_events", "event_id", ("session_id",), TopologyEventRepository),
    GovernedRepositoryCase(
        "collaboration_terminations",
        "session_id",
        (),
        CollaborationTerminationRepository,
    ),
    GovernedRepositoryCase(
        "procedure_compilations",
        "compilation_id",
        (),
        ProcedureCompilationRepository,
    ),
    GovernedRepositoryCase(
        "method_direction_outcomes",
        "outcome_id",
        ("compilation_id",),
        MethodDirectionOutcomeRepository,
    ),
    GovernedRepositoryCase(
        "compiled_progress_plan_bindings",
        "binding_id",
        ("compilation_id",),
        CompiledProgressPlanBindingRepository,
    ),
    GovernedRepositoryCase(
        "guidance_protocols",
        "protocol_id",
        (),
        GuidanceEvaluationProtocolRepository,
    ),
    GovernedRepositoryCase(
        "guidance_cells",
        "cell_id",
        ("protocol_id",),
        GuidanceCellRepository,
    ),
    GovernedRepositoryCase(
        "model_harness_protocols",
        "protocol_id",
        (),
        ModelHarnessProtocolRepository,
    ),
    GovernedRepositoryCase(
        "model_harness_cells",
        "cell_id",
        ("protocol_id",),
        ModelHarnessCellRepository,
    ),
    GovernedRepositoryCase(
        "model_harness_analyses",
        "protocol_id",
        (),
        ModelHarnessAnalysisRepository,
    ),
    GovernedRepositoryCase(
        "harness_execution_traces",
        "trace_id",
        ("protocol_id",),
        HarnessExecutionTraceRepository,
    ),
    GovernedRepositoryCase(
        "reward_assessments",
        "assessment_id",
        ("trace_id", "observation_id"),
        RewardAssessmentRepository,
    ),
)


@pytest.mark.integration
def test_capability_profile_repository_round_trips_and_checks_provenance(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'cognitive.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            record = profile("peer-a")
            proposal = RecordCapabilityProfile(
                proposal_id="proposal-profile-a",
                idempotency_key="proposal-profile-a",
                proposer=actor("coordinator"),
                profile=record,
            )
            _persist_accepted_with_audit(
                RepositorySet(connection),
                proposal,
                POLICY_HASH,
            )
            repository = CapabilityProfileRepository(connection)
            repository.add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=POLICY_HASH,
            )

            assert repository.get(record.profile_id) == record
            stored = connection.execute(
                text(
                    "SELECT schema_version, transaction_id, governing_policy_hash "
                    "FROM capability_profiles WHERE profile_id = :profile_id"
                ),
                {"profile_id": record.profile_id},
            ).one()
            assert stored == (1, proposal.proposal_id, POLICY_HASH)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_capability_profile_repository_rejects_derived_identifier_tamper(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'tamper.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            record = profile("peer-a")
            proposal = RecordCapabilityProfile(
                proposal_id="proposal-profile-a",
                idempotency_key="proposal-profile-a",
                proposer=actor("coordinator"),
                profile=record,
            )
            repository = CapabilityProfileRepository(connection)
            repository.add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=POLICY_HASH,
            )
            connection.exec_driver_sql("DROP TRIGGER capability_profiles_no_update")
            connection.execute(
                text(
                    "UPDATE capability_profiles SET content_hash = :content_hash "
                    "WHERE profile_id = :profile_id"
                ),
                {"content_hash": "a" * 64, "profile_id": record.profile_id},
            )
            with pytest.raises(StorageIntegrityError, match="content_hash does not match"):
                repository.get(record.profile_id)
            canonical_record_json = canonical_json_bytes(record.model_dump(mode="json")).decode()
            connection.execute(
                text(
                    "UPDATE capability_profiles SET content_hash = :content_hash "
                    "WHERE profile_id = :profile_id"
                ),
                {
                    "content_hash": sha256_hex(canonical_record_json.encode()),
                    "profile_id": record.profile_id,
                },
            )
            unknown_payload = json.loads(canonical_record_json)
            unknown_payload["unknown"] = True
            unknown_record_json = canonical_json_bytes(unknown_payload).decode()
            connection.execute(
                text(
                    "UPDATE capability_profiles "
                    "SET record_json = :record_json, content_hash = :content_hash "
                    "WHERE profile_id = :profile_id"
                ),
                {
                    "record_json": unknown_record_json,
                    "content_hash": sha256_hex(unknown_record_json.encode()),
                    "profile_id": record.profile_id,
                },
            )
            with pytest.raises(StorageIntegrityError, match="invalid record JSON"):
                repository.get(record.profile_id)
            connection.execute(
                text(
                    "UPDATE capability_profiles "
                    "SET record_json = :record_json, content_hash = :content_hash "
                    "WHERE profile_id = :profile_id"
                ),
                {
                    "record_json": canonical_record_json,
                    "content_hash": sha256_hex(canonical_record_json.encode()),
                    "profile_id": record.profile_id,
                },
            )
            connection.execute(
                text(
                    "UPDATE capability_profiles SET profile_id = 'profile-forged' "
                    "WHERE profile_id = :profile_id"
                ),
                {"profile_id": record.profile_id},
            )

            with pytest.raises(StorageIntegrityError, match="does not match record_json"):
                repository.get("profile-forged")
    finally:
        engine.dispose()


def _governed_examples() -> tuple[Any, ...]:
    namespace = {
        name: getattr(transaction_models, name)
        for name in (
            *(item.__name__ for item in transaction_models.GOVERNED_PROPOSAL_CLASSES),
            "HarnessTraceRecordMetadata",
            "HarnessExecutionTraceEnvelope",
        )
    }
    return _governed_proposal_examples(namespace)


def _proposal_record_and_id(proposal: Any) -> tuple[Any, str]:
    if isinstance(proposal, RecordCapabilityProfile):
        return proposal.profile, proposal.profile.profile_id
    if isinstance(proposal, RecordCohortPlan):
        return proposal.plan, proposal.plan.cohort_plan_id
    if isinstance(proposal, RecordDiversityAssessment):
        return proposal.assessment, proposal.assessment.diversity_assessment_id
    if isinstance(proposal, RecordCollaborationSession):
        return proposal.session, proposal.session.session_id
    if isinstance(proposal, AppendPeerRequest):
        return proposal.request, proposal.request.request_id
    if isinstance(proposal, AppendPeerContribution):
        return proposal.contribution, proposal.contribution.contribution_id
    if isinstance(proposal, AppendTopologyEvent):
        return proposal.event, proposal.event.event_id
    if isinstance(proposal, RecordCollaborationTermination):
        return proposal.termination, proposal.session_id
    if isinstance(proposal, RecordProcedureCompilation):
        record = ProcedureCompilationRecord.build_from_untrusted_envelope(proposal.compilation)
        return record, record.compilation_id
    if isinstance(proposal, RecordMethodDirectionOutcome):
        return proposal.outcome, proposal.outcome.outcome_id
    if isinstance(proposal, BindCompiledProgressPlan):
        return proposal.binding, proposal.binding.binding_id
    if isinstance(proposal, RecordGuidanceEvaluationProtocol):
        return proposal.protocol, proposal.protocol.protocol_id
    if isinstance(proposal, AppendGuidanceEvaluationCell):
        return proposal.cell, proposal.cell.cell_id
    if isinstance(proposal, RecordModelHarnessProtocol):
        return proposal.protocol, proposal.protocol.protocol_id
    if isinstance(proposal, AppendModelHarnessCell):
        return proposal.cell, proposal.cell.cell_id
    if isinstance(proposal, RecordModelHarnessAnalysis):
        return proposal.analysis, proposal.analysis.protocol_id
    if isinstance(proposal, RecordHarnessExecutionTrace):
        return proposal.envelope.trace, proposal.envelope.trace.trace_id
    if isinstance(proposal, RecordRewardAssessment):
        return proposal.assessment, proposal.assessment.assessment_id
    raise AssertionError(f"unhandled proposal fixture: {type(proposal).__name__}")


def _record_policy_hash(record: Any) -> str:
    return getattr(record, "governing_policy_hash", POLICY_HASH)


def _persist_accepted_with_audit(
    repositories: RepositorySet,
    proposal: Any,
    governing_policy_hash: str,
) -> None:
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    repositories.transactions.add(proposal, decision, NOW)
    event = append_event(
        repositories.audit.last(),
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "policy_hash": governing_policy_hash,
            "stored_policy_hash": governing_policy_hash,
            "transaction_persisted": True,
        },
        NOW,
    )
    repositories.audit.add(event)


def _tamper_column_and_require_failure(
    connection: Connection,
    case: GovernedRepositoryCase,
    repository: Any,
    record_id: str,
    column_name: str,
) -> None:
    original = connection.execute(
        text(
            f"SELECT {column_name} FROM {case.table_name} "
            f"WHERE {case.identifier_column} = :record_id"
        ),
        {"record_id": record_id},
    ).scalar_one()
    forged = f"{original}-forged"
    connection.execute(
        text(
            f"UPDATE {case.table_name} SET {column_name} = :forged "
            f"WHERE {case.identifier_column} = :record_id"
        ),
        {"forged": forged, "record_id": record_id},
    )
    lookup_id = forged if column_name == case.identifier_column else record_id
    with pytest.raises(StorageIntegrityError, match="does not match record_json"):
        repository.get(lookup_id)
    connection.execute(
        text(
            f"UPDATE {case.table_name} SET {column_name} = :original "
            f"WHERE {case.identifier_column} = :lookup_id"
        ),
        {"original": original, "lookup_id": lookup_id},
    )


def _assert_detached_integrity_error(error: StorageIntegrityError, sentinel: str) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    assert sentinel not in str(error)


@pytest.mark.integration
def test_all_18_governed_repositories_real_add_get_and_relationship_tamper(tmp_path) -> None:
    assert len(ALL_18_GOVERNED_REPOSITORY_CASES) == 18
    proposals = _governed_examples()
    assert len(proposals) == len(ALL_18_GOVERNED_REPOSITORY_CASES)
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'all-governed.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            tamper_policy = GovernancePolicy(required_claim_checks=("source_exists",))
            tamper_policy_snapshot = PolicySnapshot(
                policy_hash=policy_hash(tamper_policy),
                policy=tamper_policy,
            )
            repositories.policies.add_and_activate(tamper_policy_snapshot, NOW)
            connection.execute(
                text(
                    "INSERT INTO governance_policies "
                    "(policy_hash, policy_json, created_at) VALUES "
                    "(:policy_f, '{}', :created_at), "
                    "(:policy_a, '{}', :created_at)"
                ),
                {
                    "policy_f": "f" * 64,
                    "policy_a": "a" * 64,
                    "created_at": NOW.isoformat(),
                },
            )
            stored_records: list[tuple[GovernedRepositoryCase, Any, Any, str]] = []
            for case, proposal in zip(
                ALL_18_GOVERNED_REPOSITORY_CASES,
                proposals,
                strict=True,
            ):
                record, record_id = _proposal_record_and_id(proposal)
                _persist_accepted_with_audit(
                    repositories,
                    proposal,
                    _record_policy_hash(record),
                )
                stored_transaction = repositories.transactions.get_by_proposal_id(
                    proposal.proposal_id
                )
                assert stored_transaction is not None
                assert stored_transaction.proposal == proposal
                repository = case.repository_type(connection)
                repository.add_from_proposal(
                    proposal,
                    created_at=NOW,
                    transaction_id=proposal.proposal_id,
                    governing_policy_hash=_record_policy_hash(record),
                )
                assert repository.get(record_id) == record
                provenance = connection.execute(
                    text(
                        f"SELECT transaction_id, governing_policy_hash FROM {case.table_name} "
                        f"WHERE {case.identifier_column} = :record_id"
                    ),
                    {"record_id": record_id},
                ).one()
                assert provenance == (proposal.proposal_id, _record_policy_hash(record))
                connection.exec_driver_sql(f"DROP TRIGGER {case.table_name}_no_update")
                for column_name in (case.identifier_column, *case.relationship_columns):
                    _tamper_column_and_require_failure(
                        connection,
                        case,
                        repository,
                        record_id,
                        column_name,
                    )
                stored_records.append((case, repository, record, record_id))

            for index, (case, repository, _, record_id) in enumerate(stored_records):
                wrong_transaction_id = proposals[(index + 1) % len(proposals)].proposal_id
                connection.execute(
                    text(
                        f"UPDATE {case.table_name} SET transaction_id = :transaction_id "
                        f"WHERE {case.identifier_column} = :record_id"
                    ),
                    {"transaction_id": wrong_transaction_id, "record_id": record_id},
                )
                with pytest.raises(
                    StorageIntegrityError,
                    match="transaction provenance does not match record_json",
                ) as captured:
                    repository.get(record_id)
                _assert_detached_integrity_error(captured.value, "proposal-")
                connection.execute(
                    text(
                        f"UPDATE {case.table_name} SET transaction_id = :transaction_id "
                        f"WHERE {case.identifier_column} = :record_id"
                    ),
                    {
                        "transaction_id": proposals[index].proposal_id,
                        "record_id": record_id,
                    },
                )
            for case, repository, record, record_id in stored_records:
                connection.execute(
                    text(
                        f"UPDATE {case.table_name} SET governing_policy_hash = :policy_hash "
                        f"WHERE {case.identifier_column} = :record_id"
                    ),
                    {
                        "policy_hash": tamper_policy_snapshot.policy_hash,
                        "record_id": record_id,
                    },
                )
                expected_error = (
                    "governing_policy_hash does not match record_json"
                    if "governing_policy_hash" in record.__class__.model_fields
                    else "governing_policy_hash does not match transaction audit"
                )
                with pytest.raises(StorageIntegrityError, match=expected_error) as captured:
                    repository.get(record_id)
                _assert_detached_integrity_error(
                    captured.value,
                    tamper_policy_snapshot.policy_hash,
                )
                connection.execute(
                    text(
                        f"UPDATE {case.table_name} SET governing_policy_hash = :policy_hash "
                        f"WHERE {case.identifier_column} = :record_id"
                    ),
                    {
                        "policy_hash": _record_policy_hash(record),
                        "record_id": record_id,
                    },
                )
            assert repositories.has_durable_state()
            cognitive = repositories.cognitive_integrity_snapshot()
            evaluation = repositories.evaluation_extension_integrity_snapshot()
            assert (
                sum(
                    len(value)
                    for value in (
                        cognitive.capability_profiles,
                        cognitive.cohort_plans,
                        cognitive.diversity_assessments,
                        cognitive.collaboration_sessions,
                        cognitive.peer_requests,
                        cognitive.peer_contributions,
                        cognitive.topology_events,
                        cognitive.terminations,
                        cognitive.compilations,
                        cognitive.method_outcomes,
                        cognitive.bindings,
                    )
                )
                == 11
            )
            assert (
                sum(
                    len(value)
                    for value in (
                        evaluation.guidance_protocols,
                        evaluation.guidance_cells,
                        evaluation.model_harness_protocols,
                        evaluation.model_harness_cells,
                        evaluation.model_harness_analyses,
                        evaluation.harness_execution_traces,
                        evaluation.reward_assessments,
                    )
                )
                == 7
            )
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    "audit_case",
    ("missing", "duplicate", "stored-policy-mismatch", "decision-mismatch", "proposal-mismatch"),
)
def test_governed_read_requires_one_exact_transaction_decision_audit(
    tmp_path,
    audit_case: str,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / f'audit-{audit_case}.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            repositories = RepositorySet(connection)
            proposal = _governed_examples()[4]
            _, record_id = _proposal_record_and_id(proposal)
            decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
            repositories.transactions.add(proposal, decision, NOW)
            PeerRequestRepository(connection).add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=POLICY_HASH,
            )
            if audit_case != "missing":
                audited_proposal = (
                    _governed_examples()[5] if audit_case == "proposal-mismatch" else proposal
                )
                audited_decision = (
                    TransactionDecision(
                        proposal_id=proposal.proposal_id,
                        accepted=False,
                        reasons=(
                            {
                                "code": RejectionCode.PERMISSION_DENIED,
                                "message": "mismatched fixture decision",
                            },
                        ),
                    )
                    if audit_case == "decision-mismatch"
                    else decision
                )
                payload = {
                    "proposal": audited_proposal.model_dump(mode="json"),
                    "decision": audited_decision.model_dump(mode="json"),
                    "policy_hash": POLICY_HASH,
                    "stored_policy_hash": (
                        "e" * 64 if audit_case == "stored-policy-mismatch" else POLICY_HASH
                    ),
                    "transaction_persisted": True,
                }
                event = append_event(
                    repositories.audit.last(),
                    "transaction_decision",
                    payload,
                    NOW,
                )
                repositories.audit.add(event)
                if audit_case == "duplicate":
                    repositories.audit.add(
                        append_event(event, "transaction_decision", payload, NOW)
                    )

            with pytest.raises(
                StorageIntegrityError,
                match="governing_policy_hash does not match transaction audit",
            ) as captured:
                PeerRequestRepository(connection).get(record_id)
            _assert_detached_integrity_error(captured.value, proposal.proposal_id)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_governed_repository_requires_exact_canonical_created_at_and_detached_errors(
    tmp_path,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'canonical-row.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            proposal = _governed_examples()[0]
            record, record_id = _proposal_record_and_id(proposal)
            repositories = RepositorySet(connection)
            sentinel = "SECRET-ADD-SENTINEL"
            invalid_proposal = proposal.model_copy(update={"profile": {"unknown": sentinel}})
            with pytest.raises(
                StorageIntegrityError,
                match="invalid governed proposal",
            ) as captured:
                CapabilityProfileRepository(connection).add_from_proposal(
                    invalid_proposal,
                    created_at=NOW,
                    transaction_id=proposal.proposal_id,
                    governing_policy_hash=_record_policy_hash(record),
                )
            _assert_detached_integrity_error(captured.value, sentinel)
            repositories.transactions.add(
                proposal,
                TransactionDecision(proposal_id=proposal.proposal_id, accepted=True),
                NOW,
            )
            repository = CapabilityProfileRepository(connection)
            repository.add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=_record_policy_hash(record),
            )
            connection.exec_driver_sql("DROP TRIGGER capability_profiles_no_update")
            connection.execute(
                text(
                    "UPDATE capability_profiles SET created_at = :created_at "
                    "WHERE profile_id = :record_id"
                ),
                {"created_at": "2026-08-23T12:00:00Z", "record_id": record_id},
            )
            with pytest.raises(
                StorageIntegrityError,
                match="created_at must use canonical",
            ) as captured:
                repository.get(record_id)
            _assert_detached_integrity_error(captured.value, "2026-08-23")

            sentinel = "SECRET-CORRUPT-SENTINEL"
            corrupt_json = '{"sentinel":"' + sentinel + '"}'
            connection.execute(
                text(
                    "UPDATE capability_profiles "
                    "SET record_json = :record_json, content_hash = :content_hash, "
                    "created_at = :created_at WHERE profile_id = :record_id"
                ),
                {
                    "record_json": corrupt_json,
                    "content_hash": sha256_hex(corrupt_json.encode()),
                    "created_at": NOW.isoformat(),
                    "record_id": record_id,
                },
            )
            with pytest.raises(StorageIntegrityError, match="invalid record JSON") as captured:
                repository.get(record_id)
            _assert_detached_integrity_error(captured.value, sentinel)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_governed_transaction_requires_exact_canonical_proposal_json(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'canonical-transaction.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            proposal = _governed_examples()[0]
            repositories = RepositorySet(connection)
            repositories.transactions.add(
                proposal,
                TransactionDecision(proposal_id=proposal.proposal_id, accepted=True),
                NOW,
            )
            proposal_json = connection.execute(
                text("SELECT proposal_json FROM transactions WHERE proposal_id = :proposal_id"),
                {"proposal_id": proposal.proposal_id},
            ).scalar_one()
            connection.exec_driver_sql("DROP TRIGGER transactions_no_update")
            noncanonical_json = " " + proposal_json
            connection.execute(
                text(
                    "UPDATE transactions SET proposal_json = :proposal_json "
                    "WHERE proposal_id = :proposal_id"
                ),
                {"proposal_json": noncanonical_json, "proposal_id": proposal.proposal_id},
            )
            with pytest.raises(
                StorageIntegrityError, match="invalid transaction record"
            ) as captured:
                repositories.transactions.get_by_proposal_id(proposal.proposal_id)
            _assert_detached_integrity_error(captured.value, proposal.proposal_id)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("case_index", range(18))
def test_has_durable_state_with_exactly_one_governed_table_populated(
    tmp_path,
    case_index: int,
) -> None:
    case = ALL_18_GOVERNED_REPOSITORY_CASES[case_index]
    proposal = _governed_examples()[case_index]
    record, _ = _proposal_record_and_id(proposal)
    database_url = f"sqlite+pysqlite:///{(tmp_path / f'durable-{case_index}.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            case.repository_type(connection).add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash=_record_policy_hash(record),
            )
            populated = tuple(
                candidate.table_name
                for candidate in ALL_18_GOVERNED_REPOSITORY_CASES
                if connection.execute(text(f"SELECT 1 FROM {candidate.table_name} LIMIT 1")).first()
                is not None
            )
            assert populated == (case.table_name,)
            assert RepositorySet(connection).has_durable_state()
    finally:
        engine.dispose()
