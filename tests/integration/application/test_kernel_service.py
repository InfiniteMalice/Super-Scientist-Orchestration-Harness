from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock
from typing import cast

import pytest
from sqlalchemy import Engine

from super_scientist.application.kernel_service import KernelService
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.evidence.models import (
    ArtifactRef,
    EvidenceRecord,
    EvidenceSpan,
    VerificationState,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions import models as transaction_models
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Approval,
    Proposal,
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
from super_scientist.providers.storage.repositories import AuditRepository

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


@dataclass(frozen=True)
class KernelFixture:
    service: KernelService
    uow_factory: Callable[[], DatabaseUnitOfWork]
    artifact_store: FileArtifactStore
    actor: ActorIdentity
    policy: PolicySnapshot

    def add_evidence(
        self,
        proposal_id: str,
        key: str,
        record: EvidenceRecord,
    ) -> AddEvidence:
        return AddEvidence(
            proposal_id=proposal_id,
            idempotency_key=key,
            proposer=self.actor,
            evidence=record,
        )

    def valid_add_evidence(self, proposal_id: str, key: str, content: bytes) -> AddEvidence:
        artifact = self.artifact_store.put(content, "text/plain")
        evidence = EvidenceRecord(
            evidence_id=f"evidence-{proposal_id}",
            evidence_type="observation",
            source_locator=f"fixture://{proposal_id}",
            retrieved_at=NOW,
            artifact=artifact,
            provenance={"collector": "kernel-service-test"},
            ingestion_actor_id=self.actor.actor_id,
            verification_state=VerificationState.UNVERIFIED,
        )
        return AddEvidence(
            proposal_id=proposal_id,
            idempotency_key=key,
            proposer=self.actor,
            evidence=evidence,
        )

    def self_approved_claim(self, proposal_id: str, key: str) -> ProposeClaim:
        claim = AtomicClaim(
            claim_id=f"claim-{proposal_id}",
            version=1,
            proposition="The fixture intervention changes the fixture outcome.",
            scope="Fixture scope",
            population_or_system="Fixture system",
            epistemic_modality="supports",
            status=ClaimStatus.PROPOSED,
            created_at=NOW,
            created_by=self.actor.actor_id,
        )
        return ProposeClaim(
            proposal_id=proposal_id,
            idempotency_key=key,
            proposer=self.actor,
            approval=Approval(approver=self.actor, approved_at=NOW),
            claim=claim,
        )


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _policy_snapshot(required_claim_checks: tuple[str, ...] = ("source_exists",)) -> PolicySnapshot:
    policy = GovernancePolicy(required_claim_checks=required_claim_checks)
    policy_data = policy.model_dump(mode="json")
    policy_data["human_approval_for"] = sorted(policy.human_approval_for)
    return PolicySnapshot(
        policy_hash=sha256_hex(canonical_json_bytes(policy_data)),
        policy=policy,
    )


def _build_kernel(tmp_path: Path) -> tuple[KernelFixture, Engine]:
    database_url = _database_url(tmp_path / "kernel.db")
    upgrade_database(database_url)
    engine: Engine = create_database_engine(database_url)
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    actor = ActorIdentity(actor_id="scientist-1", kind=ActorKind.HUMAN, created_at=NOW)
    policy = _policy_snapshot()

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    return (
        KernelFixture(
            service=KernelService(uow_factory, policy, FixedClock(), artifact_store),
            uow_factory=uow_factory,
            artifact_store=artifact_store,
            actor=actor,
            policy=policy,
        ),
        engine,
    )


@pytest.fixture
def kernel(tmp_path: Path) -> Iterator[KernelFixture]:
    fixture, engine = _build_kernel(tmp_path)
    with fixture.uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(fixture.policy, NOW)
    yield fixture
    engine.dispose()


@pytest.fixture
def unregistered_kernel(tmp_path: Path) -> Iterator[KernelFixture]:
    fixture, engine = _build_kernel(tmp_path)
    yield fixture
    engine.dispose()


def test_accepted_evidence_is_committed_with_audit(kernel: KernelFixture) -> None:
    proposal = kernel.valid_add_evidence("p-1", "k-1", b"observation")

    decision = kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert decision.accepted
        stored = repositories.evidence.get(proposal.evidence.evidence_id)
        assert stored == proposal.evidence.model_copy(
            update={"verification_state": VerificationState.HASH_VERIFIED}
        )
        assert repositories.audit.list_all()[-1].payload["decision"]["accepted"] is True


def _record_for_ref(
    kernel: KernelFixture,
    artifact: ArtifactRef,
    *,
    extracted_span: EvidenceSpan | None = None,
    verification_state: VerificationState = VerificationState.UNVERIFIED,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="evidence-adversarial",
        evidence_type="observation",
        source_locator="fixture://adversarial",
        retrieved_at=NOW,
        artifact=artifact,
        extracted_span=extracted_span,
        provenance={"collector": "kernel-service-test"},
        ingestion_actor_id=kernel.actor.actor_id,
        verification_state=verification_state,
    )


def _assert_durable_hash_rejection(kernel: KernelFixture, proposal: AddEvidence) -> None:
    decision = kernel.service.submit(proposal)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.EVIDENCE_HASH_MISMATCH
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
        assert stored is not None
        assert stored.decision == decision
        assert repositories.evidence.get(proposal.evidence.evidence_id) is None
        assert repositories.audit.list_all()[-1].payload["decision"]["reasons"][0]["code"] == (
            RejectionCode.EVIDENCE_HASH_MISMATCH.value
        )


def test_nonexistent_artifact_is_durably_rejected(kernel: KernelFixture) -> None:
    digest = sha256_hex(b"missing")
    artifact = ArtifactRef(
        sha256=digest,
        size_bytes=7,
        media_type="application/octet-stream",
        relative_path=f"sha256/{digest[:2]}/{digest}",
    )
    proposal = kernel.add_evidence(
        "proposal-missing",
        "key-missing",
        _record_for_ref(kernel, artifact),
    )

    _assert_durable_hash_rejection(kernel, proposal)


def test_tampered_artifact_is_durably_rejected(kernel: KernelFixture) -> None:
    artifact = kernel.artifact_store.put(b"original", "application/octet-stream")
    kernel.artifact_store.resolve(artifact).write_bytes(b"tampered")
    proposal = kernel.add_evidence(
        "proposal-tampered",
        "key-tampered",
        _record_for_ref(kernel, artifact),
    )

    _assert_durable_hash_rejection(kernel, proposal)


def test_wrong_digest_reference_is_durably_rejected(kernel: KernelFixture) -> None:
    artifact = kernel.artifact_store.put(b"original", "application/octet-stream")
    wrong_digest = sha256_hex(b"different")
    wrong_ref = artifact.model_copy(
        update={
            "sha256": wrong_digest,
            "relative_path": f"sha256/{wrong_digest[:2]}/{wrong_digest}",
        }
    )
    proposal = kernel.add_evidence(
        "proposal-digest",
        "key-digest",
        _record_for_ref(kernel, wrong_ref),
    )

    _assert_durable_hash_rejection(kernel, proposal)


def test_wrong_artifact_size_is_durably_rejected(kernel: KernelFixture) -> None:
    artifact = kernel.artifact_store.put(b"original", "application/octet-stream")
    proposal = kernel.add_evidence(
        "proposal-size",
        "key-size",
        _record_for_ref(kernel, artifact.model_copy(update={"size_bytes": 999})),
    )

    _assert_durable_hash_rejection(kernel, proposal)


def test_caller_claimed_hash_verification_is_durably_rejected(kernel: KernelFixture) -> None:
    artifact = kernel.artifact_store.put(b"original", "application/octet-stream")
    proposal = kernel.add_evidence(
        "proposal-claimed-verified",
        "key-claimed-verified",
        _record_for_ref(
            kernel,
            artifact,
            verification_state=VerificationState.HASH_VERIFIED,
        ),
    )

    _assert_durable_hash_rejection(kernel, proposal)


def test_text_span_must_bind_to_artifact_text(kernel: KernelFixture) -> None:
    artifact = kernel.artifact_store.put(b"actual", "text/plain")
    proposal = kernel.add_evidence(
        "proposal-span",
        "key-span",
        _record_for_ref(
            kernel,
            artifact,
            extracted_span=EvidenceSpan(start=0, end=6, text="unreal"),
        ),
    )

    _assert_durable_hash_rejection(kernel, proposal)


@pytest.mark.parametrize(
    ("content", "media_type", "span"),
    [
        (b"binary\x00evidence", "application/octet-stream", None),
        (b"prefix exact suffix", "text/plain", EvidenceSpan(start=7, end=12, text="exact")),
    ],
)
def test_unverified_binary_and_text_evidence_are_verified_before_projection(
    kernel: KernelFixture,
    content: bytes,
    media_type: str,
    span: EvidenceSpan | None,
) -> None:
    artifact = kernel.artifact_store.put(content, media_type)
    proposal = kernel.add_evidence(
        f"proposal-valid-{media_type}",
        f"key-valid-{media_type}",
        _record_for_ref(kernel, artifact, extracted_span=span),
    )

    decision = kernel.service.submit(proposal)

    assert decision.accepted
    with kernel.uow_factory() as unit_of_work:
        stored = unit_of_work.repositories().evidence.get(proposal.evidence.evidence_id)
        assert stored is not None
        assert stored.verification_state is VerificationState.HASH_VERIFIED
        assert stored.extracted_span == span


def test_rejected_claim_is_audited_but_not_projected(kernel: KernelFixture) -> None:
    proposal = kernel.self_approved_claim("p-2", "k-2")

    decision = kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert not decision.accepted
        assert repositories.claims.get_head(proposal.claim.claim_id) is None
        assert repositories.audit.list_all()[-1].payload["decision"]["accepted"] is False


def test_transition_projects_the_exact_intended_next_claim(kernel: KernelFixture) -> None:
    artifact = kernel.artifact_store.put(b"supporting fixture span", "text/plain")
    evidence = _record_for_ref(
        kernel,
        artifact,
        extracted_span=EvidenceSpan(
            start=0,
            end=len("supporting fixture span"),
            text="supporting fixture span",
        ),
    ).model_copy(update={"evidence_id": "transition-evidence"})
    assert kernel.service.submit(
        kernel.add_evidence("proposal-transition-evidence", "key-transition-evidence", evidence)
    ).accepted
    current = AtomicClaim(
        claim_id="claim-transition",
        version=1,
        proposition="The fixture contains a supporting span.",
        scope="fixture",
        population_or_system="fixture system",
        epistemic_modality="observed",
        status=ClaimStatus.PROPOSED,
        created_at=NOW,
        created_by=kernel.actor.actor_id,
    )
    assert kernel.service.submit(
        ProposeClaim(
            proposal_id="proposal-transition-claim",
            idempotency_key="key-transition-claim",
            proposer=kernel.actor,
            claim=current,
        )
    ).accepted
    next_claim = AtomicClaim(
        claim_id=current.claim_id,
        version=2,
        proposition=current.proposition,
        scope=current.scope,
        population_or_system=current.population_or_system,
        epistemic_modality=current.epistemic_modality,
        status=ClaimStatus.EVIDENCE_LINKED,
        evidence_links=(
            EvidenceLink(
                evidence_id=evidence.evidence_id,
                supporting_span="fixture span",
            ),
        ),
        assumptions=("The fixture is stable.",),
        parent_version_id=f"{current.claim_id}:1",
        created_at=NOW + timedelta(seconds=1),
        created_by=kernel.actor.actor_id,
    )
    transition = TransitionClaim(
        proposal_id="proposal-transition",
        idempotency_key="key-transition",
        proposer=kernel.actor,
        next_claim=next_claim,
    )

    decision = kernel.service.submit(transition)

    assert decision.accepted
    with kernel.uow_factory() as unit_of_work:
        history = unit_of_work.repositories().claims.history(current.claim_id)
        assert history == (current, next_claim)


def test_duplicate_submission_returns_original_decision(kernel: KernelFixture) -> None:
    proposal = kernel.valid_add_evidence("p-3", "k-3", b"same")

    first = kernel.service.submit(proposal)
    second = kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        assert second.replayed
        assert second.model_copy(update={"replayed": False}) == first
        assert len(unit_of_work.repositories().audit.list_all()) == 1


def test_concurrent_identical_intents_create_one_proposal_and_one_replay(
    kernel: KernelFixture,
) -> None:
    proposal = kernel.valid_add_evidence("proposal-concurrent", "key-concurrent", b"same")
    attempt = transaction_models.ProposalAttempt(
        proposal_id=proposal.proposal_id,
        idempotency_key=proposal.idempotency_key,
        proposer=proposal.proposer,
        proposal_kind="add_evidence",
    )
    barrier = Barrier(3)
    factory_lock = Lock()
    factory_calls = 0

    def proposal_factory() -> AddEvidence:
        nonlocal factory_calls
        with factory_lock:
            factory_calls += 1
        return proposal

    def submit() -> TransactionDecision:
        barrier.wait(timeout=10)
        return kernel.service.submit_intent(attempt, proposal_factory)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit) for _ in range(2)]
        barrier.wait(timeout=10)
        decisions = [future.result(timeout=10) for future in futures]

    assert factory_calls == 1
    assert sorted(decision.replayed for decision in decisions) == [False, True]
    assert all(decision.accepted for decision in decisions)
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert len(repositories.evidence.list_all()) == 1
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 1


