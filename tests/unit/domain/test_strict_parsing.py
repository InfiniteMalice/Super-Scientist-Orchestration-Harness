import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus, EvidenceLink
from super_scientist.domain.cognition import (
    CapabilityAssessment,
    CapabilityProfile,
    CohortRequest,
    DiversityFingerprint,
)
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord, EvidenceSpan
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    ProposeClaim,
    TransitionClaim,
)

PROPOSAL_ADAPTER: TypeAdapter[Proposal] = TypeAdapter(Proposal)
NOW = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)


def test_task_8_and_13_trace_boundary_contract_source_compiles() -> None:
    plan = (
        Path(__file__).parents[3]
        / "docs"
        / "superpowers"
        / "plans"
        / "2026-08-23-governed-cognitive-cohorts-procedure-compilation.md"
    ).read_text(encoding="utf-8")
    start_marker = "<!-- task-8-13-trace-contract:start -->"
    end_marker = "<!-- task-8-13-trace-contract:end -->"
    source = plan.split(start_marker, 1)[1].split(end_marker, 1)[0]

    compile(source, "task-8-13-trace-contract", "exec")
    assert source.count("class HarnessTraceRecordMetadata") == 1
    assert source.count("class HarnessExecutionTraceEnvelope") == 1
    assert source.count("class RecordHarnessExecutionTrace") == 1
    assert "RecordHarnessExecutionTraceProposal" not in plan
    assert "-> RecordHarnessExecutionTrace" in plan
    assert "envelope=HarnessExecutionTraceEnvelope" in plan
    assert "proposal.expectation" not in plan
    assert "proposal.verification" not in plan
    assert "proposal.diagnostic_coverage" not in plan


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


def test_cognition_records_forbid_extras_and_coercive_schema_versions() -> None:
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-strict",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )
    profile = CapabilityProfile.build(
        profile_id="profile-strict",
        actor=_actor(),
        diversity_fingerprint=fingerprint,
        governing_policy_hash="f" * 64,
    )
    request = CohortRequest.build(
        request_id="request-strict",
        task_id="task-strict",
        required_capabilities=(),
        preferred_capabilities=(),
        min_members=0,
        max_members=1,
        candidate_actor_ids=("model-actor",),
        prohibited_combinations=(),
        governing_policy_hash="f" * 64,
    )

    profile_payload = profile.model_dump(mode="json") | {"unexpected": True}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CapabilityProfile.model_validate_json(json.dumps(profile_payload), strict=True)

    request_payload = request.model_dump(mode="json") | {"schema_version": "1"}
    with pytest.raises(ValidationError):
        CohortRequest.model_validate_json(json.dumps(request_payload), strict=True)

    assert (
        CapabilityProfile.model_validate_json(
            json.dumps(profile.model_dump(mode="json")),
            strict=True,
        )
        == profile
    )


def test_cognition_json_parser_accepts_bounded_tuple_collections() -> None:
    request = CohortRequest.build(
        request_id="request-json",
        task_id="task-json",
        required_capabilities=(),
        preferred_capabilities=(),
        min_members=0,
        max_members=2,
        candidate_actor_ids=("peer-a", "peer-b"),
        prohibited_combinations=(("peer-a", "peer-b"),),
        governing_policy_hash="f" * 64,
    )

    parsed = CohortRequest.model_validate_json(
        json.dumps(request.model_dump(mode="json")),
        strict=True,
    )

    assert parsed == request


def test_cognition_rejects_unbounded_nested_actor_identity() -> None:
    actor = _actor().model_copy(update={"provider_id": "p" * 10_000})
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-bounded-actor",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )

    with pytest.raises(ValidationError, match="actor identity"):
        CapabilityProfile.build(
            profile_id="profile-bounded-actor",
            actor=actor,
            diversity_fingerprint=fingerprint,
            governing_policy_hash="f" * 64,
        )


def test_cognition_rejects_coercive_nested_actor_timestamp() -> None:
    actor_payload = _actor().model_dump(mode="python")
    actor_payload["created_at"] = NOW.isoformat()
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-strict-actor",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )

    with pytest.raises(ValidationError, match="actor identity"):
        CapabilityProfile.build(
            profile_id="profile-strict-actor",
            actor=actor_payload,
            diversity_fingerprint=fingerprint,
            governing_policy_hash="f" * 64,
        )


def test_cognition_rejects_coercive_nested_actor_kind() -> None:
    actor_payload = _actor().model_dump(mode="python")
    actor_payload["kind"] = "model"
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-strict-actor-kind",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )

    with pytest.raises(ValidationError, match="actor identity"):
        CapabilityProfile.build(
            profile_id="profile-strict-actor-kind",
            actor=actor_payload,
            diversity_fingerprint=fingerprint,
            governing_policy_hash="f" * 64,
        )


def test_cognition_revalidates_preconstructed_actor_identity() -> None:
    malformed_actor = _actor().model_copy(update={"kind": "model"})
    fingerprint = DiversityFingerprint(
        fingerprint_id="fingerprint-copied-actor",
        model_family=None,
        model_version=None,
        scale_class=None,
        provider=None,
        adapter_hash=None,
        configuration_hash=None,
        prompt_strategy=None,
        methodological_prior=None,
        tools=None,
        evidence_partitions=None,
        modalities=None,
        previous_error_clusters=None,
        prior_task_specializations=None,
    )

    valid = CapabilityProfile.build(
        profile_id="profile-copied-actor",
        actor=_actor(),
        diversity_fingerprint=fingerprint,
        governing_policy_hash="f" * 64,
    )
    payload = valid.model_dump(mode="python")
    payload["actor"] = malformed_actor
    rehashed = valid.model_copy(update={"actor": malformed_actor}).model_dump(
        mode="json", warnings=False
    )
    payload["content_hash"] = sha256_hex(
        canonical_json_bytes(
            {key: value for key, value in rehashed.items() if key != "content_hash"}
        )
    )

    with pytest.raises(ValidationError, match="actor identity"):
        CapabilityProfile(**payload)


def test_strict_capability_assessment_parser_rejects_self_report_as_satisfied() -> None:
    payload = {
        "schema_version": 1,
        "profile_id": "profile-self-report",
        "actor_id": "model-actor",
        "requirement": {
            "schema_version": 1,
            "requirement_id": "requirement-self-report",
            "capability_id": "analysis",
            "task_family_id": "research",
            "evidence_snapshot_hash": "a" * 64,
            "required_tools": [],
            "required_modalities": [],
            "required_schema_ids": [],
            "required_execution_constraints": [],
            "disqualifying_failure_categories": [],
        },
        "matched_assertion_ids": ["assertion-self-report"],
        "verified_assertion_ids": [],
        "disposition": "SATISFIED",
        "evidence_status": "SELF_REPORTED",
        "missing_dimensions": [],
        "failed_dimensions": [],
    }

    with pytest.raises(ValidationError, match="capability assessment"):
        CapabilityAssessment.model_validate_json(json.dumps(payload), strict=True)
