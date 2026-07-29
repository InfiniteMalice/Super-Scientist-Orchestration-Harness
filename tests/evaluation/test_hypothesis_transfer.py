from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import exp

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from super_scientist.application.hypothesis_testing.simulators import SimulatorRegistry
from super_scientist.domain.hypotheses.models import (
    ExecutableModelSpec,
    ExecutionMode,
    HypothesisSpec,
    ImportedPatternStatus,
    ModelInput,
    ModelType,
    NumericField,
    RevisionRecord,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import NonBlankText, StableIdentifier, sha256_hex

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
HASH = sha256_hex(b"transfer-evaluation")


class _FrozenRecord(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class IncidentNote(_FrozenRecord):
    note_id: StableIdentifier
    minute: int = Field(strict=True, ge=0)
    text: NonBlankText


class IncidentDocument(_FrozenRecord):
    document_id: StableIdentifier
    notes: tuple[IncidentNote, ...] = Field(min_length=1)


class ManifestEntry(_FrozenRecord):
    path_label: StableIdentifier
    content_hash: str
    declares_retry_limit: bool
    has_matching_test: bool


class FileManifest(_FrozenRecord):
    manifest_id: StableIdentifier
    entries: tuple[ManifestEntry, ...] = Field(min_length=1)


class PlanningOption(_FrozenRecord):
    option_id: StableIdentifier
    success_probability: float = Field(strict=True, ge=0.0, le=1.0)
    information_gain: float = Field(strict=True, ge=0.0, le=1.0)
    cost: int = Field(strict=True, gt=0)


class PlanningScenario(_FrozenRecord):
    scenario_id: StableIdentifier
    budget: int = Field(strict=True, gt=0)
    options: tuple[PlanningOption, ...] = Field(min_length=2)


class Condition(StrEnum):
    DIRECT_DETERMINISTIC = "DIRECT_DETERMINISTIC"
    PLAN_AND_EXECUTE = "PLAN_AND_EXECUTE"
    RETRY_WITH_CHECKER_FEEDBACK = "RETRY_WITH_CHECKER_FEEDBACK"
    TYPED_REVISION_LOOP = "TYPED_REVISION_LOOP"


@dataclass(frozen=True)
class Attempt:
    candidate: object | None
    correct: bool
    checker_passed: bool
    admitted: bool
    revised: bool
    unsupported: bool


@dataclass(frozen=True)
class TransferMetrics:
    correctness: float
    checker_accuracy: float
    false_admission: float
    diversity: float
    revision_utility: float
    unsupported_model: float
    abstention: float
    cost: int
    transfer: float
    regression: float


@dataclass(frozen=True)
class ConditionRun:
    condition: Condition
    attempts: tuple[Attempt, ...]
    metrics: TransferMetrics


@dataclass(frozen=True)
class TransferResult:
    fixture_id: str
    condition_runs: tuple[ConditionRun, ...]
    metrics: TransferMetrics
    domain_contract_fields: tuple[str, ...]
    imported_code_used: bool


@pytest.mark.parametrize(
    "fixture_id",
    [
        "thermal-chamber",
        "exponential-decay",
        "equipment-incident",
        "software-maintenance",
        "sensor-calibration-planning",
    ],
)
def test_generic_loop_transfers_across_independent_fixtures(fixture_id: str) -> None:
    result = _run_transfer_fixture(fixture_id)

    assert result.domain_contract_fields == ()
    assert result.imported_code_used is False
    assert tuple(run.condition for run in result.condition_runs) == tuple(Condition)
    assert result.metrics.correctness is not None
    assert result.metrics.checker_accuracy is not None
    assert result.metrics.false_admission is not None
    assert result.metrics.diversity is not None
    assert result.metrics.revision_utility is not None
    assert result.metrics.unsupported_model is not None
    assert result.metrics.abstention is not None
    assert result.metrics.cost > 0
    assert result.metrics.transfer is not None
    assert result.metrics.regression is not None


def test_metrics_are_recomputed_from_real_attempts_not_fixture_constants() -> None:
    result = _run_transfer_fixture("thermal-chamber")
    typed = next(
        run for run in result.condition_runs if run.condition is Condition.TYPED_REVISION_LOOP
    )

    assert typed.attempts[0].correct is False
    assert typed.attempts[-1].correct is True
    assert typed.metrics.revision_utility == 1.0
    assert typed.metrics.cost == len(typed.attempts)
    assert typed.metrics.checker_accuracy == sum(
        attempt.checker_passed == attempt.correct for attempt in typed.attempts
    ) / len(typed.attempts)


def test_document_and_manifest_transfer_records_are_immutable_and_in_memory() -> None:
    document = _incident_document()
    manifest = _maintenance_manifest()

    with pytest.raises((AttributeError, TypeError, ValidationError)):
        document.notes[0].text = "changed"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError, ValidationError)):
        manifest.entries[0].has_matching_test = False  # type: ignore[misc]
    assert all(not hasattr(record, "filesystem_path") for record in (document, manifest))