def test_reused_idempotency_key_with_new_content_is_rejected_and_audited(
    kernel: KernelFixture,
) -> None:
    first = kernel.valid_add_evidence("p-4", "shared-key", b"first")
    conflicting = kernel.valid_add_evidence("p-5", "shared-key", b"different")

    assert kernel.service.submit(first).accepted
    decision = kernel.service.submit(conflicting)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(first.idempotency_key)
        assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
        assert repositories.evidence.get(conflicting.evidence.evidence_id) is None
        assert stored is not None
        assert stored.proposal == first
        assert stored.decision.accepted
        assert len(repositories.audit.list_all()) == 2


def test_exact_retry_replays_when_constructor_policy_is_stale(kernel: KernelFixture) -> None:
    proposal = kernel.valid_add_evidence("p-5", "k-5", b"observation")
    first = kernel.service.submit(proposal)
    stale_policy = _policy_snapshot(("source_exists", "evidence_span_exists"))
    stale_service = KernelService(
        kernel.uow_factory,
        stale_policy,
        FixedClock(),
        kernel.artifact_store,
    )

    replay = stale_service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert replay.replayed
        assert replay.model_copy(update={"replayed": False}) == first
        assert len(repositories.audit.list_all()) == 1


def test_idempotency_conflict_is_audited_when_constructor_policy_is_stale(
    kernel: KernelFixture,
) -> None:
    first = kernel.valid_add_evidence("p-6", "shared-key", b"first")
    conflicting = kernel.valid_add_evidence("p-7", "shared-key", b"different")
    stale_policy = _policy_snapshot(("source_exists", "evidence_span_exists"))
    stale_service = KernelService(
        kernel.uow_factory,
        stale_policy,
        FixedClock(),
        kernel.artifact_store,
    )

    assert kernel.service.submit(first).accepted
    decision = stale_service.submit(conflicting)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(first.idempotency_key)
        assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
        assert repositories.evidence.get(conflicting.evidence.evidence_id) is None
        assert stored is not None
        assert stored.proposal == first
        assert stored.decision.accepted
        events = repositories.audit.list_all()
        assert len(events) == 2
        assert "configured_policy_hash" not in events[-1].payload
        assert verify_workspace(repositories, kernel.artifact_store).valid


