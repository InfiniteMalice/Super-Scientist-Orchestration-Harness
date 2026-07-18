from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import TypeAdapter
from sqlalchemy.exc import SQLAlchemyError

from super_scientist.application.evidence_verification import verify_artifact_binding
from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.configurations.models import ConfigurationVersion
from super_scientist.domain.evaluators.models import (
    EvaluatorSuccessionDecision,
    EvaluatorVersion,
)
from super_scientist.domain.evidence.models import EvidenceRecord, VerificationState
from super_scientist.domain.improvement.models import (
    EvaluatorAuditRecord,
    SelfImprovementMeasurementRecord,
)
from super_scientist.domain.primitives import Sha256Hex, canonical_json_bytes
from super_scientist.domain.research_runs.models import ResearchRun, ResearchRunEvent
from super_scientist.evaluation.claim_drift.deterministic import run_deterministic_checks
from super_scientist.evaluation.claim_drift.models import CheckOutcome
from super_scientist.kernel.audit.models import (
    AuditEvent,
    AuditVerification,
    json_compatible_payload,
)
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    AppendResearchRunEvent,
    CreateResearchRun,
    DecideEvaluatorSuccession,
    Proposal,
    ProposeClaim,
    ProposeEvaluatorVersion,
    ProposeGovernancePolicyTransition,
    RecordConfigurationVersion,
    RecordEvaluatorAudit,
    RecordSelfImprovementMeasurement,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.artifacts import ArtifactStore