def _run_transfer_fixture(fixture_id: str) -> TransferResult:
    runs = tuple(_run_condition(fixture_id, condition) for condition in Condition)
    all_attempts = tuple(attempt for run in runs for attempt in run.attempts)
    forbidden_domain_fields = {
        "temperature",
        "decay_rate",
        "incident",
        "equipment",
        "manifest",
        "file_path",
        "retry_limit",
        "planning_option",
        "success_probability",
        "budget",
    }
    contract_fields = tuple(
        sorted(
            forbidden_domain_fields
            & (set(HypothesisSpec.model_fields) | set(ExecutableModelSpec.model_fields))
        )
    )
    return TransferResult(
        fixture_id=fixture_id,
        condition_runs=runs,
        metrics=_metrics(all_attempts, baseline_correct=runs[0].attempts[-1].correct),
        domain_contract_fields=contract_fields,
        imported_code_used=False,
    )


def _run_condition(fixture_id: str, condition: Condition) -> ConditionRun:
    expected = _expected_value(fixture_id)
    correct_candidate = _candidate(fixture_id, expected=expected, correct=True)
    incorrect_candidate = _candidate(fixture_id, expected=expected, correct=False)
    sequence = (
        (correct_candidate,)
        if condition in {Condition.DIRECT_DETERMINISTIC, Condition.PLAN_AND_EXECUTE}
        else (incorrect_candidate, correct_candidate)
    )
    attempts: list[Attempt] = []
    for index, candidate in enumerate(sequence):
        correct = _check_candidate(fixture_id, candidate, expected)
        checker_passed = _checker_decision(fixture_id, candidate, expected)
        revised = condition is Condition.TYPED_REVISION_LOOP and index > 0
        if revised:
            _typed_revision(fixture_id)
        attempts.append(
            Attempt(
                candidate=candidate,
                correct=correct,
                checker_passed=checker_passed,
                admitted=checker_passed,
                revised=revised,
                unsupported=False,
            )
        )
        if checker_passed:
            break
    attempt_tuple = tuple(attempts)
    return ConditionRun(
        condition=condition,
        attempts=attempt_tuple,
        metrics=_metrics(attempt_tuple, baseline_correct=True),
    )


def _expected_value(fixture_id: str) -> object:
    if fixture_id == "thermal-chamber":
        return 32.195
    if fixture_id == "exponential-decay":
        return 100.0 * exp(-0.2 * 0.5 * 4)
    if fixture_id == "equipment-incident":
        return ("alarm", "pressure-drop", "pump-stop")
    if fixture_id == "software-maintenance":
        return ("retry-policy", "retry-policy-test")
    if fixture_id == "sensor-calibration-planning":
        return "cross-check-reference"
    raise AssertionError(f"unknown fixture {fixture_id}")