def test_missing_active_policy_is_rejected_without_unauthoritative_audit(
    unregistered_kernel: KernelFixture,
) -> None:
    proposal = unregistered_kernel.valid_add_evidence("p-5", "k-5", b"observation")

    decision = unregistered_kernel.service.submit(proposal)

    with unregistered_kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
        assert decision.reasons[0].code is RejectionCode.POLICY_HASH_MISMATCH
        assert repositories.evidence.get(proposal.evidence.evidence_id) is None
        assert stored is None
        assert repositories.audit.list_all() == ()


def test_mismatched_active_policy_is_rejected_and_audited(kernel: KernelFixture) -> None:
    stored_policy = _policy_snapshot(("source_exists", "evidence_span_exists"))
    with kernel.uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(stored_policy, NOW)
    proposal = kernel.valid_add_evidence("p-6", "k-6", b"observation")

    decision = kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
        assert decision.reasons[0].code is RejectionCode.POLICY_HASH_MISMATCH
        assert repositories.evidence.get(proposal.evidence.evidence_id) is None
        assert stored is not None
        assert stored.decision == decision
        payload = repositories.audit.list_all()[-1].payload
        assert payload["decision"]["accepted"] is False
        assert payload["policy_hash"] == stored_policy.policy_hash
        assert payload["configured_policy_hash"] == kernel.policy.policy_hash
        assert payload["stored_policy_hash"] == stored_policy.policy_hash


