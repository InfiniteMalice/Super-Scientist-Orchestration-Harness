from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, Engine, insert, text

from super_scientist.domain.primitives import sha256_hex
from super_scientist.providers.storage.database import create_database_engine, upgrade_database
from super_scientist.providers.storage.domain_records import AppendOnlyRecordRepository
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import research_runs


class StoredRun(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    run_id: str
    charter: str


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


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
        with engine.begin() as connection:
            connection.execute(
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
