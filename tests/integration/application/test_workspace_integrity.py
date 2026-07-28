import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, func, insert, select, update

from super_scientist.application.kernel_service import KernelService
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.evidence.models import (
    EvidenceRecord,
    EvidenceSpan,
    VerificationState,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    InvalidProposal,
    ProposalAttempt,
    ProposeClaim,
    RejectionCode,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import (
    audit_events,
    claim_heads,
    governance_policies,
    governance_state,
    transactions,
)
from super_scientist.quality import imported_pattern_firewall, runner
from super_scientist.quality.imported_pattern_firewall import (
    QualityPolicyBinding,
    current_quality_policy_binding,
    quality_policy_hash,
)
from super_scientist.quality.runner import QualityCheck

NOW = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class IntegrityFixture:
    engine: Engine
    service: KernelService
    artifacts: FileArtifactStore
    actor: ActorIdentity
    policy: PolicySnapshot

    def uow(self) -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(self.engine)

    def evidence_proposal(self, proposal_id: str = "proposal-evidence") -> AddEvidence:
        artifact = self.artifacts.put(b"authoritative evidence", "application/octet-stream")
        return AddEvidence(
            proposal_id=proposal_id,
            idempotency_key=f"key-{proposal_id}",
            proposer=self.actor,
            evidence=EvidenceRecord(
                evidence_id=f"evidence-{proposal_id}",
                evidence_type="observation",
                source_locator=f"fixture://{proposal_id}",
                retrieved_at=NOW,
                artifact=artifact,
                provenance={"collector": "integrity-test"},
                ingestion_actor_id=self.actor.actor_id,
                verification_state=VerificationState.UNVERIFIED,
            ),
        )

    def claim_proposal(self) -> ProposeClaim:
        return ProposeClaim(
            proposal_id="proposal-claim",
            idempotency_key="key-claim",
            proposer=self.actor,
            claim=AtomicClaim(
                claim_id="claim-1",
                version=1,
                proposition="The fixture is intact.",
                scope="fixture",
                population_or_system="fixture system",
                epistemic_modality="observed",
                status=ClaimStatus.PROPOSED,
                created_at=NOW,
                created_by=self.actor.actor_id,
            ),
        )


@pytest.fixture
def integrity(tmp_path: Path) -> Iterator[IntegrityFixture]:
    database_url = f"sqlite:///{(tmp_path / 'kernel.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    actor = ActorIdentity(actor_id="actor-1", kind=ActorKind.HUMAN, created_at=NOW)
    policy = GovernancePolicy(required_claim_checks=("source_exists",))
    snapshot = PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)
    with DatabaseUnitOfWork(engine) as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(snapshot, NOW)
    service = KernelService(
        lambda: DatabaseUnitOfWork(engine),
        snapshot,
        FixedClock(),
        artifacts,
    )
    yield IntegrityFixture(
        engine=engine,
        service=service,
        artifacts=artifacts,
        actor=actor,
        policy=snapshot,
    )
    engine.dispose()


def _verify(integrity: IntegrityFixture) -> object:
    with integrity.uow() as unit_of_work:
        return verify_workspace(unit_of_work.repositories(), integrity.artifacts)


def test_workspace_verifier_recomputes_composite_quality_policy_hash(
    integrity: IntegrityFixture,
) -> None:
    binding = current_quality_policy_binding()
    tampered = binding.model_construct(
        registry_hash=binding.registry_hash,
        firewall_policy_sha256=binding.firewall_policy_sha256,
        allowed_attribution_paths=binding.allowed_attribution_paths,
        quality_policy_hash="f" * 64,
    )

    with integrity.uow() as unit_of_work:
        result = verify_workspace(
            unit_of_work.repositories(),
            integrity.artifacts,
            quality_policy_binding=tampered,
        )

    assert result.valid is False
    assert "quality policy" in result.reason