def test_reused_proposal_id_is_rejected_and_audited(kernel: KernelFixture) -> None:
    first = kernel.valid_add_evidence("p-7", "k-7", b"first")
    colliding = kernel.valid_add_evidence("p-7", "k-8", b"different")
    colliding = colliding.model_copy(
        update={
            "evidence": colliding.evidence.model_copy(
                update={"evidence_id": "evidence-p-7-conflict"}
            )
        }
    )

    assert kernel.service.submit(first).accepted
    decision = kernel.service.submit(colliding)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert decision.reasons[0].code is RejectionCode.ENTITY_ALREADY_EXISTS
        assert repositories.evidence.get(first.evidence.evidence_id) == first.evidence.model_copy(
            update={"verification_state": VerificationState.HASH_VERIFIED}
        )
        assert repositories.evidence.get(colliding.evidence.evidence_id) is None
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 2


@pytest.mark.parametrize("malformation", ["construct", "unchecked-copy"])
def test_service_durably_rejects_malformed_proposal_with_recoverable_identity(
    kernel: KernelFixture,
    malformation: str,
) -> None:
    if malformation == "construct":
        malformed = AddEvidence.model_construct(
            proposal_type="add_evidence",
            proposal_id="proposal-malformed-construct",
            idempotency_key="key-malformed-construct",
        )
    else:
        malformed = kernel.valid_add_evidence(
            "proposal-malformed-copy",
            "key-malformed-copy",
            b"unused",
        ).model_copy(update={"evidence": {"evidence_id": "incomplete"}})

    decision = kernel.service.submit(cast(Proposal, malformed))

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(
            cast(str, malformed.idempotency_key)
        )
        assert stored is not None
        assert stored.decision == decision
        assert len(repositories.audit.list_all()) == 1
        assert repositories.evidence.list_all() == ()


