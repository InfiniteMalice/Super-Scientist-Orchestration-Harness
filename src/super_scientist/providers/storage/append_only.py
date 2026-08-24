from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import Connection, Table, insert, select

from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes, sha256_hex
from super_scientist.providers.storage.query_bounds import sqlite_in_chunks
from super_scientist.providers.storage.repositories import StorageIntegrityError
from super_scientist.providers.storage.schema import hypothesis_versions

__all__ = [
    "AppendOnlyRecordRepository",
    "OrderedReferenceBinding",
    "ReferencedAppendOnlyRecordRepository",
    "StrictFrozenStorageRecord",
]

TIMESTAMP_ADAPTER: TypeAdapter[UtcTimestamp] = TypeAdapter(UtcTimestamp)

type _RelationshipStorageType = type[str] | type[int]


class StrictFrozenStorageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


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
        relationship_types: Mapping[str, _RelationshipStorageType] | None = None,
        nullable_relationship_fields: Collection[str] | None = None,
        hypothesis_scope_field: str | None = None,
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
        self._nullable_relationship_fields = frozenset(nullable_relationship_fields or ())
        self._hypothesis_scope_field = hypothesis_scope_field
        requested_relationship_types = dict(relationship_types or {})
        _require_integrity(
            set(self._relationship_fields).issubset(table.c.keys()),
            "unknown relationship column",
        )
        _require_integrity(
            set(self._relationship_fields.values()).issubset(model_type.model_fields),
            "unknown relationship field",
        )
        _require_integrity(
            set(requested_relationship_types).issubset(self._relationship_fields),
            "unknown typed relationship column",
        )
        _require_integrity(
            self._nullable_relationship_fields.issubset(self._relationship_fields),
            "unknown nullable relationship column",
        )
        _require_integrity(
            all(
                self._table.c[column_name].nullable
                for column_name in self._nullable_relationship_fields
            ),
            "nullable relationship column must permit null",
        )
        _require_integrity(
            all(
                storage_type is str or storage_type is int
                for storage_type in requested_relationship_types.values()
            ),
            "unsupported relationship storage type",
        )
        if hypothesis_scope_field is not None:
            _require_integrity(
                hypothesis_scope_field in model_type.model_fields,
                "unknown hypothesis scope field",
            )
            _require_integrity("hypothesis_id" in table.c, "missing hypothesis scope column")
        self._relationship_types: dict[str, _RelationshipStorageType] = {
            column_name: requested_relationship_types.get(column_name, str)
            for column_name in self._relationship_fields
        }

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

    def _list_by_relationship(
        self,
        column_name: str,
        value: str | int,
    ) -> tuple[RecordT, ...]:
        _require_integrity(column_name in self._relationship_fields, "unknown relationship column")
        rows = self._connection.execute(
            select(self._table)
            .where(self._table.c[column_name] == value)
            .order_by(self._table.c[self._identifier_field])
        ).mappings()
        return tuple(self._decode_row(dict(row)) for row in rows)

    def _get_many(self, record_ids: tuple[str, ...]) -> tuple[RecordT, ...]:
        rows: list[dict[str, object]] = []
        for identifiers in sqlite_in_chunks(record_ids):
            rows.extend(
                dict(row)
                for row in self._connection.execute(
                    select(self._table)
                    .where(self._table.c[self._identifier_field].in_(identifiers))
                    .order_by(self._table.c[self._identifier_field])
                ).mappings()
            )
        return tuple(self._decode_row(row) for row in rows)

    def add(self, record_id: str, record: RecordT, created_at: UtcTimestamp) -> None:
        _require_integrity(isinstance(record_id, str), "record identifier must be a string")
        try:
            validated = self._model_type.model_validate(record.model_dump(mode="python"))
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
            values[column_name] = _validated_relationship_value(
                getattr(validated, field_name),
                field_name,
                self._relationship_types[column_name],
                nullable=column_name in self._nullable_relationship_fields,
            )
        derived_values = self._derive_storage_values(validated)
        _require_integrity(
            set(derived_values).issubset(self._table.c.keys()),
            "unknown derived relationship column",
        )
        _require_integrity(
            not (set(derived_values) & set(values)),
            "derived relationship column overlaps canonical storage",
        )
        values.update(derived_values)
        self._connection.execute(insert(self._table).values(**values))

    def _decode_row(self, row: Mapping[str, object]) -> RecordT:
        try:
            record_json = _stored_string(row, "record_json")
            content_hash = _stored_string(row, "content_hash")
            record = self._model_type.model_validate_json(record_json)
            canonical_record_json = canonical_json_bytes(record.model_dump(mode="json")).decode(
                "utf-8"
            )
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError("storage integrity error: invalid record JSON") from error
        created_at = _stored_string(row, "created_at")
        try:
            TIMESTAMP_ADAPTER.validate_python(datetime.fromisoformat(created_at))
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid created_at timestamp"
            ) from error
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
            storage_type = self._relationship_types[column_name]
            record_value = _validated_relationship_value(
                getattr(record, field_name),
                field_name,
                storage_type,
                nullable=column_name in self._nullable_relationship_fields,
            )
            _require_integrity(
                record_value
                == _stored_relationship_value(
                    row,
                    column_name,
                    storage_type,
                    nullable=column_name in self._nullable_relationship_fields,
                ),
                f"{column_name} does not match record_json",
            )
        self._verify_derived_storage_values(row, record)
        _require_integrity(record_json == canonical_record_json, "record_json must be canonical")
        return record

    def _derive_storage_values(self, record: RecordT) -> dict[str, object]:
        if self._hypothesis_scope_field is None:
            return {}
        hypothesis_version_id = _validated_relationship_value(
            getattr(record, self._hypothesis_scope_field),
            self._hypothesis_scope_field,
            str,
        )
        hypothesis_id = self._connection.execute(
            select(hypothesis_versions.c.hypothesis_id).where(
                hypothesis_versions.c.hypothesis_version_id == hypothesis_version_id
            )
        ).scalar_one_or_none()
        _require_integrity(
            isinstance(hypothesis_id, str),
            "hypothesis scope version does not exist",
        )
        return {"hypothesis_id": hypothesis_id}

    def _verify_derived_storage_values(
        self,
        row: Mapping[str, object],
        record: RecordT,
    ) -> None:
        if self._hypothesis_scope_field is None:
            return
        expected = self._derive_storage_values(record)
        _require_integrity(
            _stored_string(row, "hypothesis_id") == expected["hypothesis_id"],
            "hypothesis_id does not match the exact hypothesis version scope",
        )


