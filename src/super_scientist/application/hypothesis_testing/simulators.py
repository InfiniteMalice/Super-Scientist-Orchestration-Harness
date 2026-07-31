from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import exp
from types import MappingProxyType
from typing import Protocol

from super_scientist.domain.hypotheses.models import (
    ExecutableModelSpec,
    ExecutionMode,
    ModelInput,
    ModelOutput,
    NumericField,
    NumericValue,
)
from super_scientist.domain.primitives import StableIdentifier, canonical_json_bytes


class UnsupportedModelError(ValueError):
    """The retained model metadata carries no executable authority."""


class UnknownSimulatorError(ValueError):
    """The requested identifier is absent from the source-controlled registry."""


class ModelSchemaError(ValueError):
    """A strict simulator input or output schema was not satisfied."""


class ModelBoundsError(ValueError):
    """A deterministic seed, step, or state bound was violated."""


class DeterministicSimulator(Protocol):
    @property
    def simulator_id(self) -> str: ...

    @property
    def input_schema_id(self) -> str: ...

    @property
    def output_schema_id(self) -> str: ...

    def run(
        self,
        model_input: ModelInput,
        *,
        output_id: StableIdentifier,
        max_steps: int,
        max_state_bytes: int,
    ) -> ModelOutput: ...


@dataclass(frozen=True, slots=True)
class ThermalChamberSimulator:
    simulator_id: str = "thermal-chamber-v1"
    input_schema_id: str = "thermal-chamber-input-v1"
    output_schema_id: str = "thermal-chamber-output-v1"

    def run(
        self,
        model_input: ModelInput,
        *,
        output_id: StableIdentifier,
        max_steps: int,
        max_state_bytes: int,
    ) -> ModelOutput:
        values = _exact_values(
            model_input,
            (
                "initial_temperature",
                "ambient_temperature",
                "heater_delta",
                "cooling_rate",
                "steps",
            ),
        )
        initial = _finite_number(values[0], "initial_temperature")
        ambient = _finite_number(values[1], "ambient_temperature")
        heater_delta = _finite_number(values[2], "heater_delta")
        cooling_rate = _finite_number(values[3], "cooling_rate")
        steps = _positive_steps(values[4], max_steps)
        if heater_delta < 0.0:
            raise ModelSchemaError("heater_delta must be nonnegative")
        if not 0.0 <= cooling_rate <= 1.0:
            raise ModelSchemaError("cooling_rate must be between zero and one")
        temperature = initial
        peak = initial
        for _ in range(steps):
            temperature += heater_delta
            temperature -= cooling_rate * (temperature - ambient)
            peak = max(peak, temperature)
        output_values = (
            NumericField(name="final_temperature", value=float(temperature)),
            NumericField(name="peak_temperature", value=float(peak)),
        )
        return _bounded_output(
            output_id,
            self.output_schema_id,
            output_values,
            steps,
            max_state_bytes,
        )


@dataclass(frozen=True, slots=True)
class ExponentialDecaySimulator:
    simulator_id: str = "exponential-decay-v1"
    input_schema_id: str = "exponential-decay-input-v1"
    output_schema_id: str = "exponential-decay-output-v1"

    def run(
        self,
        model_input: ModelInput,
        *,
        output_id: StableIdentifier,
        max_steps: int,
        max_state_bytes: int,
    ) -> ModelOutput:
        values = _exact_values(
            model_input,
            ("initial_value", "decay_rate", "step_duration", "steps"),
        )
        initial = _finite_number(values[0], "initial_value")
        decay_rate = _finite_number(values[1], "decay_rate")
        step_duration = _finite_number(values[2], "step_duration")
        steps = _positive_steps(values[3], max_steps)
        if initial < 0.0 or decay_rate < 0.0 or step_duration <= 0.0:
            raise ModelSchemaError("decay inputs must be nonnegative with positive duration")
        final_value = initial * exp(-decay_rate * step_duration * steps)
        output_values = (NumericField(name="final_value", value=float(final_value)),)
        return _bounded_output(
            output_id,
            self.output_schema_id,
            output_values,
            steps,
            max_state_bytes,
        )


class SimulatorRegistry:
    """Immutable source-controlled registry; it accepts no runtime registrations."""

    __slots__ = ("_simulators",)

    def __init__(self) -> None:
        simulators: dict[str, DeterministicSimulator] = {
            "thermal-chamber-v1": ThermalChamberSimulator(),
            "exponential-decay-v1": ExponentialDecaySimulator(),
        }
        self._simulators: Mapping[str, DeterministicSimulator] = MappingProxyType(simulators)

    @property
    def simulators(self) -> Mapping[str, DeterministicSimulator]:
        return self._simulators

    def available_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._simulators))

    def resolve(self, simulator_id: str) -> DeterministicSimulator:
        try:
            return self._simulators[simulator_id]
        except KeyError as error:
            raise UnknownSimulatorError(f"unknown builtin simulator: {simulator_id}") from error

    def execute(
        self,
        model: ExecutableModelSpec,
        model_input: ModelInput,
        *,
        output_id: StableIdentifier,
    ) -> ModelOutput:
        if model.execution_mode is ExecutionMode.METADATA_ONLY:
            raise UnsupportedModelError("metadata-only model artifacts are never executed")
        simulator_id = model.builtin_simulator_id
        if simulator_id is None:
            raise UnsupportedModelError("builtin execution requires a registered simulator")
        simulator = self.resolve(simulator_id)
        if model_input.deterministic_seed != model.deterministic_seed:
            raise ModelBoundsError(
                "input seed must exactly match the registered deterministic seed"
            )
        if (
            model.input_schema_id != simulator.input_schema_id
            or model.output_schema_id != simulator.output_schema_id
            or model_input.schema_id != simulator.input_schema_id
        ):
            raise ModelSchemaError("model and input schemas must match the registered simulator")
        return simulator.run(
            model_input,
            output_id=output_id,
            max_steps=model.max_steps,
            max_state_bytes=model.max_state_bytes,
        )


def _exact_values(
    model_input: ModelInput,
    names: tuple[str, ...],
) -> tuple[NumericValue, ...]:
    actual_names = tuple(item.name for item in model_input.values)
    if actual_names != names:
        raise ModelSchemaError("numeric fields must exactly match the registered schema order")
    return tuple(item.value for item in model_input.values)


def _finite_number(value: NumericValue, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelSchemaError(f"{label} must be a strict numeric value")
    return float(value)


def _positive_steps(value: NumericValue, max_steps: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ModelSchemaError("steps must be a positive strict integer")
    if value > max_steps:
        raise ModelBoundsError("requested steps exceed the registered maximum")
    return value


def _bounded_output(
    output_id: StableIdentifier,
    schema_id: StableIdentifier,
    values: tuple[NumericField, ...],
    steps: int,
    max_state_bytes: int,
) -> ModelOutput:
    state_bytes = len(
        canonical_json_bytes(
            {
                "schema_id": schema_id,
                "values": tuple(item.model_dump(mode="json") for item in values),
                "steps": steps,
            }
        )
    )
    if state_bytes > max_state_bytes:
        raise ModelBoundsError("in-memory state exceeds the registered state bound")
    return ModelOutput(
        model_output_id=output_id,
        schema_id=schema_id,
        values=values,
        steps=steps,
        state_bytes=state_bytes,
    )


__all__ = [
    "DeterministicSimulator",
    "ExponentialDecaySimulator",
    "ModelBoundsError",
    "ModelSchemaError",
    "SimulatorRegistry",
    "ThermalChamberSimulator",
    "UnknownSimulatorError",
    "UnsupportedModelError",
]
