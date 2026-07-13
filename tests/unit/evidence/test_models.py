from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest
from pydantic import ValidationError

from super_scientist.domain.evidence.models import (
    ArtifactRef,
    EvidenceRecord,
    EvidenceSpan,
    VerificationState,
)


def test_artifact_ref_rejects_non_sha256_digest() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(
            sha256="not-a-sha256",
            size_bytes=0,
            media_type="application/octet-stream",
            relative_path="sha256/00/not-a-sha256",
        )


@pytest.mark.parametrize("media_type", ["", " ", "\t\n"])
def test_artifact_ref_rejects_blank_media_type(media_type: str) -> None:
    with pytest.raises(ValidationError, match="media_type"):
        ArtifactRef(
            sha256="a" * 64,
            size_bytes=0,
            media_type=media_type,
            relative_path=f"sha256/aa/{'a' * 64}",
        )


def test_artifact_ref_normalizes_media_type() -> None:
    artifact = ArtifactRef(
        sha256="a" * 64,
        size_bytes=0,
        media_type=" Text/Plain ",
        relative_path=f"sha256/aa/{'a' * 64}",
    )

    assert artifact.media_type == "text/plain"


def test_evidence_span_must_fit_extracted_text() -> None:
    with pytest.raises(ValueError, match="span offsets"):
        EvidenceSpan(start=0, end=10, text="short")


def test_evidence_span_end_must_follow_start() -> None:
    with pytest.raises(ValueError, match="span end"):
        EvidenceSpan(start=2, end=2, text="")


def test_evidence_record_hash_matches_artifact() -> None:
    artifact = ArtifactRef(
        sha256="a" * 64,
        size_bytes=3,
        media_type="text/plain",
        relative_path=f"sha256/aa/{'a' * 64}",
    )
    record = EvidenceRecord(
        evidence_id="ev-1",
        evidence_type="document",
        source_locator="fixture://one",
        retrieved_at=datetime.now(timezone(timedelta(0), name="zero-offset")),
        artifact=artifact,
        provenance={"collector": "test"},
        ingestion_actor_id="actor-1",
    )

    assert record.content_hash == artifact.sha256
    assert record.retrieved_at.tzinfo is UTC
    assert record.verification_state is VerificationState.UNVERIFIED


def test_evidence_identifiers_and_text_are_stripped() -> None:
    record = EvidenceRecord(
        evidence_id="  ev-1  ",
        evidence_type="  document  ",
        source_locator="  fixture://one  ",
        retrieved_at=datetime.now(UTC),
        artifact=ArtifactRef(
            sha256="a" * 64,
            size_bytes=3,
            media_type="text/plain",
            relative_path=f"sha256/aa/{'a' * 64}",
        ),
        provenance={"collector": "test"},
        ingestion_actor_id="  actor-1  ",
    )

    assert (
        record.evidence_id,
        record.evidence_type,
        record.source_locator,
        record.ingestion_actor_id,
    ) == ("ev-1", "document", "fixture://one", "actor-1")


@pytest.mark.parametrize(
    "field",
    ["evidence_id", "evidence_type", "source_locator", "ingestion_actor_id"],
)
def test_evidence_durable_text_rejects_blank(field: str) -> None:
    values: dict[str, object] = {
        "evidence_id": "ev-1",
        "evidence_type": "document",
        "source_locator": "fixture://one",
        "retrieved_at": datetime.now(UTC),
        "artifact": ArtifactRef(
            sha256="a" * 64,
            size_bytes=3,
            media_type="text/plain",
            relative_path=f"sha256/aa/{'a' * 64}",
        ),
        "provenance": {"collector": "test"},
        "ingestion_actor_id": "actor-1",
    }
    values[field] = "  "

    with pytest.raises(ValidationError):
        EvidenceRecord(**values)


def test_evidence_records_are_frozen() -> None:
    artifact = ArtifactRef(
        sha256="a" * 64,
        size_bytes=3,
        media_type="text/plain",
        relative_path=f"sha256/aa/{'a' * 64}",
    )
    record = EvidenceRecord(
        evidence_id="ev-1",
        evidence_type="document",
        source_locator="fixture://one",
        retrieved_at=datetime.now(UTC),
        artifact=artifact,
        provenance={"collector": "test"},
        ingestion_actor_id="actor-1",
    )

    with pytest.raises(ValidationError):
        record.evidence_id = "ev-2"


def test_evidence_record_collections_are_deeply_immutable() -> None:
    record = EvidenceRecord(
        evidence_id="ev-1",
        evidence_type="document",
        source_locator="fixture://one",
        retrieved_at=datetime.now(UTC),
        artifact=ArtifactRef(
            sha256="a" * 64,
            size_bytes=3,
            media_type="text/plain",
            relative_path=f"sha256/aa/{'a' * 64}",
        ),
        structured_observation={"values": ["first"]},
        provenance={"collector": "test"},
        ingestion_actor_id="actor-1",
    )

    with pytest.raises(TypeError):
        cast(dict[str, str], record.provenance)["collector"] = "other"
    with pytest.raises(TypeError):
        cast(dict[str, list[str]], record.structured_observation)["values"][0] = "other"


def test_evidence_record_freezes_tuple_containing_mutable_collections() -> None:
    record = EvidenceRecord(
        evidence_id="ev-1",
        evidence_type="document",
        source_locator="fixture://one",
        retrieved_at=datetime.now(UTC),
        artifact=ArtifactRef(
            sha256="a" * 64,
            size_bytes=3,
            media_type="text/plain",
            relative_path=f"sha256/aa/{'a' * 64}",
        ),
        structured_observation={"tuple": (["list item"], {"set item"})},
        provenance={"collector": "test"},
        ingestion_actor_id="actor-1",
    )

    frozen_tuple = cast(tuple[object, object], record.structured_observation["tuple"])

    assert isinstance(frozen_tuple[0], tuple)
    assert isinstance(frozen_tuple[1], frozenset)
    with pytest.raises(TypeError):
        cast(tuple[str, ...], frozen_tuple[0])[0] = "other"


@pytest.mark.parametrize("unsupported", [bytearray(b"mutable"), object()])
def test_evidence_record_rejects_unsupported_structured_observation_values(
    unsupported: object,
) -> None:
    with pytest.raises(ValidationError, match="unsupported structured observation value"):
        EvidenceRecord(
            evidence_id="ev-1",
            evidence_type="document",
            source_locator="fixture://one",
            retrieved_at=datetime.now(UTC),
            artifact=ArtifactRef(
                sha256="a" * 64,
                size_bytes=3,
                media_type="text/plain",
                relative_path=f"sha256/aa/{'a' * 64}",
            ),
            structured_observation={"unsupported": unsupported},
            provenance={"collector": "test"},
            ingestion_actor_id="actor-1",
        )
