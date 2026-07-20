from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import signature
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import IntegrityError

from super_scientist.providers.storage import domain_records, schema
from super_scientist.providers.storage.database import (
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    AdmissionDecisionOutcome,
    BuiltinSimulatorId,
    CounterexampleRecord,
    CounterexampleRecordRepository,
    EvaluationOutcome,
    ExecutableModelSpecRecord,
    ExecutableModelSpecRepository,
    HypothesisAdmissionDecisionRecord,
    HypothesisAdmissionDecisionRepository,
    HypothesisAdmissionStatus,
    HypothesisHeadRepository,
    HypothesisRevisionRecord,
    HypothesisRevisionRepository,
    HypothesisVersionRecord,
    HypothesisVersionRepository,
    ModelExecutionMode,
    ModelType,
    PrimitiveEvaluationFrame,
    PrimitiveEvaluationRecord,
    PrimitiveEvaluationRepository,
    PrimitiveHeadRepository,
    PrimitiveStatus,
    PrimitiveVersionRecord,
    PrimitiveVersionRepository,
    SimulationResultRecord,
    SimulationResultRepository,
    VerificationMechanismCategory,
    VerificationMechanismSpecRecord,
    VerificationMechanismSpecRepository,
    VerificationOutcome,
    VerificationResultCategory,
    VerificationResultRecord,
    VerificationResultRepository,
)
from super_scientist.providers.storage.repositories import StorageIntegrityError

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
POLICY_HASH = "a" * 64
ARTIFACT_HASH = "b" * 64

AUTHORITATIVE_0005_TABLES = {
    "primitive_versions",
    "primitive_evaluations",
    "hypothesis_versions",
    "executable_model_specs",
    "verification_mechanism_specs",
    "verification_results",
    "simulation_results",
    "counterexample_records",
    "hypothesis_revisions",
    "hypothesis_admission_decisions",
    "primitive_version_predecessors",
    "primitive_version_dependencies",
    "primitive_version_measurements",
    "primitive_evaluation_verification_results",
    "primitive_evaluation_evidence",
    "hypothesis_version_primitives",
    "hypothesis_version_evidence",
    "verification_result_simulations",
    "counterexample_simulations",
    "counterexample_verification_results",
    "counterexample_evidence",
    "hypothesis_revision_verification_results",
    "hypothesis_revision_counterexamples",
    "hypothesis_admission_models",
    "hypothesis_admission_verification_results",
    "hypothesis_admission_counterexamples",
    "hypothesis_admission_revisions",
}

REPOSITORIES = (
    PrimitiveVersionRepository,
    PrimitiveEvaluationRepository,
    HypothesisVersionRepository,
    ExecutableModelSpecRepository,
    VerificationMechanismSpecRepository,
    VerificationResultRepository,
    SimulationResultRepository,
    CounterexampleRecordRepository,
    HypothesisRevisionRepository,
    HypothesisAdmissionDecisionRepository,
)


def test_public_0005_repositories_are_fixed_to_connection_only() -> None:
    for repository_type in (*REPOSITORIES, PrimitiveHeadRepository, HypothesisHeadRepository):
        assert tuple(signature(repository_type).parameters) == ("connection",)
        assert repository_type.__name__ in domain_records.__all__
    assert "_AppendOnlyRecordRepository" not in domain_records.__all__
    assert "_ReferencedAppendOnlyRecordRepository" not in domain_records.__all__


def test_0005_storage_records_are_strict_frozen_and_reject_unknown_fields() -> None:
    records = _records()
    for record in records.all_records:
        assert record.model_config["extra"] == "forbid"
        assert record.model_config["frozen"] is True
        assert record.model_config["strict"] is True
        with pytest.raises(ValidationError, match="extra_forbidden"):
            type(record).model_validate(record.model_dump(mode="python") | {"unknown": True})


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "source_text",
        "import_path",
        "entry_point",
        "argv",
        "command",
        "shell_command",
        "url",
        "network_url",
        "executable",
    ],
)
def test_model_spec_rejects_execution_authority(forbidden_field: str) -> None:
    payload = _records().model.model_dump(mode="python")
    payload[forbidden_field] = "untrusted"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExecutableModelSpecRecord.model_validate(payload)