def test_workspace_verifier_rejects_self_consistent_nonactive_quality_policy(
    integrity: IntegrityFixture,
) -> None:
    active = current_quality_policy_binding()
    foreign_registry_hash = "a" * 64
    foreign = QualityPolicyBinding(
        registry_hash=foreign_registry_hash,
        firewall_policy_sha256=active.firewall_policy_sha256,
        allowed_attribution_paths=active.allowed_attribution_paths,
        quality_policy_hash=quality_policy_hash(
            registry_hash=foreign_registry_hash,
            firewall_policy_sha256=active.firewall_policy_sha256,
            allowed_attribution_paths=active.allowed_attribution_paths,
        ),
    )

    with integrity.uow() as unit_of_work:
        result = verify_workspace(
            unit_of_work.repositories(),
            integrity.artifacts,
            quality_policy_binding=foreign,
        )

    assert result.valid is False
    assert "approved quality policy anchor" in result.reason


def test_workspace_verifier_uses_independent_anchor_when_all_executable_inputs_mutate(
    integrity: IntegrityFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner,
        "CHECKS",
        (
            *runner.CHECKS,
            QualityCheck(
                "mutated-check",
                (
                    runner.PYTHON,
                    "-m",
                    "super_scientist.quality.wheel_smoke",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        imported_pattern_firewall,
        "PINNED_POLICY_SHA256",
        "b" * 64,
    )
    monkeypatch.setattr(
        imported_pattern_firewall,
        "ALLOWED_ATTRIBUTION_PATHS",
        ("docs/mutated-attribution.md",),
    )

    with integrity.uow() as unit_of_work:
        result = verify_workspace(
            unit_of_work.repositories(),
            integrity.artifacts,
        )

    assert result.valid is False
    assert "approved quality policy anchor" in result.reason


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_workspace_verifier_rehashes_every_projected_artifact(
    integrity: IntegrityFixture,
    damage: str,
) -> None:
    proposal = integrity.evidence_proposal()
    assert integrity.service.submit(proposal).accepted
    path = integrity.artifacts.resolve(proposal.evidence.artifact)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"tampered")

    result = _verify(integrity)

    assert not result.valid
    assert "artifact" in result.reason


def test_workspace_verifier_rechecks_stored_text_span_binding(
    integrity: IntegrityFixture,
) -> None:
    artifact = integrity.artifacts.put(b"authoritative evidence", "text/plain")
    proposal = AddEvidence(
        proposal_id="proposal-invalid-span",
        idempotency_key="key-invalid-span",
        proposer=integrity.actor,
        evidence=EvidenceRecord(
            evidence_id="evidence-invalid-span",
            evidence_type="observation",
            source_locator="fixture://invalid-span",
            retrieved_at=NOW,
            artifact=artifact,
            extracted_span=EvidenceSpan(start=0, end=5, text="wrong"),
            provenance={"collector": "integrity-test"},
            ingestion_actor_id=integrity.actor.actor_id,
        ),
    )
    decision = TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
    event = append_event(
        None,
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "policy_hash": integrity.policy.policy_hash,
            "transaction_persisted": True,
        },
        NOW,
    )
    with integrity.uow() as unit_of_work:
        repositories = unit_of_work.repositories()
        repositories.evidence.add(
            proposal.evidence.model_copy(
                update={"verification_state": VerificationState.HASH_VERIFIED}
            )
        )
        repositories.transactions.add(proposal, decision, NOW)
        repositories.audit.add(event)

    result = _verify(integrity)

    assert not result.valid
    assert "span" in result.reason


def test_workspace_verifier_detects_corrupt_claim_head(
    integrity: IntegrityFixture,
) -> None:
    proposal = integrity.claim_proposal()
    assert integrity.service.submit(proposal).accepted
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.execute(
            update(claim_heads)
            .where(claim_heads.c.claim_id == proposal.claim.claim_id)
            .values(version=99)
        )

    result = _verify(integrity)

    assert not result.valid
    assert "claim head" in result.reason


def test_workspace_verifier_detects_transaction_audit_mismatch(
    integrity: IntegrityFixture,
) -> None:
    proposal = integrity.claim_proposal()
    assert integrity.service.submit(proposal).accepted
    mismatch = TransactionDecision(
        proposal_id=proposal.proposal_id,
        accepted=False,
        reasons=(
            {
                "code": RejectionCode.PERMISSION_DENIED,
                "message": "mismatched fixture decision",
            },
        ),
    )
    replacement = append_event(
        None,
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": mismatch.model_dump(mode="json"),
            "policy_hash": integrity.policy.policy_hash,
            "transaction_persisted": True,
        },
        NOW,
    )
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.exec_driver_sql("DROP TRIGGER audit_events_no_update")
        unit_of_work.connection.execute(
            update(audit_events)
            .where(audit_events.c.sequence == 1)
            .values(
                event_id=replacement.event_id,
                previous_hash=replacement.previous_hash,
                payload_hash=replacement.payload_hash,
                event_hash=replacement.event_hash,
                event_json=replacement.model_dump_json(),
            )
        )

    result = _verify(integrity)

    assert not result.valid
    assert "transaction" in result.reason


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_exact_replay_fails_closed_on_artifact_corruption_without_new_audit(
    integrity: IntegrityFixture,
    damage: str,
) -> None:
    proposal = integrity.evidence_proposal()
    assert integrity.service.submit(proposal).accepted
    path = integrity.artifacts.resolve(proposal.evidence.artifact)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"tampered")

    with pytest.raises(StorageIntegrityError, match="workspace integrity"):
        integrity.service.submit(proposal)

    with integrity.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(audit_events)).scalar_one() == 1