from super_scientist.providers.storage.integrity_records import AdaptationIntegritySnapshot
from super_scientist.providers.storage.repositories import (
    RepositorySet,
    StorageIntegrityError,
    StoredTransaction,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
SHA256_ADAPTER: TypeAdapter[Sha256Hex] = TypeAdapter(Sha256Hex)


@dataclass(frozen=True)
class _ValidatedAuditRecord:
    proposal: Proposal
    decision: TransactionDecision
    intent_fingerprint: str | None
    transaction_persisted: bool
    governing_policy_hash: str
    payload: Mapping[str, object]


def verify_workspace(
    repositories: RepositorySet,
    artifact_store: ArtifactStore,
) -> AuditVerification:
    events: tuple[AuditEvent, ...] = ()
    try:
        active_policy = repositories.policies.get_active()
        policies = repositories.policies.list_all()
        evidence = repositories.evidence.list_all()
        heads = repositories.claims.list_heads()
        adaptation = repositories.adaptation_integrity_snapshot()
        transactions = repositories.transactions.list_all()
        events = repositories.audit.list_all()
        _require(
            active_policy is not None or not repositories.has_durable_state(),
            "durable workspace state requires an active registered policy",
        )
        audit_records = _validated_audit_records(events, repositories)
        _require_transaction_audit_consistency(transactions, audit_records)
        _require_projection_consistency(
            repositories,
            audit_records,
            evidence,
            heads,
            adaptation,
            policies,
            active_policy,
        )
        _require_artifact_consistency(evidence, artifact_store)
        _require_claim_evidence_consistency(repositories, heads, evidence)
    except (KeyError, OSError, SQLAlchemyError, TypeError, ValueError) as error:
        return AuditVerification(
            valid=False,
            checked_events=len(events),
            reason=f"workspace integrity error: {error}",
        )
    return AuditVerification(valid=True, checked_events=len(events))


def require_workspace_integrity(
    repositories: RepositorySet,
    artifact_store: ArtifactStore,
) -> None:
    result = verify_workspace(repositories, artifact_store)
    if not result.valid:
        raise StorageIntegrityError(result.reason or "workspace integrity verification failed")


def _validated_audit_records(
    events: tuple[AuditEvent, ...],
    repositories: RepositorySet,
) -> tuple[_ValidatedAuditRecord, ...]:
    records: list[_ValidatedAuditRecord] = []
    for event in events:
        _require(event.event_type == "transaction_decision", "unexpected audit event type")
        payload = json_compatible_payload(event.payload)
        proposal = PROPOSAL_ADAPTER.validate_json(
            canonical_json_bytes(_mapping_value(payload, "proposal"))
        )
        decision = TransactionDecision.model_validate_json(
            canonical_json_bytes(_mapping_value(payload, "decision"))
        )
        governing_hash = SHA256_ADAPTER.validate_python(_mapping_value(payload, "policy_hash"))
        _require(
            repositories.policies.get(governing_hash) is not None,
            "audit event governing policy is not registered",
        )
        configured_hash = _optional_policy_hash(payload, "configured_policy_hash")
        stored_hash = _optional_policy_hash(payload, "stored_policy_hash")
        intent_fingerprint = _optional_policy_hash(payload, "intent_fingerprint")
        transaction_persisted = _strict_bool(payload, "transaction_persisted")
        if configured_hash is not None:
            _require(
                repositories.policies.get(configured_hash) is not None,
                "audit event configured policy is not registered",
            )
        if stored_hash is not None:
            _require(
                repositories.policies.get(stored_hash) is not None,
                "audit event stored policy is not registered",
            )
            _require(
                stored_hash == governing_hash,
                "audit event governing and stored policies do not match",
            )
        _require(
            proposal.proposal_id == decision.proposal_id,
            "audit proposal and decision identifiers do not match",
        )
        if isinstance(proposal, ProposeGovernancePolicyTransition):
            _require(
                _optional_policy_hash(payload, "prior_policy_hash") == proposal.prior_policy_hash,
                "transition audit prior policy hash does not match proposal",
            )
            _require(
                _optional_policy_hash(payload, "candidate_policy_hash")
                == proposal.candidate_policy_snapshot.policy_hash,
                "transition audit candidate policy hash does not match proposal",
            )
            _require(
                _optional_policy_hash(payload, "rollback_policy_hash")
                == proposal.rollback_policy_hash,
                "transition audit rollback policy hash does not match proposal",
            )
        records.append(
            _ValidatedAuditRecord(
                proposal=proposal,
                decision=decision,
                intent_fingerprint=intent_fingerprint,
                transaction_persisted=transaction_persisted,
                governing_policy_hash=governing_hash,
                payload=payload,
            )
        )
    return tuple(records)


def _require_transaction_audit_consistency(
    transactions: tuple[StoredTransaction, ...],
    audit_records: tuple[_ValidatedAuditRecord, ...],
) -> None:
    def key(
        proposal: Proposal,
        decision: TransactionDecision,
        intent_fingerprint: str | None,
    ) -> tuple[bytes, bytes, str | None]:
        return (
            canonical_json_bytes(proposal.model_dump(mode="json")),
            canonical_json_bytes(decision.model_dump(mode="json")),
            intent_fingerprint,
        )

    transaction_keys = {
        key(transaction.proposal, transaction.decision, transaction.intent_fingerprint)
        for transaction in transactions
    }
    persisted_audit_counts = Counter(
        key(record.proposal, record.decision, record.intent_fingerprint)
        for record in audit_records
        if record.transaction_persisted
    )
    for transaction in transactions:
        transaction_key = key(
            transaction.proposal,
            transaction.decision,
            transaction.intent_fingerprint,
        )
        _require(
            persisted_audit_counts[transaction_key] == 1,
            "transaction does not have one exact audit decision",
        )
    for record in audit_records:
        exact_transaction_exists = (
            key(record.proposal, record.decision, record.intent_fingerprint) in transaction_keys
        )
        _require(
            exact_transaction_exists == record.transaction_persisted,
            "audit transaction persistence does not match stored transactions",
        )
        _require(
            not record.decision.accepted or record.transaction_persisted,
            "accepted audit decision has no stored transaction",
        )


def _require_projection_consistency(
    repositories: RepositorySet,
    audit_records: tuple[_ValidatedAuditRecord, ...],
    evidence: tuple[EvidenceRecord, ...],
    heads: tuple[AtomicClaim, ...],
    adaptation: AdaptationIntegritySnapshot,
    policies: tuple[PolicySnapshot, ...],
    active_policy: PolicySnapshot | None,
) -> None:
    expected_evidence: dict[str, EvidenceRecord] = {}
    expected_claims: dict[tuple[str, int], AtomicClaim] = {}
    expected_runs: dict[str, ResearchRun] = {}
    expected_run_events: dict[str, ResearchRunEvent] = {}
    expected_configurations: dict[str, ConfigurationVersion] = {}
    expected_audits: dict[str, EvaluatorAuditRecord] = {}
    expected_measurements: dict[str, SelfImprovementMeasurementRecord] = {}
    expected_evaluator_versions: dict[str, EvaluatorVersion] = {}
    expected_succession_decisions: dict[str, EvaluatorSuccessionDecision] = {}
    expected_run_heads: dict[str, str] = {}
    expected_evaluator_head: str | None = None
    accepted_evaluator_succession = False
    transitions: list[tuple[ProposeGovernancePolicyTransition, str]] = []
    for audit_record in audit_records:
        if not (audit_record.transaction_persisted and audit_record.decision.accepted):
            continue
        proposal = audit_record.proposal
        if isinstance(proposal, AddEvidence):
            projected = proposal.evidence.model_copy(
                update={"verification_state": VerificationState.HASH_VERIFIED}
            )
            _add_unique(expected_evidence, projected.evidence_id, projected, "evidence projection")
        elif isinstance(proposal, ProposeClaim):
            _add_unique(
                expected_claims,
                (proposal.claim.claim_id, proposal.claim.version),
                proposal.claim,
                "claim projection",
            )
        elif isinstance(proposal, TransitionClaim):
            _add_unique(
                expected_claims,
                (proposal.next_claim.claim_id, proposal.next_claim.version),
                proposal.next_claim,
                "claim projection",
            )
        elif isinstance(proposal, CreateResearchRun):
            _require_governing_hash(
                proposal.run.active_governance_policy_hash,
                audit_record.governing_policy_hash,
                "research run",
            )
            _add_unique(expected_runs, proposal.run.run_id, proposal.run, "research run projection")
        elif isinstance(proposal, AppendResearchRunEvent):
            _require_governing_hash(
                proposal.event.governing_policy_hash,
                audit_record.governing_policy_hash,
                "research run event",
            )
            _require(
                proposal.event.run_id in expected_runs,
                "research run event transaction precedes its run",
            )
            _add_unique(
                expected_run_events,
                proposal.event.run_event_id,
                proposal.event,
                "research run event projection",
            )
            expected_run_heads[proposal.event.run_id] = proposal.event.run_event_id
        elif isinstance(proposal, RecordConfigurationVersion):
            configuration = proposal.configuration_version
            _require_governing_hash(
                configuration.governing_policy_hash,
                audit_record.governing_policy_hash,
                "configuration version",
            )
            _add_unique(
                expected_configurations,
                configuration.configuration_version_id,
                configuration,
                "configuration projection",
            )
        elif isinstance(proposal, RecordEvaluatorAudit):
            evaluator_audit = proposal.evaluator_audit
            _require_governing_hash(
                evaluator_audit.governing_policy_hash,
                audit_record.governing_policy_hash,
                "evaluator audit",
            )
            _add_unique(
                expected_audits,
                evaluator_audit.evaluator_audit_id,
                evaluator_audit,
                "evaluator audit projection",
            )
        elif isinstance(proposal, RecordSelfImprovementMeasurement):
            _add_expected_measurement(
                proposal.measurement,
                audit_record.governing_policy_hash,
                expected_runs,
                expected_audits,
                expected_measurements,
            )
        elif isinstance(proposal, ProposeEvaluatorVersion):
            evaluator_version = proposal.evaluator_version
            _require_governing_hash(
                evaluator_version.governing_policy_hash,
                audit_record.governing_policy_hash,
                "evaluator version",
            )
            _add_unique(
                expected_evaluator_versions,
                evaluator_version.evaluator_version_id,
                evaluator_version,
                "evaluator version projection",
            )
        elif isinstance(proposal, DecideEvaluatorSuccession):
            succession = proposal.succession_decision
            _require_governing_hash(
                succession.governing_policy_hash,
                audit_record.governing_policy_hash,
                "evaluator succession",
            )
            _require(
                succession.predecessor_evaluator_version_id in expected_evaluator_versions
                and succession.candidate_evaluator_version_id in expected_evaluator_versions,
                "evaluator succession references an unprojected evaluator version",
            )
            _add_unique(
                expected_succession_decisions,
                succession.evaluator_succession_decision_id,
                succession,
                "evaluator succession projection",
            )
            if succession.accepted:
                if accepted_evaluator_succession:
                    _require(
                        succession.predecessor_evaluator_version_id == expected_evaluator_head,
                        "evaluator succession does not continue the replay-derived head",
                    )
                else:
                    root_ids = {
                        evaluator_version_id
                        for evaluator_version_id, evaluator_version in (
                            expected_evaluator_versions.items()
                        )
                        if evaluator_version.predecessor_evaluator_version_id is None
                    }
                    _require(
                        root_ids == {succession.predecessor_evaluator_version_id},
                        "first evaluator succession must start from the unique root evaluator",
                    )
                expected_evaluator_head = succession.candidate_evaluator_version_id
                accepted_evaluator_succession = True
        elif isinstance(proposal, ProposeGovernancePolicyTransition):
            _require(
                audit_record.governing_policy_hash == proposal.prior_policy_hash,
                "accepted transition audit must be governed by its prior policy",
            )
            _require_governing_hash(
                proposal.research_run.active_governance_policy_hash,
                proposal.prior_policy_hash,
                "transition research run",
            )
            _require_governing_hash(
                proposal.evaluator_audit.governing_policy_hash,
                proposal.prior_policy_hash,
                "transition evaluator audit",
            )
            _add_unique(
                expected_runs,
                proposal.research_run.run_id,
                proposal.research_run,
                "research run projection",
            )
            _add_unique(
                expected_audits,
                proposal.evaluator_audit.evaluator_audit_id,
                proposal.evaluator_audit,
                "evaluator audit projection",
            )
            _add_expected_measurement(
                proposal.measurement,
                proposal.prior_policy_hash,
                expected_runs,
                expected_audits,
                expected_measurements,
            )
            transitions.append((proposal, audit_record.governing_policy_hash))

    actual_evidence = {record.evidence_id: record for record in evidence}
    _require(actual_evidence == expected_evidence, "evidence projections do not match transactions")

    actual_claims: dict[tuple[str, int], AtomicClaim] = {}
    for head in heads:
        for claim in repositories.claims.history(head.claim_id):
            _add_unique(
                actual_claims,
                (claim.claim_id, claim.version),
                claim,
                "stored claim version",
            )
    _require(actual_claims == expected_claims, "claim projections do not match transactions")
    _require(
        {record.run_id: record for record in adaptation.research_runs} == expected_runs,
        "research run projections do not match accepted transactions",
    )
    _require(
        {record.run_event_id: record for record in adaptation.research_run_events}
        == expected_run_events,
        "research run event projections do not match accepted transactions",
    )
    _require(
        {record.configuration_version_id: record for record in adaptation.configuration_versions}
        == expected_configurations,
        "configuration projections do not match accepted transactions",
    )
    _require(
        {record.evaluator_audit_id: record for record in adaptation.evaluator_audits}
        == expected_audits,
        "evaluator audit projections do not match accepted transactions",
    )
    _require(
        {record.measurement_id: record for record in adaptation.measurements}
        == expected_measurements,
        "measurement projections do not match accepted transactions",
    )
    _require(
        {record.evaluator_version_id: record for record in adaptation.evaluator_versions}
        == expected_evaluator_versions,
        "evaluator version projections do not match accepted transactions",
    )
    _require(
        {
            record.evaluator_succession_decision_id: record
            for record in adaptation.evaluator_succession_decisions
        }
        == expected_succession_decisions,
        "evaluator succession projections do not match accepted transactions",
    )
    _require(
        not adaptation.evaluator_collapse_records,
        "evaluator collapse records have no accepted transaction projection",
    )
    _require(
        dict(adaptation.research_run_heads) == expected_run_heads,
        "research run heads do not match accepted event transactions",
    )
    if accepted_evaluator_succession:
        _require(
            adaptation.evaluator_head == expected_evaluator_head,
            "evaluator head does not match accepted succession transactions",
        )
    else:
        root_ids = {
            evaluator_version_id
            for evaluator_version_id, evaluator_version in expected_evaluator_versions.items()
            if evaluator_version.predecessor_evaluator_version_id is None
        }
        allowed_baseline_heads: set[str | None] = {None}
        if len(root_ids) == 1:
            allowed_baseline_heads.update(root_ids)
        _require(
            adaptation.evaluator_head in allowed_baseline_heads,
            "evaluator head is neither empty nor the unique root evaluator",
        )
    _require_policy_projection_consistency(
        policies,
        active_policy,
        tuple(transitions),
    )


def _add_expected_measurement(
    measurement: SelfImprovementMeasurementRecord,
    governing_policy_hash: str,
    expected_runs: dict[str, ResearchRun],
    expected_audits: dict[str, EvaluatorAuditRecord],
    expected_measurements: dict[str, SelfImprovementMeasurementRecord],
) -> None:
    _require_governing_hash(
        measurement.governing_policy_hash,
        governing_policy_hash,
        "measurement",
    )
    _require(
        measurement.run_id in expected_runs and measurement.evaluator_audit_id in expected_audits,
        "measurement references an unprojected run or evaluator audit",
    )
    _add_unique(
        expected_measurements,
        measurement.measurement_id,
        measurement,
        "measurement projection",
    )


def _require_policy_projection_consistency(
    policies: tuple[PolicySnapshot, ...],
    active_policy: PolicySnapshot | None,
    transitions: tuple[tuple[ProposeGovernancePolicyTransition, str], ...],
) -> None:
    actual_policies: dict[str, PolicySnapshot] = {}
    for snapshot in policies:
        _add_unique(actual_policies, snapshot.policy_hash, snapshot, "governance policy")
    if not transitions:
        _require(
            (not actual_policies and active_policy is None)
            or (
                active_policy is not None
                and actual_policies.get(active_policy.policy_hash) == active_policy
            ),
            "active policy pointer does not name a registered governance policy",
        )
        return
    first_transition = transitions[0][0]
    initial = actual_policies.get(first_transition.prior_policy_hash)
    if initial is None:
        raise StorageIntegrityError("transition prior policy is not registered")
    expected_policies = {initial.policy_hash: initial}
    replay_active_hash = initial.policy_hash
    for proposal, governing_policy_hash in transitions:
        _require(
            proposal.prior_policy_hash == replay_active_hash
            and governing_policy_hash == replay_active_hash,
            "governance transition does not continue the replay-derived active policy",
        )
        _require(
            proposal.rollback_policy_hash in expected_policies,
            "governance transition rollback policy is not in prior accepted history",
        )
        candidate = proposal.candidate_policy_snapshot
        prior_candidate = expected_policies.get(candidate.policy_hash)
        _require(
            prior_candidate is None or prior_candidate == candidate,
            "governance candidate hash is reused with different policy content",
        )
        expected_policies[candidate.policy_hash] = candidate
        replay_active_hash = candidate.policy_hash
    _require(
        actual_policies == expected_policies,
        "governance policies do not match accepted transition transactions",
    )
    _require(
        active_policy is not None and active_policy.policy_hash == replay_active_hash,
        "active policy pointer does not match accepted transition replay",
    )


def _require_governing_hash(actual: str, expected: str, label: str) -> None:
    _require(actual == expected, f"{label} does not name its transaction governing policy")


def _require_artifact_consistency(
    evidence: tuple[EvidenceRecord, ...],
    artifact_store: ArtifactStore,
) -> None:
    for record in evidence:
        _require(
            record.verification_state is VerificationState.HASH_VERIFIED,
            f"authoritative evidence {record.evidence_id} is not hash verified",
        )
        verify_artifact_binding(record, artifact_store)


def _require_claim_evidence_consistency(
    repositories: RepositorySet,
    heads: tuple[AtomicClaim, ...],
    evidence: tuple[EvidenceRecord, ...],
) -> None:
    evidence_by_id = {record.evidence_id: record for record in evidence}
    for head in heads:
        for claim in repositories.claims.history(head.claim_id):
            if claim.status is ClaimStatus.PROPOSED:
                continue
            if claim.status is ClaimStatus.WITHDRAWN and not claim.evidence_links:
                continue
            checks = run_deterministic_checks(claim, evidence_by_id)
            _require(
                all(check.outcome is CheckOutcome.PASS_DETERMINISTIC for check in checks),
                f"claim {claim.claim_id}:{claim.version} has invalid evidence links",
            )


def _mapping_value(mapping: Mapping[str, object], key: str) -> object:
    if key not in mapping:
        raise StorageIntegrityError(f"audit payload is missing {key}")
    return mapping[key]


def _optional_policy_hash(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    return SHA256_ADAPTER.validate_python(value)


def _strict_bool(mapping: Mapping[str, object], key: str) -> bool:
    value = _mapping_value(mapping, key)
    if type(value) is not bool:
        raise StorageIntegrityError(f"audit payload {key} must be a boolean")
    return value


def _add_unique[KeyT, ValueT](
    values: dict[KeyT, ValueT],
    key: KeyT,
    value: ValueT,
    label: str,
) -> None:
    _require(key not in values, f"duplicate {label}")
    values[key] = value


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise StorageIntegrityError(detail)