@dataclass(frozen=True, slots=True)
class OrderedReferenceBinding:
    table: Table
    owner_column: str
    record_field: str
    reference_column: str
    scope_columns: tuple[str, ...] = ()


class ReferencedAppendOnlyRecordRepository[RecordT: BaseModel](AppendOnlyRecordRepository[RecordT]):
    """Materializes ordered relationship tuples and verifies exact canonical equality."""

    def __init__(
        self,
        connection: Connection,
        *,
        table: Table,
        model_type: type[RecordT],
        identifier_field: str,
        reference_bindings: tuple[OrderedReferenceBinding, ...],
        relationship_fields: Mapping[str, str] | None = None,
        relationship_types: Mapping[str, _RelationshipStorageType] | None = None,
        nullable_relationship_fields: Collection[str] | None = None,
        hypothesis_scope_field: str | None = None,
        pre_parent_reference_fields: Collection[str] | None = None,
    ) -> None:
        super().__init__(
            connection,
            table=table,
            model_type=model_type,
            identifier_field=identifier_field,
            relationship_fields=relationship_fields,
            relationship_types=relationship_types,
            nullable_relationship_fields=nullable_relationship_fields,
            hypothesis_scope_field=hypothesis_scope_field,
        )
        _require_integrity(bool(reference_bindings), "referenced repository needs bindings")
        for binding in reference_bindings:
            _require_integrity(
                binding.owner_column in binding.table.c,
                "unknown reference owner column",
            )
            _require_integrity("position" in binding.table.c, "missing reference position")
            _require_integrity(
                binding.reference_column in binding.table.c,
                "unknown reference column",
            )
            _require_integrity(
                binding.record_field in model_type.model_fields,
                "unknown canonical reference field",
            )
            _require_integrity(
                set(binding.scope_columns).issubset(binding.table.c.keys()),
                "unknown normalized reference scope column",
            )
            _require_integrity(
                set(binding.scope_columns).issubset(table.c.keys()),
                "normalized reference scope is absent from owner",
            )
        self._reference_bindings = reference_bindings
        self._pre_parent_reference_fields = frozenset(pre_parent_reference_fields or ())
        binding_fields = {binding.record_field for binding in reference_bindings}
        _require_integrity(
            self._pre_parent_reference_fields.issubset(binding_fields),
            "unknown pre-parent reference field",
        )
        for binding in reference_bindings:
            if binding.record_field in self._pre_parent_reference_fields:
                _require_integrity(
                    set(binding.scope_columns).issubset(model_type.model_fields),
                    "pre-parent reference scope must be canonical",
                )

    def add(self, record_id: str, record: RecordT, created_at: UtcTimestamp) -> None:
        try:
            validated = self._model_type.model_validate(record.model_dump(mode="python"))
        except (TypeError, ValueError) as error:
            raise StorageIntegrityError(
                "storage integrity error: invalid append-only record"
            ) from error
        pre_parent_bindings = tuple(
            binding
            for binding in self._reference_bindings
            if binding.record_field in self._pre_parent_reference_fields
        )
        self._insert_reference_bindings(record_id, validated, pre_parent_bindings, None)
        super().add(record_id, validated, created_at)
        owner_row = (
            self._connection.execute(
                select(self._table).where(self._table.c[self._identifier_field] == record_id)
            )
            .mappings()
            .one()
        )
        post_parent_bindings = tuple(
            binding
            for binding in self._reference_bindings
            if binding.record_field not in self._pre_parent_reference_fields
        )
        self._insert_reference_bindings(
            record_id,
            validated,
            post_parent_bindings,
            dict(owner_row),
        )

    def _insert_reference_bindings(
        self,
        record_id: str,
        record: RecordT,
        bindings: tuple[OrderedReferenceBinding, ...],
        owner_row: Mapping[str, object] | None,
    ) -> None:
        for binding in bindings:
            references = _canonical_reference_tuple(record, binding.record_field)
            for position, reference_id in enumerate(references):
                if owner_row is None:
                    scope_values = {
                        column_name: _validated_relationship_value(
                            getattr(record, column_name),
                            column_name,
                            str,
                        )
                        for column_name in binding.scope_columns
                    }
                else:
                    scope_values = {
                        column_name: _stored_string(owner_row, column_name)
                        for column_name in binding.scope_columns
                    }
                self._connection.execute(
                    insert(binding.table).values(
                        **{
                            binding.owner_column: record_id,
                            "position": position,
                            binding.reference_column: reference_id,
                            **scope_values,
                        }
                    )
                )

    def _decode_row(self, row: Mapping[str, object]) -> RecordT:
        record = super()._decode_row(row)
        owner_id = _validated_relationship_value(
            getattr(record, self._identifier_field),
            self._identifier_field,
            str,
        )
        for binding in self._reference_bindings:
            expected = _canonical_reference_tuple(record, binding.record_field)
            expected_scope = (
                tuple(_stored_string(row, column_name) for column_name in binding.scope_columns)
                if expected
                else ()
            )
            stored_rows = self._connection.execute(
                select(
                    binding.table.c.position,
                    binding.table.c[binding.reference_column],
                    *(binding.table.c[column_name] for column_name in binding.scope_columns),
                )
                .where(binding.table.c[binding.owner_column] == owner_id)
                .order_by(binding.table.c.position)
            ).mappings()
            actual = tuple(
                (
                    _stored_integer(dict(stored_row), "position"),
                    _stored_string(dict(stored_row), binding.reference_column),
                    *(
                        _stored_string(dict(stored_row), column_name)
                        for column_name in binding.scope_columns
                    ),
                )
                for stored_row in stored_rows
            )
            _require_integrity(
                actual
                == tuple(
                    (position, reference_id, *expected_scope)
                    for position, reference_id in enumerate(expected)
                ),
                f"{binding.record_field} materialization does not match exact canonical references",
            )
        return record