def test_model_spec_requires_content_metadata_or_closed_builtin_identifier() -> None:
    model = _records().model
    with pytest.raises(ValidationError, match="artifact"):
        ExecutableModelSpecRecord.model_validate(
            model.model_dump(mode="python")
            | {
                "artifact_hash": None,
                "artifact_media_type": None,
                "artifact_size_bytes": None,
            }
        )
    with pytest.raises(ValidationError, match="builtin_simulator_id"):
        ExecutableModelSpecRecord.model_validate(
            model.model_dump(mode="python")
            | {
                "execution_mode": ModelExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
                "artifact_hash": None,
                "artifact_media_type": None,
                "artifact_size_bytes": None,
                "builtin_simulator_id": "unknown-simulator",
            }
        )

    builtin = ExecutableModelSpecRecord.model_validate(
        model.model_dump(mode="python")
        | {
            "model_spec_id": "model-builtin",
            "execution_mode": ModelExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
            "artifact_hash": None,
            "artifact_media_type": None,
            "artifact_size_bytes": None,
            "builtin_simulator_id": BuiltinSimulatorId.THERMAL_CHAMBER_V1,
        }
    )
    assert builtin.builtin_simulator_id is BuiltinSimulatorId.THERMAL_CHAMBER_V1


def test_verification_result_category_must_match_mechanism_category() -> None:
    result = _records().verification
    with pytest.raises(ValidationError, match="result_category"):
        VerificationResultRecord.model_validate(
            result.model_dump(mode="python")
            | {"result_category": VerificationResultCategory.LEARNED_JUDGE_RESULT}
        )


def test_version_and_reference_contracts_reject_gaps_duplicates_and_invalid_lineage() -> None:
    records = _records()
    with pytest.raises(ValidationError, match="version"):
        HypothesisVersionRecord.model_validate(
            records.hypothesis_v1.model_dump(mode="python") | {"version": True}
        )
    with pytest.raises(ValidationError, match="primitive_version_ids"):
        HypothesisVersionRecord.model_validate(
            records.hypothesis_v1.model_dump(mode="python")
            | {"primitive_version_ids": ("primitive-1-v2", "primitive-1-v2")}
        )
    with pytest.raises(ValidationError, match="resulting_version"):
        HypothesisRevisionRecord.model_validate(
            records.revision.model_dump(mode="python") | {"resulting_version": 3}
        )


