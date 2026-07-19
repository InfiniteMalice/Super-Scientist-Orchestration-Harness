from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from super_scientist.domain.evidence_trails.models import (
    ClaimModality,
    ReportSentenceBinding,
    ReportSourceSpan,
)
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex
from super_scientist.providers.storage.database import (
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    EvidenceTrailAssessmentRepository,
    EvidenceTrailCheckRepository,
    EvidenceTrailHeadRepository,
    EvidenceTrailNodeRepository,
    EvidenceTrailRelationRepository,
    EvidenceTrailVersionRepository,
    ReportSentenceBindingRepository,
)
from super_scientist.providers.storage.repositories import (
    ClaimRepository,
    EvidenceRepository,
    StorageIntegrityError,
)
from tests.unit.evidence_trails.conftest import make_trail_fixture


@pytest.mark.integration
def test_all_six_fixed_repositories_round_trip_exact_strict_records(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "trail-records.db")
    fixture = make_trail_fixture()
    snapshot = fixture.snapshot
    binding = _binding(fixture)
    try:
        with engine.begin() as connection:
            EvidenceRepository(connection).add(fixture.inputs.sources[0].evidence)
            ClaimRepository(connection).add_version(fixture.inputs.claim)
            versions = EvidenceTrailVersionRepository(connection)
            nodes = EvidenceTrailNodeRepository(connection)
            relations = EvidenceTrailRelationRepository(connection)
            checks = EvidenceTrailCheckRepository(connection)
            assessments = EvidenceTrailAssessmentRepository(connection)
            bindings = ReportSentenceBindingRepository(connection)

            versions.add(
                snapshot.version.trail_version_id,
                snapshot.version,
                snapshot.version.created_at,
            )
            for node in snapshot.nodes:
                nodes.add(node.node_id, node, snapshot.version.created_at)
            for relation in snapshot.relations:
                relations.add(relation.relation_id, relation, snapshot.version.created_at)
            for check in snapshot.checks:
                checks.add(check.check_id, check, check.checked_at)
            for assessment in snapshot.assessments:
                assessments.add(
                    assessment.assessment_id,
                    assessment,
                    assessment.provenance.assessed_at,
                )
            bindings.add(binding.binding_id, binding, binding.created_at)

            assert versions.list_all() == (snapshot.version,)
            assert nodes.list_all() == tuple(sorted(snapshot.nodes, key=lambda item: item.node_id))
            assert relations.list_all() == tuple(
                sorted(snapshot.relations, key=lambda item: item.relation_id)
            )
            assert checks.list_all() == tuple(
                sorted(snapshot.checks, key=lambda item: item.check_id)
            )
            assert assessments.list_all() == tuple(
                sorted(snapshot.assessments, key=lambda item: item.assessment_id)
            )
            assert bindings.list_all() == (binding,)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_trail_head_requires_version_one_then_exact_monotonic_successors(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, "trail-head-monotonic.db")
    fixture = make_trail_fixture()
    first = fixture.snapshot.version
    second = first.model_copy(
        update={
            "trail_version_id": "trail-version-2",
            "version": 2,
            "parent_trail_version_id": first.trail_version_id,
        }
    )
    try:
        with engine.begin() as connection:
            ClaimRepository(connection).add_version(fixture.inputs.claim)
            versions = EvidenceTrailVersionRepository(connection)
            versions.add(first.trail_version_id, first, first.created_at)
            versions.add(second.trail_version_id, second, second.created_at)
            head = EvidenceTrailHeadRepository(connection)

            with pytest.raises(StorageIntegrityError, match="begin at version 1"):
                head.set(first.trail_id, second.trail_version_id, second.version)
            head.set(first.trail_id, first.trail_version_id, first.version)
            head.set(second.trail_id, second.trail_version_id, second.version)
            with pytest.raises(StorageIntegrityError, match="exact successor"):
                head.set(first.trail_id, first.trail_version_id, first.version)
            assert head.get(first.trail_id) == (second.trail_version_id, second.version)
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("damage", ("hash", "unknown", "noncanonical", "relationship"))
def test_node_repository_rejects_hash_json_and_relationship_corruption(
    tmp_path: Path,
    damage: str,
) -> None:
    engine = _engine(tmp_path, f"trail-node-{damage}.db")
    fixture = make_trail_fixture()
    snapshot = fixture.snapshot
    node = snapshot.nodes[0]
    try:
        with engine.begin() as connection:
            evidence = fixture.inputs.sources[0].evidence
            EvidenceRepository(connection).add(evidence)
            EvidenceRepository(connection).add(
                evidence.model_copy(update={"evidence_id": "evidence-2"})
            )
            ClaimRepository(connection).add_version(fixture.inputs.claim)
            EvidenceTrailVersionRepository(connection).add(
                snapshot.version.trail_version_id,
                snapshot.version,
                snapshot.version.created_at,
            )
            repository = EvidenceTrailNodeRepository(connection)
            repository.add(node.node_id, node, snapshot.version.created_at)
            connection.execute(text("DROP TRIGGER evidence_trail_nodes_no_update"))
            row = connection.execute(
                text(
                    "SELECT record_json FROM evidence_trail_nodes WHERE node_id = :node_id"
                ),
                {"node_id": node.node_id},
            ).scalar_one()
            if damage == "hash":
                connection.execute(
                    text(
                        "UPDATE evidence_trail_nodes SET content_hash = :content_hash "
                        "WHERE node_id = :node_id"
                    ),
                    {"content_hash": "b" * 64, "node_id": node.node_id},
                )
            elif damage == "relationship":
                connection.execute(
                    text(
                        "UPDATE evidence_trail_nodes SET evidence_id = 'evidence-2' "
                        "WHERE node_id = :node_id"
                    ),
                    {"node_id": node.node_id},
                )
            else:
                decoded = json.loads(row)
                if damage == "unknown":
                    decoded["unknown"] = True
                    changed = canonical_json_bytes(decoded).decode("utf-8")
                else:
                    changed = f"{row} "
                connection.execute(
                    text(
                        "UPDATE evidence_trail_nodes SET record_json = :record_json, "
                        "content_hash = :content_hash WHERE node_id = :node_id"
                    ),
                    {
                        "record_json": changed,
                        "content_hash": sha256_hex(changed.encode("utf-8")),
                        "node_id": node.node_id,
                    },
                )

            with pytest.raises(StorageIntegrityError, match="storage integrity error"):
                repository.get(node.node_id)
    finally:
        engine.dispose()


def _engine(tmp_path: Path, name: str):  # type: ignore[no-untyped-def]
    database_url = f"sqlite:///{(tmp_path / name).as_posix()}"
    upgrade_database(database_url)
    return create_database_engine(database_url)


def _binding(fixture):  # type: ignore[no-untyped-def]
    version = fixture.snapshot.version
    node = fixture.snapshot.nodes[0]
    return ReportSentenceBinding(
        binding_id="binding-1",
        trail_version_id=version.trail_version_id,
        claim_version_id=version.claim_version_id,
        sentence="The exact source supports the sentence.",
        outcome=version.status,
        source_node_ids=(node.node_id,),
        source_spans=(
            ReportSourceSpan(
                node_id=node.node_id,
                source_id=node.source_id,
                evidence_id=node.evidence_id,
                start=node.exact_span.start,
                end=node.exact_span.end,
                text=node.exact_span.text,
                content_hash=node.content_hash,
            ),
        ),
        contradiction_node_ids=(),
        opposing_node_ids=(),
        uncertainty="The sentence is bounded to the exact retained span.",
        modality=ClaimModality.ASSERTED,
        created_at=version.created_at,
        governing_policy_hash=version.governing_policy_hash,
    )
