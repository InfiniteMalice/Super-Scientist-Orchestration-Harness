from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, Engine, insert, text

from super_scientist.domain.primitives import sha256_hex
from super_scientist.providers.storage import domain_records
from super_scientist.providers.storage.append_only import AppendOnlyRecordRepository
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.domain_records import (
    ResearchRunHeadRepository,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import research_run_events, research_runs


class StoredRun(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    run_id: str
    charter: str


class RecordState(StrEnum):
    OBSERVED = "OBSERVED"


class StrictTypedRun(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    run_id: str
    observed_at: datetime
    state: RecordState


class StoredRunEvent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    run_event_id: str
    run_id: str


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def test_public_storage_surface_exposes_no_generic_record_constructor() -> None:
    assert "AppendOnlyRecordRepository" not in domain_records.__all__
    assert not hasattr(domain_records, "AppendOnlyRecordRepository")


def test_private_record_engine_round_trips_strict_datetime_and_enum_fields(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'typed.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    connection = engine.connect()
    repository = AppendOnlyRecordRepository(
        connection,
        table=research_runs,
        model_type=StrictTypedRun,
        identifier_field="run_id",
    )
    try:
        record = StrictTypedRun(run_id="typed-run", observed_at=NOW, state=RecordState.OBSERVED)
        repository.add(record.run_id, record, NOW)

        assert repository.get(record.run_id) == record
    finally:
        connection.close()
        engine.dispose()


def test_append_only_repository_round_trips_canonical_record(tmp_path: Path) -> None:
    repository, connection, engine = _repository(tmp_path)
    try:
        record = StoredRun(run_id="run-1", charter="test canonical storage")
        repository.add("run-1", record, NOW)

        assert repository.get("run-1") == record
        assert repository.list_all() == (record,)
        assert repository.get("missing") is None
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("record_json", "content_hash", "expected_detail"),
    [
        ("{not json", "a" * 64, "invalid record JSON"),
        ('{"run_id":"other-run","charter":"mismatch"}', "a" * 64, "run_id"),
        ('{"run_id":"run-1","charter":"hash mismatch"}', "a" * 64, "content_hash"),
    ],
    ids=["corrupt-json", "identifier-mismatch", "hash-mismatch"],
)
def test_append_only_repository_rejects_corrupt_stored_records(
    tmp_path: Path,
    record_json: str,
    content_hash: str,
    expected_detail: str,
) -> None:
    repository, connection, engine = _repository(tmp_path)
    try:
        with engine.begin() as write_connection:
            write_connection.execute(
                insert(research_runs).values(
                    run_id="run-1",
                    record_json=record_json,
                    content_hash=content_hash,
                    created_at=NOW.isoformat(),
                )
            )

        with pytest.raises(StorageIntegrityError, match=expected_detail):
            repository.get("run-1")
    finally:
        connection.close()
        engine.dispose()


def test_append_only_repository_recomputes_hash_before_persisting(tmp_path: Path) -> None:
    repository, connection, engine = _repository(tmp_path)
    try:
        record = StoredRun(run_id="run-1", charter="hash me")
        repository.add("run-1", record, NOW)
        stored_json, stored_hash = connection.execute(
            text("SELECT record_json, content_hash FROM research_runs WHERE run_id = 'run-1'")
        ).one()

        assert stored_hash == sha256_hex(stored_json.encode("utf-8"))
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.parametrize(
    "created_at",
    ["not-a-timestamp", "2026-07-18T12:00:00+01:00"],
    ids=["invalid", "non-utc"],
)
def test_append_only_repository_rejects_invalid_stored_created_at(
    tmp_path: Path,
    created_at: str,
) -> None:
    repository, connection, engine = _repository(tmp_path)
    try:
        record_json = '{"run_id":"run-1","charter":"timestamp validation"}'
        with engine.begin() as writer:
            writer.execute(
                insert(research_runs).values(
                    run_id="run-1",
                    record_json=record_json,
                    content_hash=sha256_hex(record_json.encode("utf-8")),
                    created_at=created_at,
                )
            )

        with pytest.raises(StorageIntegrityError, match="created_at"):
            repository.get("run-1")
    finally:
        connection.close()
        engine.dispose()


def test_private_record_engine_rejects_relationship_column_mismatch(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'relationships.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    connection = engine.connect()
    repository = AppendOnlyRecordRepository(
        connection,
        table=research_run_events,
        model_type=StoredRunEvent,
        identifier_field="run_event_id",
        relationship_fields={"run_id": "run_id"},
    )
    try:
        with engine.begin() as writer:
            writer.execute(
                insert(research_runs).values(
                    run_id="run-1",
                    record_json="{}",
                    content_hash="a" * 64,
                    created_at=NOW.isoformat(),
                )
            )
            record_json = '{"run_event_id":"event-1","run_id":"different-run"}'
            writer.execute(
                insert(research_run_events).values(
                    run_event_id="event-1",
                    run_id="run-1",
                    record_json=record_json,
                    content_hash=sha256_hex(record_json.encode("utf-8")),
                    created_at=NOW.isoformat(),
                )
            )

        with pytest.raises(StorageIntegrityError, match="run_id"):
            repository.get("event-1")
    finally:
        connection.close()
        engine.dispose()


def test_run_head_repository_rejects_an_event_from_another_run(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'heads.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    connection = engine.connect()
    try:
        with engine.begin() as writer:
            for run_id in ("run-1", "run-2"):
                writer.execute(
                    insert(research_runs).values(
                        run_id=run_id,
                        record_json="{}",
                        content_hash="a" * 64,
                        created_at=NOW.isoformat(),
                    )
                )
            writer.execute(
                insert(research_run_events).values(
                    run_event_id="event-2",
                    run_id="run-2",
                    record_json="{}",
                    content_hash="a" * 64,
                    created_at=NOW.isoformat(),
                )
            )

        with pytest.raises(StorageIntegrityError, match="does not belong"):
            ResearchRunHeadRepository(connection).set("run-1", "event-2")
    finally:
        connection.close()
        engine.dispose()


def _repository(
    tmp_path: Path,
) -> tuple[AppendOnlyRecordRepository[StoredRun], Connection, Engine]:
    database_url = f"sqlite:///{(tmp_path / 'adaptation.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)
    connection = engine.connect()
    return (
        AppendOnlyRecordRepository(
            connection,
            table=research_runs,
            model_type=StoredRun,
            identifier_field="run_id",
        ),
        connection,
        engine,
    )