@pytest.mark.integration
def test_repositories_round_trip_all_history_and_exact_reference_order(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "round-trip.db")
    records = _records()
    try:
        _add_records(connection, records)

        assert PrimitiveVersionRepository(connection).list_all() == (
            records.primitive_v1,
            records.primitive_v2,
        )
        assert (
            PrimitiveEvaluationRepository(connection).get(
                records.evaluation.primitive_evaluation_id
            )
            == records.evaluation
        )
        assert HypothesisVersionRepository(connection).list_all() == (
            records.hypothesis_v1,
            records.hypothesis_v2,
        )
        assert (
            ExecutableModelSpecRepository(connection).get(records.model.model_spec_id)
            == records.model
        )
        assert (
            VerificationMechanismSpecRepository(connection).get(records.mechanism.mechanism_spec_id)
            == records.mechanism
        )
        assert (
            SimulationResultRepository(connection).get(records.simulation.simulation_result_id)
            == records.simulation
        )
        assert (
            VerificationResultRepository(connection).get(
                records.verification.verification_result_id
            )
            == records.verification
        )
        assert (
            CounterexampleRecordRepository(connection).get(records.counterexample.counterexample_id)
            == records.counterexample
        )
        assert (
            HypothesisRevisionRepository(connection).get(records.revision.revision_id)
            == records.revision
        )
        assert (
            HypothesisAdmissionDecisionRepository(connection).get(
                records.admission.admission_decision_id
            )
            == records.admission
        )

        assert (
            _ordered_references(
                connection,
                schema.primitive_version_predecessors,
                "primitive_version_id",
                records.primitive_v2.primitive_version_id,
                "predecessor_primitive_version_id",
            )
            == records.primitive_v2.predecessor_primitive_version_ids
        )
        assert (
            _ordered_references(
                connection,
                schema.hypothesis_admission_verification_results,
                "admission_decision_id",
                records.admission.admission_decision_id,
                "verification_result_id",
            )
            == records.admission.verification_result_ids
        )
        assert (
            _ordered_references(
                connection,
                schema.hypothesis_admission_revisions,
                "admission_decision_id",
                records.admission.admission_decision_id,
                "revision_id",
            )
            == records.admission.revision_ids
        )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_every_0005_history_and_reference_table_is_append_only(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "append-only.db")
    try:
        _add_records(connection, _records())
        for table_name in sorted(AUTHORITATIVE_0005_TABLES):
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(f"UPDATE {table_name} SET rowid = rowid"))
            with pytest.raises(IntegrityError, match="append-only table"):
                connection.execute(text(f"DELETE FROM {table_name}"))
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_repositories_reject_missing_normalized_references(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "missing-reference.db")
    records = _records()
    try:
        _seed_external_references(connection)
        PrimitiveVersionRepository(connection).add(
            records.primitive_v1.primitive_version_id,
            records.primitive_v1,
            records.primitive_v1.created_at,
        )
        missing = records.primitive_v2.model_copy(
            update={"dependency_primitive_version_ids": ("missing-primitive-version",)}
        )
        with pytest.raises(IntegrityError):
            PrimitiveVersionRepository(connection).add(
                missing.primitive_version_id,
                missing,
                missing.created_at,
            )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("trigger", "tamper_sql", "repository_type", "record_id"),
    [
        (
            "hypothesis_admission_verification_results_no_update",
            "UPDATE hypothesis_admission_verification_results SET position = 2 "
            "WHERE admission_decision_id = 'admission-1' AND position = 0",
            HypothesisAdmissionDecisionRepository,
            "admission-1",
        ),
        (
            "primitive_version_predecessors_no_update",
            "UPDATE primitive_version_predecessors SET predecessor_primitive_version_id = "
            "'primitive-1-v2' WHERE primitive_version_id = 'primitive-1-v2' AND position = 0",
            PrimitiveVersionRepository,
            "primitive-1-v2",
        ),
    ],
    ids=("position-gap", "reference-drift"),
)
def test_decoder_rejects_normalized_reference_drift(
    tmp_path: Path,
    trigger: str,
    tamper_sql: str,
    repository_type: type[object],
    record_id: str,
) -> None:
    database_path = tmp_path / f"{record_id}-drift.db"
    engine, connection = _connection_for_path(database_path)
    try:
        _add_records(connection, _records())
        connection.commit()
    finally:
        connection.close()
        engine.dispose()

    with sqlite3.connect(database_path) as raw_connection:
        raw_connection.execute(f"DROP TRIGGER {trigger}")
        raw_connection.execute(tamper_sql)

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    connection = engine.connect()
    try:
        repository = repository_type(connection)  # type: ignore[call-arg]
        with pytest.raises(StorageIntegrityError, match="exact canonical references"):
            repository.get(record_id)  # type: ignore[attr-defined]
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_decoder_rejects_discriminator_and_canonical_json_tampering(tmp_path: Path) -> None:
    database_path = tmp_path / "discriminator-drift.db"
    engine, connection = _connection_for_path(database_path)
    records = _records()
    try:
        _add_records(connection, records)
        connection.commit()
    finally:
        connection.close()
        engine.dispose()

    with sqlite3.connect(database_path) as raw_connection:
        raw_connection.execute("DROP TRIGGER verification_results_no_update")
        raw_connection.execute(
            "UPDATE verification_results SET mechanism_category = 'LEARNED_JUDGE', "
            "result_category = 'LEARNED_JUDGE_RESULT' "
            "WHERE verification_result_id = 'verification-1'"
        )

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    connection = engine.connect()
    try:
        with pytest.raises(StorageIntegrityError, match="does not match record_json"):
            VerificationResultRepository(connection).get("verification-1")
    finally:
        connection.close()
        engine.dispose()

    database_path = tmp_path / "unknown-json.db"
    engine, connection = _connection_for_path(database_path)
    try:
        _add_records(connection, records)
        connection.commit()
    finally:
        connection.close()
        engine.dispose()
    with sqlite3.connect(database_path) as raw_connection:
        raw_connection.execute("DROP TRIGGER executable_model_specs_no_update")
        original = records.model.model_dump_json()
        raw_connection.execute(
            "UPDATE executable_model_specs SET record_json = ? WHERE model_spec_id = 'model-1'",
            (original[:-1] + ',"entry_point":"untrusted"}',),
        )
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    connection = engine.connect()
    try:
        with pytest.raises(StorageIntegrityError, match="invalid record JSON"):
            ExecutableModelSpecRepository(connection).get("model-1")
    finally:
        connection.close()
        engine.dispose()


