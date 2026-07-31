from __future__ import annotations

from datetime import UTC, datetime
from math import exp

import pytest

from super_scientist.application.hypothesis_testing.simulators import (
    ModelBoundsError,
    ModelSchemaError,
    SimulatorRegistry,
    UnknownSimulatorError,
    UnsupportedModelError,
)
from super_scientist.domain.hypotheses.models import (
    ExecutableModelSpec,
    ExecutionMode,
    ModelInput,
    ModelType,
    NumericField,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import sha256_hex

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
HASH = sha256_hex(b"simulators")


def _actor() -> ActorIdentity:
    return ActorIdentity(actor_id="registrar", kind=ActorKind.HUMAN, created_at=NOW)


def _model(
    simulator_id: str,
    input_schema_id: str,
    output_schema_id: str,
    *,
    seed: int = 7,
    max_steps: int = 10,
    max_state_bytes: int = 4_096,
) -> ExecutableModelSpec:
    return ExecutableModelSpec(
        model_spec_id=f"model-{simulator_id}",
        hypothesis_version_id="hypothesis-v1",
        model_type=ModelType.DETERMINISTIC_SIMULATOR,
        execution_mode=ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
        artifact_hash=None,
        artifact_media_type=None,
        artifact_size_bytes=None,
        artifact_name=f"source-controlled {simulator_id}",
        builtin_simulator_id=simulator_id,
        input_schema_id=input_schema_id,
        output_schema_id=output_schema_id,
        deterministic_seed=seed,
        max_steps=max_steps,
        max_state_bytes=max_state_bytes,
        registered_by=_actor(),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def _input(schema_id: str, values: tuple[NumericField, ...], *, seed: int = 7) -> ModelInput:
    return ModelInput(
        model_input_id="input-1",
        schema_id=schema_id,
        values=values,
        deterministic_seed=seed,
    )


def test_registry_is_fixed_to_two_source_controlled_simulators() -> None:
    registry = SimulatorRegistry()

    assert registry.available_ids() == (
        "exponential-decay-v1",
        "thermal-chamber-v1",
    )
    with pytest.raises(TypeError):
        registry.simulators["untrusted"] = object()  # type: ignore[index]


def test_thermal_simulator_is_deterministic_and_bounded() -> None:
    registry = SimulatorRegistry()
    model = _model(
        "thermal-chamber-v1",
        "thermal-chamber-input-v1",
        "thermal-chamber-output-v1",
    )
    model_input = _input(
        "thermal-chamber-input-v1",
        (
            NumericField(name="initial_temperature", value=20.0),
            NumericField(name="ambient_temperature", value=20.0),
            NumericField(name="heater_delta", value=5.0),
            NumericField(name="cooling_rate", value=0.1),
            NumericField(name="steps", value=3),
        ),
    )

    first = registry.execute(model, model_input, output_id="thermal-output-1")
    second = registry.execute(model, model_input, output_id="thermal-output-1")

    assert first == second
    assert first.steps == 3
    assert first.numeric_value("final_temperature") == pytest.approx(32.195)
    assert first.numeric_value("peak_temperature") == pytest.approx(32.195)
    assert first.state_bytes <= model.max_state_bytes


def test_exponential_decay_simulator_matches_registered_equation() -> None:
    registry = SimulatorRegistry()
    model = _model(
        "exponential-decay-v1",
        "exponential-decay-input-v1",
        "exponential-decay-output-v1",
        seed=11,
    )
    model_input = _input(
        "exponential-decay-input-v1",
        (
            NumericField(name="initial_value", value=100.0),
            NumericField(name="decay_rate", value=0.2),
            NumericField(name="step_duration", value=0.5),
            NumericField(name="steps", value=4),
        ),
        seed=11,
    )

    output = registry.execute(model, model_input, output_id="decay-output-1")

    assert output.numeric_value("final_value") == pytest.approx(100.0 * exp(-0.2 * 0.5 * 4))
    assert output.steps == 4


def test_metadata_only_model_never_executes() -> None:
    model = _model(
        "thermal-chamber-v1",
        "thermal-chamber-input-v1",
        "thermal-chamber-output-v1",
    ).model_copy(
        update={
            "execution_mode": ExecutionMode.METADATA_ONLY,
            "artifact_hash": HASH,
            "artifact_media_type": "application/octet-stream",
            "artifact_size_bytes": 12,
            "artifact_name": "malicious.py",
            "builtin_simulator_id": None,
        }
    )
    model_input = _input(
        "thermal-chamber-input-v1",
        (
            NumericField(name="initial_temperature", value=20.0),
            NumericField(name="ambient_temperature", value=20.0),
            NumericField(name="heater_delta", value=1.0),
            NumericField(name="cooling_rate", value=0.1),
            NumericField(name="steps", value=1),
        ),
    )

    with pytest.raises(UnsupportedModelError, match="metadata-only"):
        SimulatorRegistry().execute(model, model_input, output_id="forbidden-output")


def test_unknown_simulator_identifier_fails_closed() -> None:
    registry = SimulatorRegistry()

    with pytest.raises(UnknownSimulatorError, match="unknown"):
        registry.resolve("user-provided-simulator")

    model = _model(
        "user-provided-simulator",
        "thermal-chamber-input-v1",
        "thermal-chamber-output-v1",
    )
    model_input = _input(
        "thermal-chamber-input-v1",
        (
            NumericField(name="initial_temperature", value=20.0),
            NumericField(name="ambient_temperature", value=20.0),
            NumericField(name="heater_delta", value=1.0),
            NumericField(name="cooling_rate", value=0.1),
            NumericField(name="steps", value=1),
        ),
    )
    with pytest.raises(UnknownSimulatorError):
        registry.execute(model, model_input, output_id="unknown-output")


@pytest.mark.parametrize(
    ("change", "error_type"),
    [
        ({"deterministic_seed": 99}, ModelBoundsError),
        ({"schema_id": "wrong-input-schema"}, ModelSchemaError),
    ],
)
def test_seed_and_schema_must_exactly_match_registered_model(
    change: dict[str, object],
    error_type: type[Exception],
) -> None:
    model = _model(
        "thermal-chamber-v1",
        "thermal-chamber-input-v1",
        "thermal-chamber-output-v1",
    )
    model_input = _input(
        "thermal-chamber-input-v1",
        (
            NumericField(name="initial_temperature", value=20.0),
            NumericField(name="ambient_temperature", value=20.0),
            NumericField(name="heater_delta", value=1.0),
            NumericField(name="cooling_rate", value=0.1),
            NumericField(name="steps", value=1),
        ),
    ).model_copy(update=change)

    with pytest.raises(error_type):
        SimulatorRegistry().execute(model, model_input, output_id="invalid-output")


def test_step_and_state_bounds_fail_before_returning_output() -> None:
    registry = SimulatorRegistry()
    too_few_steps = _model(
        "thermal-chamber-v1",
        "thermal-chamber-input-v1",
        "thermal-chamber-output-v1",
        max_steps=2,
    )
    model_input = _input(
        "thermal-chamber-input-v1",
        (
            NumericField(name="initial_temperature", value=20.0),
            NumericField(name="ambient_temperature", value=20.0),
            NumericField(name="heater_delta", value=1.0),
            NumericField(name="cooling_rate", value=0.1),
            NumericField(name="steps", value=3),
        ),
    )
    with pytest.raises(ModelBoundsError, match="steps"):
        registry.execute(too_few_steps, model_input, output_id="too-many-steps")

    too_few_bytes = too_few_steps.model_copy(update={"max_steps": 3, "max_state_bytes": 1})
    with pytest.raises(ModelBoundsError, match="state"):
        registry.execute(too_few_bytes, model_input, output_id="too-many-bytes")


@pytest.mark.parametrize(
    "values",
    [
        (NumericField(name="steps", value=1),),
        (
            NumericField(name="initial_temperature", value=20.0),
            NumericField(name="ambient_temperature", value=20.0),
            NumericField(name="heater_delta", value=1.0),
            NumericField(name="cooling_rate", value=1.1),
            NumericField(name="steps", value=1),
        ),
    ],
)
def test_simulator_rejects_missing_or_out_of_range_numeric_fields(
    values: tuple[NumericField, ...],
) -> None:
    model = _model(
        "thermal-chamber-v1",
        "thermal-chamber-input-v1",
        "thermal-chamber-output-v1",
    )
    with pytest.raises(ModelSchemaError):
        SimulatorRegistry().execute(
            model,
            _input("thermal-chamber-input-v1", values),
            output_id="bad-fields",
        )


@pytest.mark.parametrize(
    ("simulator_id", "input_schema", "output_schema", "values", "message"),
    [
        (
            "thermal-chamber-v1",
            "thermal-chamber-input-v1",
            "thermal-chamber-output-v1",
            (
                NumericField(name="initial_temperature", value=20.0),
                NumericField(name="ambient_temperature", value=20.0),
                NumericField(name="heater_delta", value=-0.1),
                NumericField(name="cooling_rate", value=0.1),
                NumericField(name="steps", value=1),
            ),
            "heater_delta",
        ),
        (
            "exponential-decay-v1",
            "exponential-decay-input-v1",
            "exponential-decay-output-v1",
            (
                NumericField(name="initial_value", value=-1.0),
                NumericField(name="decay_rate", value=0.2),
                NumericField(name="step_duration", value=0.5),
                NumericField(name="steps", value=1),
            ),
            "decay inputs",
        ),
    ],
)
def test_simulators_reject_domain_invalid_numeric_ranges(
    simulator_id: str,
    input_schema: str,
    output_schema: str,
    values: tuple[NumericField, ...],
    message: str,
) -> None:
    with pytest.raises(ModelSchemaError, match=message):
        SimulatorRegistry().execute(
            _model(simulator_id, input_schema, output_schema),
            _input(input_schema, values),
            output_id="invalid-range-output",
        )


def test_registry_revalidates_builtin_identity_and_strict_runtime_numbers() -> None:
    registry = SimulatorRegistry()
    model = _model(
        "thermal-chamber-v1",
        "thermal-chamber-input-v1",
        "thermal-chamber-output-v1",
    )
    values = (
        NumericField(name="initial_temperature", value=20.0),
        NumericField(name="ambient_temperature", value=20.0),
        NumericField(name="heater_delta", value=1.0),
        NumericField(name="cooling_rate", value=0.1),
        NumericField(name="steps", value=1),
    )

    with pytest.raises(UnsupportedModelError, match="requires a registered simulator"):
        registry.execute(
            model.model_copy(update={"builtin_simulator_id": None}),
            _input("thermal-chamber-input-v1", values),
            output_id="missing-simulator-output",
        )

    non_numeric = values[0].model_copy(update={"value": True})
    with pytest.raises(ModelSchemaError, match="strict numeric"):
        registry.execute(
            model,
            _input("thermal-chamber-input-v1", (non_numeric, *values[1:])),
            output_id="boolean-number-output",
        )

    non_integer_steps = values[-1].model_copy(update={"value": 1.0})
    with pytest.raises(ModelSchemaError, match="positive strict integer"):
        registry.execute(
            model,
            _input("thermal-chamber-input-v1", (*values[:-1], non_integer_steps)),
            output_id="float-step-output",
        )
