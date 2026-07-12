from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.admission.engine import AdmissionContext, AdmissionEngine
from super_scientist.kernel.transactions.models import AddEvidence, TransactionDecision


def _proposal(key: str) -> AddEvidence:
    proposer = ActorIdentity(
        actor_id="actor-1",
        kind=ActorKind.HUMAN,
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
    )
    evidence = EvidenceRecord(
        evidence_id="evidence-1",
        evidence_type="document",
        source_locator="fixture://one",
        retrieved_at=datetime(2026, 7, 12, tzinfo=UTC),
        artifact=ArtifactRef(
            sha256="a" * 64,
            size_bytes=1,
            media_type="text/plain",
            relative_path=f"sha256/aa/{'a' * 64}",
        ),
        provenance={"collector": "property-test"},
        ingestion_actor_id="actor-1",
    )
    return AddEvidence(
        proposal_id="proposal-1",
        idempotency_key=key,
        proposer=proposer,
        evidence=evidence,
    )


def _context(
    prior: dict[str, TransactionDecision] | None = None,
) -> AdmissionContext:
    return AdmissionContext(
        active_policy=PolicySnapshot(
            policy_hash="b" * 64,
            policy=GovernancePolicy(
                required_claim_checks=["source_exists", "evidence_span_exists"]
            ),
        ),
        evidence_by_id={},
        claim_by_id={},
        prior_decision_by_idempotency_key=prior or {},
    )


@given(st.text(min_size=1, max_size=32))
def test_same_idempotency_key_replays_same_decision(key: str) -> None:
    proposal = _proposal(key)
    engine = AdmissionEngine()
    first = engine.decide(proposal, _context())

    replay = engine.decide(proposal, _context(prior={key: first}))

    assert replay.replayed
    assert replay.model_copy(update={"replayed": False}) == first
