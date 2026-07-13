import json
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, EvidenceSpan
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    ProposeClaim,
    TransitionClaim,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
NOW = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)


def _actor() -> ActorIdentity:
    return ActorIdentity(
        actor_id="model-actor",
        kind=ActorKind.MODEL,
        created_at=NOW,
        provider_id="provider",
        model_id="model",
        adapter_id="adapter",
        configuration_hash="a" * 64,
    )


def _evidence_proposal() -> AddEvidence:
    actor = _actor()
    text = "support"
    return AddEvidence(
        proposal_id="proposal-evidence",
        idempotency_key="key-evidence",
        proposer=actor,
        evidence=EvidenceRecord(
            evidence_id="evidence-1",
            evidence_type="document",
            source_locator="fixture://strict",
            retrieved_at=NOW,
            artifact=ArtifactRef(
                sha256="b" * 64,
                size_bytes=len(text),
                media_type="text/plain",
                relative_path=f"sha256/bb/{'b' * 64}",
            ),
            extracted_span=EvidenceSpan(start=0, end=len(text), text=text),
            structured_observation={"score": 1, "labels": ["strict", "json"]},
            provenance={"collector": "test"},
            ingestion_actor_id=actor.actor_id,
        ),
    )


def _claim(status: ClaimStatus, version: int) -> AtomicClaim:
    link = EvidenceLink(evidence_id="evidence-1", supporting_span="support")
    return AtomicClaim(
        claim_id="claim-1",
        version=version,
        proposition="Strict parsing is required.",
        scope="fixture",
        population_or_system="fixture system",
        epistemic_modality="observed",
        status=status,
        evidence_links=(() if status is ClaimStatus.PROPOSED else (link,)),
        assumptions=("serialized tuples are accepted",),
        parent_version_id=(None if version == 1 else f"claim-1:{version - 1}"),
        created_at=NOW,
        created_by=_actor().actor_id,
    )


def _proposal_payload(kind: str) -> dict[str, object]:
    if kind == "evidence":
        proposal: Proposal = _evidence_proposal()
    elif kind == "claim":
        proposal = ProposeClaim(
            proposal_id="proposal-claim",
            idempotency_key="key-claim",
            proposer=_actor(),
            claim=_claim(ClaimStatus.PROPOSED, 1),
        )
    else:
        proposal = TransitionClaim(
            proposal_id="proposal-transition",
            idempotency_key="key-transition",
            proposer=_actor(),
            next_claim=_claim(ClaimStatus.EVIDENCE_LINKED, 2),
        )
    return proposal.model_dump(mode="json")


def _add_extra(payload: dict[str, object], path: tuple[str | int, ...]) -> None:
    target: object = payload
    for segment in path:
        if isinstance(segment, int):
            assert isinstance(target, list)
            target = target[segment]
        else:
            assert isinstance(target, dict)
            target = target[segment]
    assert isinstance(target, dict)
    target["unexpected_field"] = "must be rejected"


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("evidence", ("proposer",)),
        ("evidence", ("evidence",)),
        ("evidence", ("evidence", "artifact")),
        ("evidence", ("evidence", "extracted_span")),
        ("claim", ("claim",)),
        ("transition", ("next_claim", "evidence_links", 0)),
        ("transition", ("next_claim",)),
    ],
    ids=[
        "model-identity",
        "evidence-record",
        "artifact",
        "span",
        "claim",
        "evidence-link",
        "transition-next-claim",
    ],
)
def test_external_proposal_json_forbids_nested_extras(
    kind: str,
    path: tuple[str | int, ...],
) -> None:
    payload = _proposal_payload(kind)
    _add_extra(payload, path)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        PROPOSAL_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize("kind", ["evidence", "claim", "transition"])
def test_strict_external_parser_accepts_legitimate_json_collections(kind: str) -> None:
    payload = _proposal_payload(kind)

    parsed = PROPOSAL_ADAPTER.validate_json(json.dumps(payload))

    assert parsed.proposal_id == payload["proposal_id"]
