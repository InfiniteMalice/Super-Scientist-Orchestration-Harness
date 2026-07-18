from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import Connection, Table, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes, sha256_hex
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import evaluator_heads, research_run_heads

TIMESTAMP_ADAPTER: TypeAdapter[UtcTimestamp] = TypeAdapter(UtcTimestamp)


def _require_integrity(condition: bool, detail: str) -> None:
    if not condition:
        raise StorageIntegrityError(f"storage integrity error: {detail}")


class AppendOnlyRecordRepository[RecordT: BaseModel]:
    """Stores one source-controlled Pydantic record type in one fixed authoritative table."""

    def __init__(
        self,
        connection: Connection,
        *,
        table: Table,
        model_type: type[RecordT],
        identifier_field: str,
        relationship_fields: Mapping[str, str] | None = None,
    ) -> None:
        _require_integrity(
            bool(model_type.model_config.get("frozen")),
            "append-only repository model must be frozen",
        )
        _require_integrity(identifier_field in model_type.model_fields, "unknown identifier field")
        self._connection = connection
        self._table = table
        self._model_type = model_type
        self._identifier_field = identifier_field
        self._relationship_fields = dict(relationship_fields or {})
        _require_integrity(
            set(self._relationship_fields).issubset(table.c.keys()),
            "unknown relationship column",
        )
        _require_integrity(
            set(self._relationship_fields.values()).issubset(model_type.model_fields),
            "unknown relationship field",
        )

    def get(self, record_id: str) -> RecordT | None:
        row = (
            self._connection.execute(
                select(self._table).where(self._table.c[self._identifier_field] == record_id)
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._decode_row(dict(row))

    def list_all(self) -> tuple[RecordT, ...]:
        rows = self._connection.execute(
            select(self._table).order_by(self._table.c[self._identifier_field])
        ).mappings()
        return tuple(self._decode_row(dict(row)) for row in rows)

    def add(self, record_id: str, record: RecordT, created_at: UtcTimestamp) -> None:
        _require_integrity(isinstance(record_id, str), "record identifier must be a string")
        try:
            validated = self._model_type.model_validate(record.model_dump(mode="json"))
            validated_created_at = TIMESTAMP_ADAPTER.validate_python(created_at)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid append-only record"
            ) from error
        _require_integrity(
            getattr(validated, self._identifier_field) == record_id,
            f"{self._identifier_field} does not match record",
        )
        record_json = canonical_json_bytes(validated.model_dump(mode="json")).decode("utf-8")
        values: dict[str, object] = {
            self._identifier_field: record_id,
            "record_json": record_json,
            "content_hash": sha256_hex(record_json.encode("utf-8")),
            "created_at": validated_created_at.isoformat(),
        }
        for column_name, field_name in self._relationship_fields.items():
            value = getattr(validated, field_name)
            _require_integrity(isinstance(value, str), f"{field_name} must be a string")
            values[column_name] = value
        self._connection.execute(insert(self._table).values(**values))

    def _decode_row(self, row: Mapping[str, object]) -> RecordT:
        try:
            record_json = _stored_string(row, "record_json")
            content_hash = _stored_string(row, "content_hash")
            _stored_string(row, "created_at")
            record = self._model_type.model_validate_json(record_json)
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError("storage integrity error: invalid record JSON") from error
        stored_identifier = _stored_string(row, self._identifier_field)
        _require_integrity(
            getattr(record, self._identifier_field) == stored_identifier,
            f"{self._identifier_field} does not match record_json",
        )
        _require_integrity(
            sha256_hex(record_json.encode("utf-8")) == content_hash,
            "content_hash does not match record_json",
        )
        for column_name, field_name in self._relationship_fields.items():
            _require_integrity(
                getattr(record, field_name) == _stored_string(row, column_name),
                f"{column_name} does not match record_json",
            )
        return record


class ResearchRunHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, run_id: str) -> str | None:
        return self._connection.execute(
            select(research_run_heads.c.run_event_id).where(research_run_heads.c.run_id == run_id)
        ).scalar_one_or_none()

    def set(self, run_id: str, run_event_id: str) -> None:
        statement = sqlite_insert(research_run_heads).values(
            run_id=run_id,
            run_event_id=run_event_id,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[research_run_heads.c.run_id],
                set_={"run_event_id": run_event_id},
            )
        )


class EvaluatorHeadRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self) -> str | None:
        return self._connection.execute(
            select(evaluator_heads.c.evaluator_version_id)
        ).scalar_one_or_none()

    def set(self, evaluator_version_id: str) -> None:
        statement = sqlite_insert(evaluator_heads).values(
            singleton_id=1,
            evaluator_version_id=evaluator_version_id,
        )
        self._connection.execute(
            statement.on_conflict_do_update(
                index_elements=[evaluator_heads.c.singleton_id],
                set_={"evaluator_version_id": evaluator_version_id},
            )
        )


def _stored_string(row: Mapping[str, object], column_name: str) -> str:
    value = row[column_name]
    if not isinstance(value, str):
        raise StorageIntegrityError(f"storage integrity error: {column_name} must be a string")
    return value