def test_service_returns_stable_nondurable_rejection_when_identity_is_unusable(
    kernel: KernelFixture,
) -> None:
    malformed = AddEvidence.model_construct(
        proposal_type="add_evidence",
        proposal_id="   ",
        idempotency_key=object(),
    )

    decision = kernel.service.submit(cast(Proposal, malformed))

    assert not decision.accepted
    assert decision.proposal_id == "invalid-proposal"
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert repositories.transactions.list_all() == ()
        assert repositories.audit.list_all() == ()


def test_nested_extra_cannot_collapse_into_clean_proposal_transaction(
    kernel: KernelFixture,
) -> None:
    clean = kernel.valid_add_evidence(
        "proposal-nested-extra",
        "key-nested-extra",
        b"strict",
    )
    payload = clean.model_dump(mode="json")
    payload["evidence"]["artifact"]["unexpected_field"] = "must be rejected"

    malformed_decision = kernel.service.submit(payload)
    clean_decision = kernel.service.submit(clean)

    assert not malformed_decision.accepted
    assert malformed_decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    assert not clean_decision.accepted
    assert clean_decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(clean.idempotency_key)
        assert stored is not None
        assert isinstance(stored.proposal, transaction_models.InvalidProposal)
        assert stored.proposal_hash != sha256_hex(
            canonical_json_bytes(clean.model_dump(mode="json"))
        )
        assert repositories.evidence.list_all() == ()
        assert len(repositories.audit.list_all()) == 2


def test_submit_intent_durably_rejects_malformed_factory_result(
    kernel: KernelFixture,
) -> None:
    malformed = AddEvidence.model_construct(
        proposal_type="add_evidence",
        proposal_id="proposal-malformed-factory",
        idempotency_key="key-malformed-factory",
    )
    attempt = transaction_models.ProposalAttempt(
        proposal_id="proposal-malformed-factory",
        idempotency_key="key-malformed-factory",
        proposer=kernel.actor,
        proposal_kind="add_evidence",
    )

    decision = kernel.service.submit_intent(
        attempt,
        lambda: cast(Proposal, malformed),
    )

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key("key-malformed-factory")
        assert stored is not None
        assert stored.decision == decision
        assert len(repositories.audit.list_all()) == 1