def test_workspace_verifier_binds_service_owned_intent_fingerprint_to_audit(
    integrity: IntegrityFixture,
) -> None:
    proposal = integrity.evidence_proposal()
    attempt = ProposalAttempt(
        proposal_id=proposal.proposal_id,
        idempotency_key=proposal.idempotency_key,
        proposer=proposal.proposer,
        proposal_kind="add_evidence",
        intent_digest=sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json"))),
    )
    assert integrity.service.submit_intent(attempt, lambda: proposal).accepted
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.exec_driver_sql("DROP TRIGGER transactions_no_update")
        unit_of_work.connection.execute(update(transactions).values(intent_fingerprint="f" * 64))

    result = _verify(integrity)

    assert not result.valid
    assert "transaction" in result.reason or "fingerprint" in result.reason


def test_workspace_verifier_rejects_lost_rejected_transaction_row(
    integrity: IntegrityFixture,
) -> None:
    proposal = InvalidProposal(
        proposal_id="proposal-rejected-loss",
        idempotency_key="key-rejected-loss",
        validation_error="fixture rejection",
    )
    decision = integrity.service.submit(proposal)
    assert not decision.accepted
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.exec_driver_sql("DROP TRIGGER transactions_no_delete")
        unit_of_work.connection.execute(delete(transactions))

    result = _verify(integrity)

    assert not result.valid
    assert "transaction" in result.reason


@pytest.mark.parametrize("damage", ["missing-pointer", "missing-policy-row"])
def test_durable_workspace_and_exact_replay_require_registered_active_policy(
    integrity: IntegrityFixture,
    damage: str,
) -> None:
    proposal = integrity.evidence_proposal()
    assert integrity.service.submit(proposal).accepted
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        if damage == "missing-pointer":
            unit_of_work.connection.execute(delete(governance_state))
        else:
            unit_of_work.connection.exec_driver_sql("DROP TRIGGER governance_policies_no_delete")
            unit_of_work.connection.execute(
                delete(governance_policies).where(
                    governance_policies.c.policy_hash == integrity.policy.policy_hash
                )
            )

    result = _verify(integrity)

    assert not result.valid
    assert "policy" in result.reason
    with pytest.raises(StorageIntegrityError, match="workspace integrity"):
        integrity.service.submit(proposal)
    with integrity.engine.connect() as connection:
        assert connection.execute(select(func.count()).select_from(transactions)).scalar_one() == 1
        assert connection.execute(select(func.count()).select_from(audit_events)).scalar_one() == 1