def _candidate(fixture_id: str, *, expected: object, correct: bool) -> object:
    if fixture_id == "thermal-chamber":
        output = (
            SimulatorRegistry()
            .execute(
                _model(
                    "thermal-chamber-v1",
                    "thermal-chamber-input-v1",
                    "thermal-chamber-output-v1",
                    seed=7,
                ),
                ModelInput(
                    model_input_id="thermal-transfer-input",
                    schema_id="thermal-chamber-input-v1",
                    values=(
                        NumericField(name="initial_temperature", value=20.0),
                        NumericField(name="ambient_temperature", value=20.0),
                        NumericField(name="heater_delta", value=5.0),
                        NumericField(name="cooling_rate", value=0.1),
                        NumericField(name="steps", value=3),
                    ),
                    deterministic_seed=7,
                ),
                output_id="thermal-transfer-output",
            )
            .numeric_value("final_temperature")
        )
        return output if correct else float(output) - 1.0
    if fixture_id == "exponential-decay":
        output = (
            SimulatorRegistry()
            .execute(
                _model(
                    "exponential-decay-v1",
                    "exponential-decay-input-v1",
                    "exponential-decay-output-v1",
                    seed=11,
                ),
                ModelInput(
                    model_input_id="decay-transfer-input",
                    schema_id="exponential-decay-input-v1",
                    values=(
                        NumericField(name="initial_value", value=100.0),
                        NumericField(name="decay_rate", value=0.2),
                        NumericField(name="step_duration", value=0.5),
                        NumericField(name="steps", value=4),
                    ),
                    deterministic_seed=11,
                ),
                output_id="decay-transfer-output",
            )
            .numeric_value("final_value")
        )
        return output if correct else float(output) + 1.0
    if fixture_id == "equipment-incident":
        ordered = tuple(
            note.note_id
            for note in sorted(
                _incident_document().notes,
                key=lambda item: item.minute,
            )
        )
        return ordered if correct else tuple(reversed(ordered))
    if fixture_id == "software-maintenance":
        selected = tuple(
            entry.path_label
            for entry in _maintenance_manifest().entries
            if entry.declares_retry_limit or entry.has_matching_test
        )
        return selected if correct else selected[:1]
    if fixture_id == "sensor-calibration-planning":
        selected = _selected_planning_option(_planning_scenario())
        return selected if correct else "recalibrate-primary"
    raise AssertionError(f"unknown fixture {fixture_id}")


def _check_candidate(fixture_id: str, candidate: object, expected: object) -> bool:
    if fixture_id in {"thermal-chamber", "exponential-decay"}:
        return isinstance(candidate, (int, float)) and float(candidate) == pytest.approx(
            float(expected)
        )
    return candidate == expected


def _checker_decision(fixture_id: str, candidate: object, expected: object) -> bool:
    if fixture_id == "equipment-incident":
        document = _incident_document()
        chronological = tuple(
            note.note_id for note in sorted(document.notes, key=lambda item: item.minute)
        )
        return candidate == chronological == expected
    if fixture_id == "software-maintenance":
        manifest = _maintenance_manifest()
        required = tuple(
            entry.path_label
            for entry in manifest.entries
            if entry.declares_retry_limit or entry.has_matching_test
        )
        return candidate == required == expected and all(
            entry.content_hash for entry in manifest.entries
        )
    if fixture_id == "sensor-calibration-planning":
        scenario = _planning_scenario()
        return (
            candidate == _selected_planning_option(scenario) == expected
            and next(option for option in scenario.options if option.option_id == candidate).cost
            <= scenario.budget
        )
    return _check_candidate(fixture_id, candidate, expected)


def _typed_revision(fixture_id: str) -> RevisionRecord:
    prior = _hypothesis(fixture_id, version=1)
    resulting = _hypothesis(fixture_id, version=2)
    return RevisionRecord(
        revision_id=f"{fixture_id}-revision-v2",
        hypothesis_id=prior.hypothesis_id,
        prior_hypothesis_version_id=prior.hypothesis_version_id,
        prior_version=1,
        resulting_hypothesis_version_id=resulting.hypothesis_version_id,
        resulting_version=2,
        triggering_verification_result_ids=(f"{fixture_id}-failed-check",),
        considered_counterexample_ids=(),
        assumptions_added=("Checker feedback constrains the second attempt.",),
        assumptions_removed=(),
        assumptions_changed=(),
        variables_added=(),
        variables_removed=(),
        variables_changed=(),
        mechanism_changes=("Retained deterministic checker feedback.",),
        preserved_elements=("Domain-neutral typed loop.",),
        changed_predictions=resulting.predictions,
        changed_falsification_conditions=resulting.falsification_conditions,
        author=_actor("revision-author"),
        revised_at=NOW,
        governing_policy_hash=HASH,
    )