def test_submit_intent_durably_audits_validation_failure_and_replays_before_factory(
    kernel: KernelFixture,
) -> None:
    proposal = kernel.valid_add_evidence(
        "proposal-invalid-attempt",
        "key-invalid-attempt",
        b"unused",
    )
    attempt = transaction_models.ProposalAttempt(
        proposal_id=proposal.proposal_id,
        idempotency_key=proposal.idempotency_key,
        proposer=proposal.proposer,
        proposal_kind="add_evidence",
    )
    factory_calls = 0

    def invalid_factory() -> AddEvidence:
        nonlocal factory_calls
        factory_calls += 1
        evidence = proposal.evidence
        invalid_evidence = EvidenceRecord(
            evidence_id=evidence.evidence_id,
            evidence_type=evidence.evidence_type,
            source_locator="   ",
            retrieved_at=evidence.retrieved_at,
            artifact=evidence.artifact,
            extracted_span=evidence.extracted_span,
            structured_observation=evidence.structured_observation,
            provenance=evidence.provenance,
            license=evidence.license,
            ingestion_actor_id=evidence.ingestion_actor_id,
            verification_state=evidence.verification_state,
        )
        return proposal.model_copy(update={"evidence": invalid_evidence})

    first = kernel.service.submit_intent(attempt, invalid_factory)
    second = kernel.service.submit_intent(attempt, invalid_factory)

    assert factory_calls == 1
    assert not first.accepted
    assert first.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    assert second.replayed
    assert second.model_copy(update={"replayed": False}) == first
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key(attempt.idempotency_key)
        assert stored is not None
        assert isinstance(stored.proposal, transaction_models.InvalidProposal)
        assert stored.proposal.proposer == attempt.proposer
        assert stored.proposal.attempted_proposal_kind == attempt.proposal_kind
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 1
        assert repositories.evidence.list_all() == ()


def test_submit_intent_propagates_unexpected_factory_error_and_rolls_back(
    kernel: KernelFixture,
) -> None:
    proposal = kernel.valid_add_evidence(
        "proposal-runtime-error",
        "key-runtime-error",
        b"unused",
    )
    attempt = transaction_models.ProposalAttempt(
        proposal_id=proposal.proposal_id,
        idempotency_key=proposal.idempotency_key,
        proposer=proposal.proposer,
        proposal_kind="add_evidence",
    )
    factory_calls = 0

    def broken_factory() -> AddEvidence:
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError("factory programming error")

    with pytest.raises(RuntimeError, match="factory programming error"):
        kernel.service.submit_intent(attempt, broken_factory)

    assert factory_calls == 1
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert repositories.transactions.list_all() == ()
        assert repositories.audit.list_all() == ()
        assert repositories.evidence.list_all() == ()


def test_submit_intent_rejects_factory_key_mismatch_without_raising(
    kernel: KernelFixture,
) -> None:
    proposal = kernel.valid_add_evidence(
        "proposal-wrong-factory-key",
        "different-key",
        b"unused",
    )
    attempt = transaction_models.ProposalAttempt(
        proposal_id=proposal.proposal_id,
        idempotency_key="expected-key",
        proposer=proposal.proposer,
        proposal_kind="add_evidence",
    )

    decision = kernel.service.submit_intent(attempt, lambda: proposal)

    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        stored = repositories.transactions.get_by_idempotency_key("expected-key")
        assert stored is not None
        assert stored.decision == decision
        assert repositories.evidence.list_all() == ()


def test_conflict_audit_id_cannot_collide_with_later_proposal_id(
    kernel: KernelFixture,
) -> None:
    first = kernel.valid_add_evidence("p", "key-first", b"first")
    conflict = kernel.valid_add_evidence("p", "key-conflict", b"conflict")
    conflict = conflict.model_copy(
        update={
            "evidence": conflict.evidence.model_copy(update={"evidence_id": "evidence-conflict"})
        }
    )
    later = kernel.valid_add_evidence("p-2", "key-later", b"later")

    assert kernel.service.submit(first).accepted
    assert not kernel.service.submit(conflict).accepted
    assert kernel.service.submit(later).accepted

    with kernel.uow_factory() as unit_of_work:
        events = unit_of_work.repositories().audit.list_all()
        assert [event.event_id for event in events] == [
            "audit-event-00000000000000000001",
            "audit-event-00000000000000000002",
            "audit-event-00000000000000000003",
        ]


def test_audit_failure_rolls_back_database_rows_but_not_prepared_artifact(
    kernel: KernelFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = kernel.valid_add_evidence("p-6", "k-6", b"observation")

    def fail_add(self: AuditRepository, event: object) -> None:
        del self, event
        raise RuntimeError("disk failure")

    monkeypatch.setattr(AuditRepository, "add", fail_add)

    with pytest.raises(RuntimeError, match="disk failure"):
        kernel.service.submit(proposal)

    with kernel.uow_factory() as unit_of_work:
        repositories = unit_of_work.repositories()
        assert repositories.evidence.get(proposal.evidence.evidence_id) is None
        assert repositories.transactions.get_by_idempotency_key(proposal.idempotency_key) is None
        assert repositories.audit.list_all() == ()
    assert kernel.artifact_store.read(proposal.evidence.artifact) == b"observation"