@pytest.mark.integration
def test_mutable_heads_require_exact_stored_version_and_status(tmp_path: Path) -> None:
    engine, connection = _connection(tmp_path, "heads.db")
    records = _records()
    primitive_heads = PrimitiveHeadRepository(connection)
    hypothesis_heads = HypothesisHeadRepository(connection)
    try:
        _add_records(connection, records)
        assert primitive_heads.get("primitive-1") is None
        primitive_heads.set(
            "primitive-1",
            "primitive-1-v1",
            "1.0.0",
            PrimitiveStatus.PROPOSED,
        )
        primitive_heads.set(
            "primitive-1",
            "primitive-1-v2",
            "1.1.0",
            PrimitiveStatus.EXPERIMENTAL,
        )
        assert primitive_heads.get("primitive-1") == (
            "primitive-1-v2",
            "1.1.0",
            PrimitiveStatus.EXPERIMENTAL,
        )
        with pytest.raises(StorageIntegrityError, match="does not match"):
            primitive_heads.set(
                "primitive-1",
                "primitive-1-v2",
                "1.0.0",
                PrimitiveStatus.EXPERIMENTAL,
            )

        hypothesis_heads.set(
            "hypothesis-1",
            "hypothesis-1-v2",
            2,
            HypothesisAdmissionStatus.TRANSFER_VALIDATED,
        )
        assert hypothesis_heads.get("hypothesis-1") == (
            "hypothesis-1-v2",
            2,
            HypothesisAdmissionStatus.TRANSFER_VALIDATED,
        )
        with pytest.raises(StorageIntegrityError, match="invalid hypothesis head"):
            hypothesis_heads.set(
                "hypothesis-1",
                "hypothesis-1-v2",
                True,
                HypothesisAdmissionStatus.TRANSFER_VALIDATED,
            )
    finally:
        connection.rollback()
        connection.close()
        engine.dispose()


@dataclass(frozen=True, slots=True)
class _Records:
    primitive_v1: PrimitiveVersionRecord
    primitive_v2: PrimitiveVersionRecord
    hypothesis_v1: HypothesisVersionRecord
    hypothesis_v2: HypothesisVersionRecord
    model: ExecutableModelSpecRecord
    mechanism: VerificationMechanismSpecRecord
    simulation: SimulationResultRecord
    verification: VerificationResultRecord
    evaluation: PrimitiveEvaluationRecord
    counterexample: CounterexampleRecord
    revision: HypothesisRevisionRecord
    admission: HypothesisAdmissionDecisionRecord

    @property
    def all_records(self) -> tuple[object, ...]:
        return (
            self.primitive_v1,
            self.primitive_v2,
            self.hypothesis_v1,
            self.hypothesis_v2,
            self.model,
            self.mechanism,
            self.simulation,
            self.verification,
            self.evaluation,
            self.counterexample,
            self.revision,
            self.admission,
        )