def _canonical_reference_tuple(record: BaseModel, field_name: str) -> tuple[str, ...]:
    value = getattr(record, field_name)
    _require_integrity(isinstance(value, tuple), f"{field_name} must be a tuple")
    references: list[str] = []
    for item in value:
        _require_integrity(isinstance(item, str), f"{field_name} must contain strings")
        references.append(item)
    _require_integrity(
        len(set(references)) == len(references),
        f"{field_name} must contain unique identifiers",
    )
    return tuple(references)


def _stored_string(row: Mapping[str, object], column_name: str) -> str:
    value = row[column_name]
    if not isinstance(value, str):
        raise StorageIntegrityError(f"storage integrity error: {column_name} must be a string")
    return value


def _stored_integer(row: Mapping[str, object], column_name: str) -> int:
    value = row[column_name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise StorageIntegrityError(f"storage integrity error: {column_name} must be an integer")
    return value


def _validated_relationship_value(
    value: object,
    field_name: str,
    storage_type: _RelationshipStorageType,
    *,
    nullable: bool = False,
) -> str | int | None:
    if value is None:
        _require_integrity(nullable, f"{field_name} must not be null")
        return None
    if storage_type is str:
        if not isinstance(value, str):
            raise StorageIntegrityError(f"storage integrity error: {field_name} must be a string")
        return value
    if not isinstance(value, int) or isinstance(value, bool):
        raise StorageIntegrityError(f"storage integrity error: {field_name} must be an integer")
    return value


def _stored_relationship_value(
    row: Mapping[str, object],
    column_name: str,
    storage_type: _RelationshipStorageType,
    *,
    nullable: bool = False,
) -> str | int | None:
    if row[column_name] is None:
        _require_integrity(nullable, f"{column_name} must not be null")
        return None
    if storage_type is str:
        return _stored_string(row, column_name)
    return _stored_integer(row, column_name)