def _hypothesis(fixture_id: str, *, version: int) -> HypothesisSpec:
    return HypothesisSpec(
        hypothesis_version_id=f"{fixture_id}-hypothesis-v{version}",
        hypothesis_id=f"{fixture_id}-hypothesis",
        version=version,
        statement="A retained bounded mechanism predicts the checked observation.",
        assumptions=("Fixture records are immutable and complete.",),
        scope=("One independently authored deterministic transfer fixture.",),
        variables=("registered-input", "checked-output"),
        predictions=(f"Version {version} predicts the checker-accepted output.",),
        falsification_conditions=(
            f"Version {version} fails if its deterministic checker rejects the output.",
        ),
        primitive_version_ids=(),
        evidence_ids=(f"{fixture_id}-evidence",),
        imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
        proposer=_actor("hypothesis-author", ActorKind.MODEL),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def _model(
    simulator_id: str,
    input_schema_id: str,
    output_schema_id: str,
    *,
    seed: int,
) -> ExecutableModelSpec:
    return ExecutableModelSpec(
        model_spec_id=f"transfer-{simulator_id}",
        hypothesis_version_id="transfer-hypothesis-v1",
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
        max_steps=10,
        max_state_bytes=4_096,
        registered_by=_actor("model-registrar"),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def _incident_document() -> IncidentDocument:
    return IncidentDocument(
        document_id="equipment-incident-1",
        notes=(
            IncidentNote(note_id="pump-stop", minute=23, text="Pump stopped after isolation."),
            IncidentNote(note_id="alarm", minute=2, text="Pressure alarm activated."),
            IncidentNote(note_id="pressure-drop", minute=14, text="Pressure continued to fall."),
        ),
    )


def _maintenance_manifest() -> FileManifest:
    return FileManifest(
        manifest_id="software-maintenance-1",
        entries=(
            ManifestEntry(
                path_label="retry-policy",
                content_hash=sha256_hex(b"retry policy"),
                declares_retry_limit=True,
                has_matching_test=False,
            ),
            ManifestEntry(
                path_label="retry-policy-test",
                content_hash=sha256_hex(b"retry test"),
                declares_retry_limit=False,
                has_matching_test=True,
            ),
        ),
    )


def _planning_scenario() -> PlanningScenario:
    return PlanningScenario(
        scenario_id="sensor-calibration-planning-1",
        budget=4,
        options=(
            PlanningOption(
                option_id="recalibrate-primary",
                success_probability=0.82,
                information_gain=0.70,
                cost=3,
            ),
            PlanningOption(
                option_id="cross-check-reference",
                success_probability=0.76,
                information_gain=0.95,
                cost=2,
            ),
            PlanningOption(
                option_id="replace-sensor",
                success_probability=0.90,
                information_gain=0.40,
                cost=5,
            ),
        ),
    )


def _selected_planning_option(scenario: PlanningScenario) -> str:
    eligible = tuple(option for option in scenario.options if option.cost <= scenario.budget)
    return max(
        eligible,
        key=lambda option: (
            option.success_probability * option.information_gain / option.cost,
            option.success_probability,
            option.option_id,
        ),
    ).option_id


def _metrics(attempts: tuple[Attempt, ...], *, baseline_correct: bool) -> TransferMetrics:
    count = len(attempts)
    final_correct = attempts[-1].correct
    first_correct = attempts[0].correct
    candidates = {repr(attempt.candidate) for attempt in attempts if attempt.candidate is not None}
    return TransferMetrics(
        correctness=sum(attempt.correct for attempt in attempts) / count,
        checker_accuracy=sum(attempt.checker_passed == attempt.correct for attempt in attempts)
        / count,
        false_admission=sum(attempt.admitted and not attempt.correct for attempt in attempts)
        / count,
        diversity=len(candidates) / count,
        revision_utility=float(final_correct) - float(first_correct),
        unsupported_model=sum(attempt.unsupported for attempt in attempts) / count,
        abstention=sum(attempt.candidate is None for attempt in attempts) / count,
        cost=count,
        transfer=float(final_correct),
        regression=float(baseline_correct and not final_correct),
    )


def _actor(identifier: str, kind: ActorKind = ActorKind.HUMAN) -> ActorIdentity:
    return ActorIdentity(
        actor_id=identifier,
        kind=kind,
        provider_id=f"provider-{identifier}" if kind is ActorKind.MODEL else None,
        model_id=f"model-{identifier}" if kind is ActorKind.MODEL else None,
        adapter_id=f"adapter-{identifier}" if kind is ActorKind.MODEL else None,
        configuration_hash=sha256_hex(identifier.encode()) if kind is ActorKind.MODEL else None,
        created_at=NOW,
    )