def _records() -> _Records:
    primitive_v1 = PrimitiveVersionRecord(
        primitive_version_id="primitive-1-v1",
        primitive_id="primitive-1",
        semantic_version="1.0.0",
        definition="A retained experimental vocabulary unit.",
        motivation="Test whether the representation adds explanatory utility.",
        parent_vocabulary=("existing-vocabulary",),
        contrasts=("baseline-description",),
        examples=("example-one",),
        counterexamples=("counterexample-one",),
        construction_method="Source-controlled synthesis from retained evidence.",
        expected_uses=("local hypothesis construction",),
        predecessor_primitive_version_ids=(),
        dependency_primitive_version_ids=(),
        measurement_ids=(),
        falsification_tests=("fails to produce a distinct prediction",),
        ambiguity=("boundary remains experimental",),
        proposer_id="proposer-1",
        status=PrimitiveStatus.PROPOSED,
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    primitive_v2 = primitive_v1.model_copy(
        update={
            "primitive_version_id": "primitive-1-v2",
            "semantic_version": "1.1.0",
            "predecessor_primitive_version_ids": ("primitive-1-v1",),
            "dependency_primitive_version_ids": ("primitive-1-v1",),
            "measurement_ids": ("measurement-1",),
            "status": PrimitiveStatus.EXPERIMENTAL,
        }
    )
    hypothesis_v1 = HypothesisVersionRecord(
        hypothesis_version_id="hypothesis-1-v1",
        hypothesis_id="hypothesis-1",
        version=1,
        statement="The proposed mechanism predicts a bounded deterministic response.",
        assumptions=("inputs satisfy the declared schema",),
        scope=("bounded in-memory simulations",),
        variables=("input", "response"),
        predictions=("response remains within the declared bound",),
        falsification_conditions=("response exceeds the declared bound",),
        primitive_version_ids=("primitive-1-v2",),
        evidence_ids=("evidence-1",),
        admission_status=HypothesisAdmissionStatus.TRANSFER_TESTING,
        proposer_id="proposer-1",
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    hypothesis_v2 = hypothesis_v1.model_copy(
        update={
            "hypothesis_version_id": "hypothesis-1-v2",
            "version": 2,
            "predictions": ("revised response remains within the declared bound",),
            "falsification_conditions": ("revised response exceeds the declared bound",),
            "admission_status": HypothesisAdmissionStatus.TRANSFER_VALIDATED,
        }
    )
    model = ExecutableModelSpecRecord(
        model_spec_id="model-1",
        hypothesis_version_id="hypothesis-1-v1",
        model_type=ModelType.DETERMINISTIC_SIMULATOR,
        execution_mode=ModelExecutionMode.METADATA_ONLY,
        artifact_hash=ARTIFACT_HASH,
        artifact_media_type="application/vnd.ssoh.model+json",
        artifact_size_bytes=128,
        artifact_name="bounded-model-v1",
        builtin_simulator_id=None,
        input_schema_id="input-schema-1",
        output_schema_id="output-schema-1",
        deterministic_seed=7,
        max_steps=100,
        max_state_bytes=4096,
        registered_by="model-author-1",
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    mechanism = VerificationMechanismSpecRecord(
        mechanism_spec_id="mechanism-1",
        hypothesis_version_id="hypothesis-1-v1",
        mechanism_category=VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER,
        name="bounded-response-checker",
        description="Checks the declared response bound deterministically.",
        specification_hash="c" * 64,
        input_schema_id="output-schema-1",
        output_schema_id="verification-schema-1",
        created_by="checker-author-1",
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    simulation = SimulationResultRecord(
        simulation_result_id="simulation-1",
        hypothesis_version_id="hypothesis-1-v1",
        model_spec_id="model-1",
        execution_mode=ModelExecutionMode.METADATA_ONLY,
        input_hash="d" * 64,
        output_hash="e" * 64,
        deterministic_seed=7,
        steps=12,
        state_bytes=512,
        completed_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    verification = VerificationResultRecord(
        verification_result_id="verification-1",
        hypothesis_version_id="hypothesis-1-v1",
        mechanism_spec_id="mechanism-1",
        mechanism_category=VerificationMechanismCategory.INDEPENDENT_DETERMINISTIC_CHECKER,
        result_category=VerificationResultCategory.DETERMINISTIC_CHECK_RESULT,
        model_spec_id="model-1",
        model_execution_mode=ModelExecutionMode.METADATA_ONLY,
        simulation_result_ids=("simulation-1",),
        outcome=VerificationOutcome.FAIL,
        findings=("the original bound was exceeded",),
        verified_by="checker-1",
        completed_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    evaluation = PrimitiveEvaluationRecord(
        primitive_evaluation_id="primitive-evaluation-1",
        primitive_version_id="primitive-1-v2",
        frame=PrimitiveEvaluationFrame.NEW_FRAME,
        verification_result_ids=("verification-1",),
        evidence_ids=("evidence-1",),
        criteria=("new prediction is independently operationalized",),
        findings=("the initial prediction failed and remains informative",),
        outcome=EvaluationOutcome.FAIL,
        evaluator_id="primitive-evaluator-1",
        evaluated_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    counterexample = CounterexampleRecord(
        counterexample_id="counterexample-1",
        hypothesis_version_id="hypothesis-1-v1",
        model_spec_id="model-1",
        model_execution_mode=ModelExecutionMode.METADATA_ONLY,
        simulation_result_ids=("simulation-1",),
        verification_result_ids=("verification-1",),
        evidence_ids=("evidence-1",),
        description="The response exceeded the original declared bound.",
        input_hash="d" * 64,
        observed_output_hash="e" * 64,
        expected_output_hash="f" * 64,
        discovered_by="counterexample-search-1",
        discovered_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    revision = HypothesisRevisionRecord(
        revision_id="revision-1",
        hypothesis_id="hypothesis-1",
        prior_hypothesis_version_id="hypothesis-1-v1",
        prior_version=1,
        resulting_hypothesis_version_id="hypothesis-1-v2",
        resulting_version=2,
        triggering_verification_result_ids=("verification-1",),
        considered_counterexample_ids=("counterexample-1",),
        assumptions_added=("revised bound is calibrated",),
        assumptions_removed=(),
        assumptions_changed=("response bound",),
        variables_added=(),
        variables_removed=(),
        variables_changed=("response",),
        mechanism_changes=("tighten the declared response transform",),
        preserved_elements=("bounded deterministic scope",),
        changed_predictions=("revised response remains bounded",),
        changed_falsification_conditions=("revised response exceeds the new bound",),
        author_id="hypothesis-author-1",
        revised_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    admission = HypothesisAdmissionDecisionRecord(
        admission_decision_id="admission-1",
        hypothesis_version_id="hypothesis-1-v2",
        hypothesis_id="hypothesis-1",
        version=2,
        admission_status=HypothesisAdmissionStatus.TRANSFER_VALIDATED,
        model_spec_ids=("model-1",),
        verification_result_ids=("verification-1",),
        counterexample_ids=("counterexample-1",),
        revision_ids=("revision-1",),
        outcome=AdmissionDecisionOutcome.ACCEPT,
        rationale="The failed version was preserved and the revision transferred.",
        decided_by="admission-reviewer-1",
        decided_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
    return _Records(
        primitive_v1=primitive_v1,
        primitive_v2=primitive_v2,
        hypothesis_v1=hypothesis_v1,
        hypothesis_v2=hypothesis_v2,
        model=model,
        mechanism=mechanism,
        simulation=simulation,
        verification=verification,
        evaluation=evaluation,
        counterexample=counterexample,
        revision=revision,
        admission=admission,
    )


def _add_records(connection: Connection, records: _Records) -> None:
    _seed_external_references(connection)
    primitive_repository = PrimitiveVersionRepository(connection)
    primitive_repository.add(
        records.primitive_v1.primitive_version_id,
        records.primitive_v1,
        records.primitive_v1.created_at,
    )
    primitive_repository.add(
        records.primitive_v2.primitive_version_id,
        records.primitive_v2,
        records.primitive_v2.created_at,
    )
    hypothesis_repository = HypothesisVersionRepository(connection)
    hypothesis_repository.add(
        records.hypothesis_v1.hypothesis_version_id,
        records.hypothesis_v1,
        records.hypothesis_v1.created_at,
    )
    hypothesis_repository.add(
        records.hypothesis_v2.hypothesis_version_id,
        records.hypothesis_v2,
        records.hypothesis_v2.created_at,
    )
    ExecutableModelSpecRepository(connection).add(
        records.model.model_spec_id,
        records.model,
        records.model.created_at,
    )
    VerificationMechanismSpecRepository(connection).add(
        records.mechanism.mechanism_spec_id,
        records.mechanism,
        records.mechanism.created_at,
    )
    SimulationResultRepository(connection).add(
        records.simulation.simulation_result_id,
        records.simulation,
        records.simulation.completed_at,
    )
    VerificationResultRepository(connection).add(
        records.verification.verification_result_id,
        records.verification,
        records.verification.completed_at,
    )
    PrimitiveEvaluationRepository(connection).add(
        records.evaluation.primitive_evaluation_id,
        records.evaluation,
        records.evaluation.evaluated_at,
    )
    CounterexampleRecordRepository(connection).add(
        records.counterexample.counterexample_id,
        records.counterexample,
        records.counterexample.discovered_at,
    )
    HypothesisRevisionRepository(connection).add(
        records.revision.revision_id,
        records.revision,
        records.revision.revised_at,
    )
    HypothesisAdmissionDecisionRepository(connection).add(
        records.admission.admission_decision_id,
        records.admission,
        records.admission.decided_at,
    )


def _seed_external_references(connection: Connection) -> None:
    values = {"record_json": "{}", "digest": "9" * 64, "created_at": NOW.isoformat()}
    connection.execute(
        text(
            "INSERT INTO evidence_records "
            "(evidence_id, record_json, content_hash, created_at) "
            "VALUES ('evidence-1', :record_json, :digest, :created_at)"
        ),
        values,
    )
    connection.execute(
        text(
            "INSERT INTO research_runs (run_id, record_json, content_hash, created_at) "
            "VALUES ('run-1', :record_json, :digest, :created_at)"
        ),
        values,
    )
    connection.execute(
        text(
            "INSERT INTO evaluator_audits "
            "(evaluator_audit_id, record_json, content_hash, created_at) "
            "VALUES ('evaluator-audit-1', :record_json, :digest, :created_at)"
        ),
        values,
    )
    connection.execute(
        text(
            "INSERT INTO self_improvement_measurements "
            "(measurement_id, run_id, evaluator_audit_id, record_json, content_hash, created_at) "
            "VALUES ('measurement-1', 'run-1', 'evaluator-audit-1', "
            ":record_json, :digest, :created_at)"
        ),
        values,
    )


def _connection(tmp_path: Path, filename: str) -> tuple[object, Connection]:
    return _connection_for_path(tmp_path / filename)


def _connection_for_path(database_path: Path) -> tuple[object, Connection]:
    url = f"sqlite:///{database_path.as_posix()}"
    upgrade_database(url)
    engine = create_database_engine(url)
    connection = engine.connect()
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    return engine, connection


def _ordered_references(
    connection: Connection,
    table: object,
    owner_column: str,
    owner_id: str,
    reference_column: str,
) -> tuple[str, ...]:
    rows = connection.execute(
        select(table.c[reference_column])  # type: ignore[attr-defined]
        .where(table.c[owner_column] == owner_id)  # type: ignore[attr-defined]
        .order_by(table.c.position)  # type: ignore[attr-defined]
    )
    return tuple(str(row[0]) for row in rows)