def test_workspace_verifier_rejects_unregistered_audit_policy_reference(
    integrity: IntegrityFixture,
) -> None:
    proposal = integrity.claim_proposal()
    decision = integrity.service.submit(proposal)
    replacement = append_event(
        None,
        "transaction_decision",
        {
            "proposal": proposal.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
            "policy_hash": "f" * 64,
            "transaction_persisted": True,
        },
        NOW,
    )
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.exec_driver_sql("DROP TRIGGER audit_events_no_update")
        unit_of_work.connection.execute(
            update(audit_events)
            .where(audit_events.c.sequence == 1)
            .values(
                event_id=replacement.event_id,
                previous_hash=replacement.previous_hash,
                payload_hash=replacement.payload_hash,
                event_hash=replacement.event_hash,
                event_json=replacement.model_dump_json(),
            )
        )

    result = _verify(integrity)

    assert not result.valid
    assert "policy" in result.reason


@pytest.mark.parametrize("damage", ["extra-policy", "second-state"])
def test_workspace_verifier_validates_every_governance_row(
    integrity: IntegrityFixture,
    damage: str,
) -> None:
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        if damage == "extra-policy":
            unit_of_work.connection.execute(
                insert(governance_policies).values(
                    policy_hash="f" * 64,
                    policy_json='{"schema_version":2,"required_claim_checks":["source_exists"]}',
                    created_at=NOW.isoformat(),
                )
            )
        else:
            unit_of_work.connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
            unit_of_work.connection.execute(
                insert(governance_state).values(
                    singleton_id=2,
                    active_policy_hash=integrity.policy.policy_hash,
                )
            )

    result = _verify(integrity)

    assert not result.valid
    assert "governance" in result.reason or "policy" in result.reason


@pytest.mark.parametrize("damage", ["extra", "missing-version"])
def test_workspace_verifier_rejects_noncanonical_stored_audit_envelope(
    integrity: IntegrityFixture,
    damage: str,
) -> None:
    proposal = integrity.claim_proposal()
    assert integrity.service.submit(proposal).accepted
    with integrity.uow() as unit_of_work:
        assert unit_of_work.connection is not None
        raw = unit_of_work.connection.execute(select(audit_events.c.event_json)).scalar_one()
        envelope = json.loads(raw)
        if damage == "extra":
            envelope["unexpected_field"] = "must be rejected"
        else:
            del envelope["schema_version"]
        unit_of_work.connection.exec_driver_sql("DROP TRIGGER audit_events_no_update")
        unit_of_work.connection.execute(
            update(audit_events).values(
                event_json=json.dumps(envelope, sort_keys=True, separators=(",", ":"))
            )
        )

    result = _verify(integrity)

    assert not result.valid
    assert "audit" in result.reason


def test_workspace_verifier_checks_links_on_withdrawn_history(
    integrity: IntegrityFixture,
) -> None:
    proposal = integrity.claim_proposal()
    assert integrity.service.submit(proposal).accepted
    withdrawn = proposal.claim.model_copy(
        update={
            "version": 2,
            "status": ClaimStatus.WITHDRAWN,
            "evidence_links": (
                EvidenceLink(evidence_id="missing-evidence", supporting_span="missing span"),
            ),
            "parent_version_id": f"{proposal.claim.claim_id}:1",
            "created_by": integrity.actor.actor_id,
        }
    )
    transition = TransitionClaim(
        proposal_id="proposal-corrupt-withdrawal",
        idempotency_key="key-corrupt-withdrawal",
        proposer=integrity.actor,
        next_claim=withdrawn,
    )
    decision = TransactionDecision(proposal_id=transition.proposal_id, accepted=True)
    with integrity.uow() as unit_of_work:
        repositories = unit_of_work.repositories()
        repositories.claims.add_version(withdrawn)
        repositories.transactions.add(transition, decision, NOW)
        repositories.audit.add(
            append_event(
                repositories.audit.last(),
                "transaction_decision",
                {
                    "proposal": transition.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                    "policy_hash": integrity.policy.policy_hash,
                    "transaction_persisted": True,
                },
                NOW,
            )
        )

    result = _verify(integrity)

    assert not result.valid
    assert "evidence links" in result.reason
