from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.hypotheses.models import (
    VERIFICATION_MECHANISM_ADAPTER,
    VERIFICATION_RESULT_ADAPTER,
    AdmissionOutcome,
    CounterexampleRecord,
    DeterministicCheckerSpec,
    DeterministicCheckResult,
    ExecutableModelSpec,
    ExecutionMode,
    FormalVerificationResult,
    HypothesisAdmissionDecision,
    HypothesisSpec,
    ImportedPatternStatus,
    LearnedJudgeResult,
    ModelInput,
    ModelOutput,
    ModelType,
    NumericField,
    RevisionRecord,
    SimulationResult,
    VerificationOutcome,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
)
from super_scientist.domain.primitives import sha256_hex

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
HASH = sha256_hex(b"task-12")


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


def _provenance(
    *,
    actor: ActorIdentity | None = None,
    category: VerificationLevel = VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
    deterministic_or_learned: str = "DETERMINISTIC",
    result: AssessmentOutcome = AssessmentOutcome.PASSED,
) -> AssessmentProvenance:
    return AssessmentProvenance(
        actor=actor or _actor("checker"),
        actor_version="checker-v1",
        category=category,
        deterministic_or_learned=deterministic_or_learned,
        proposer_relationship=ActorRelationship.INDEPENDENT,
        assumptions=("retained fixture inputs are complete",),
        evidence_ids=("evidence-1",),
        checks_run=("search-check",),
        limitations=("bounded deterministic fixture coverage",),
        result=result,
        meaningful_confidence=None,
        assessed_at=NOW,
        governing_policy_hash=HASH,
    )


def valid_hypothesis() -> HypothesisSpec:
    return HypothesisSpec(
        hypothesis_version_id="hypothesis-thermal-v1",
        hypothesis_id="hypothesis-thermal",
        version=1,
        statement="A bounded heater raises chamber temperature above ambient.",
        assumptions=("The chamber is closed during each bounded step.",),
        scope=("Synthetic in-memory chamber observations only.",),
        variables=("temperature", "ambient", "heater_delta"),
        predictions=("Temperature rises while bounded heating exceeds cooling.",),
        falsification_conditions=("A valid bounded run never rises above ambient.",),
        primitive_version_ids=(),
        evidence_ids=("evidence-1",),
        imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
        proposer=_actor("hypothesis-author", ActorKind.MODEL),
        created_at=NOW,
        governing_policy_hash=HASH,
    )


def valid_model_spec_payload() -> dict[str, object]:
    return {
        "model_spec_id": "thermal-model-v1",
        "hypothesis_version_id": "hypothesis-thermal-v1",
        "model_type": ModelType.DETERMINISTIC_SIMULATOR,
        "execution_mode": ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
        "artifact_hash": None,
        "artifact_media_type": None,
        "artifact_size_bytes": None,
        "artifact_name": "source-controlled thermal chamber simulator",
        "builtin_simulator_id": "thermal-chamber-v1",
        "input_schema_id": "thermal-chamber-input-v1",
        "output_schema_id": "thermal-chamber-output-v1",
        "deterministic_seed": 7,
        "max_steps": 10,
        "max_state_bytes": 4_096,
        "registered_by": _actor("model-registrar"),
        "created_at": NOW,
        "governing_policy_hash": HASH,
    }


def valid_checker_spec() -> DeterministicCheckerSpec:
    return DeterministicCheckerSpec(
        mechanism_type="DETERMINISTIC_CHECKER",
        mechanism_spec_id="thermal-checker-v1",
        hypothesis_version_id="hypothesis-thermal-v1",
        name="bounded thermal invariant checker",
        description="Checks registered predictions and searches bounded inputs.",
        specification_hash=HASH,
        input_schema_id="thermal-chamber-output-v1",
        output_schema_id="verification-result-v1",
        created_by=_actor("checker"),
        created_at=NOW,
        governing_policy_hash=HASH,
        checked_invariants=("bounded-temperature", "counterexample-search"),
    )


def valid_learned_judge_result_payload() -> dict[str, object]:
    return {
        "mechanism_type": "LEARNED_JUDGE",
        "verification_result_id": "learned-result-1",
        "hypothesis_version_id": "hypothesis-thermal-v1",
        "mechanism_spec_id": "learned-mechanism-1",
        "model_spec_id": None,
        "simulation_result_ids": (),
        "outcome": VerificationOutcome.ABSTAIN,
        "findings": ("Rubric evidence is insufficient for a deterministic conclusion.",),
        "provenance": _provenance(
            actor=_actor("learned-judge", ActorKind.MODEL),
            category=VerificationLevel.INDEPENDENT_LEARNED_JUDGE,
            deterministic_or_learned="LEARNED",
            result=AssessmentOutcome.ABSTAINED,
        ),
        "counterexample_search_performed": False,
        "counterexample_found": False,
        "rubric_id": "hypothesis-rubric-v1",
    }


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "import_path",
        "entry_point",
        "source_text",
        "module",
        "argv",
        "shell_command",
        "network_url",
        "callable",
    ],
)
def test_model_spec_rejects_execution_authority(forbidden_field: str) -> None:
    payload = valid_model_spec_payload()
    payload[forbidden_field] = "untrusted"

    with pytest.raises(ValidationError):
        ExecutableModelSpec.model_validate(payload)


def test_metadata_only_model_is_complete_inert_artifact_metadata() -> None:
    payload = valid_model_spec_payload()
    payload.update(
        execution_mode=ExecutionMode.METADATA_ONLY,
        builtin_simulator_id=None,
        artifact_hash=HASH,
        artifact_media_type="application/json",
        artifact_size_bytes=128,
        artifact_name="untrusted-model.json",
    )

    model = ExecutableModelSpec.model_validate(payload)
    assert model.execution_mode is ExecutionMode.METADATA_ONLY

    missing_hash = dict(payload)
    missing_hash["artifact_hash"] = None
    with pytest.raises(ValidationError):
        ExecutableModelSpec.model_validate(missing_hash)

    mixed = dict(payload)
    mixed["builtin_simulator_id"] = "thermal-chamber-v1"
    with pytest.raises(ValidationError):
        ExecutableModelSpec.model_validate(mixed)


def test_hypothesis_and_numeric_records_are_strict_frozen_and_deeply_immutable() -> None:
    hypothesis = valid_hypothesis()
    model_input = ModelInput(
        model_input_id="input-1",
        schema_id="thermal-chamber-input-v1",
        values=(NumericField(name="steps", value=2),),
        deterministic_seed=7,
    )

    with pytest.raises(ValidationError):
        hypothesis.statement = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        model_input.values[0].value = 3  # type: ignore[misc]
    with pytest.raises(ValidationError):
        NumericField(name="steps", value=True)
    with pytest.raises(ValidationError):
        NumericField(name="temperature", value=float("nan"))
    with pytest.raises(ValidationError):
        ModelInput(
            model_input_id="input-duplicate",
            schema_id="thermal-chamber-input-v1",
            values=(
                NumericField(name="steps", value=1),
                NumericField(name="steps", value=2),
            ),
            deterministic_seed=7,
        )


def test_verification_unions_are_precise_and_preserve_assessment_provenance() -> None:
    mechanism = VERIFICATION_MECHANISM_ADAPTER.validate_python(
        valid_checker_spec().model_dump(mode="python")
    )
    result = DeterministicCheckResult(
        mechanism_type="DETERMINISTIC_CHECKER",
        verification_result_id="deterministic-result-1",
        hypothesis_version_id="hypothesis-thermal-v1",
        mechanism_spec_id=mechanism.mechanism_spec_id,
        model_spec_id="thermal-model-v1",
        simulation_result_ids=("simulation-1",),
        outcome=VerificationOutcome.PASS,
        findings=("No bounded counterexample was found.",),
        provenance=_provenance(),
        counterexample_search_performed=True,
        counterexample_found=False,
        checked_invariants=("bounded-temperature",),
    )

    parsed = VERIFICATION_RESULT_ADAPTER.validate_python(result.model_dump(mode="python"))
    assert parsed == result
    assert parsed.provenance.evidence_ids == ("evidence-1",)


def test_learned_judge_cannot_claim_formal_verifier_category() -> None:
    payload = valid_learned_judge_result_payload()
    payload["mechanism_type"] = "FORMAL_VERIFIER"

    with pytest.raises(ValidationError):
        VERIFICATION_RESULT_ADAPTER.validate_python(payload)


def test_formal_result_requires_formal_deterministic_provenance_and_proof_hash() -> None:
    result = FormalVerificationResult(
        mechanism_type="FORMAL_VERIFIER",
        verification_result_id="formal-result-1",
        hypothesis_version_id="hypothesis-thermal-v1",
        mechanism_spec_id="formal-mechanism-1",
        model_spec_id=None,
        simulation_result_ids=(),
        outcome=VerificationOutcome.PASS,
        findings=("The bounded equation satisfies the retained invariant.",),
        provenance=_provenance(category=VerificationLevel.FORMAL_VERIFIER),
        counterexample_search_performed=False,
        counterexample_found=False,
        proof_artifact_hash=HASH,
    )
    assert isinstance(VERIFICATION_RESULT_ADAPTER.validate_python(result), FormalVerificationResult)

    payload = result.model_dump(mode="python")
    payload["provenance"] = _provenance(
        actor=_actor("learned", ActorKind.MODEL),
        category=VerificationLevel.INDEPENDENT_LEARNED_JUDGE,
        deterministic_or_learned="LEARNED",
    )
    with pytest.raises(ValidationError):
        FormalVerificationResult.model_validate(payload)


def test_revision_is_contiguous_and_explicitly_changes_predictions_and_falsification() -> None:
    prior = valid_hypothesis()
    resulting = prior.model_copy(
        update={
            "hypothesis_version_id": "hypothesis-thermal-v2",
            "version": 2,
            "predictions": ("Heating exceeds cooling only below a calibrated threshold.",),
            "falsification_conditions": (
                "A bounded run below the threshold fails to rise above ambient.",
            ),
        }
    )
    revision = RevisionRecord(
        revision_id="revision-thermal-v2",
        hypothesis_id=prior.hypothesis_id,
        prior_hypothesis_version_id=prior.hypothesis_version_id,
        prior_version=prior.version,
        resulting_hypothesis_version_id=resulting.hypothesis_version_id,
        resulting_version=resulting.version,
        triggering_verification_result_ids=("failed-result-1",),
        considered_counterexample_ids=("counterexample-1",),
        assumptions_added=("Cooling is calibrated before each run.",),
        assumptions_removed=(),
        assumptions_changed=(),
        variables_added=("calibrated_threshold",),
        variables_removed=(),
        variables_changed=(),
        mechanism_changes=("Added threshold-dependent cooling.",),
        preserved_elements=("Bounded in-memory state transition.",),
        changed_predictions=resulting.predictions,
        changed_falsification_conditions=resulting.falsification_conditions,
        author=_actor("revision-author"),
        revised_at=NOW,
        governing_policy_hash=HASH,
    )
    assert revision.resulting_version == revision.prior_version + 1

    payload = revision.model_dump(mode="python")
    payload["changed_predictions"] = ()
    with pytest.raises(ValidationError):
        RevisionRecord.model_validate(payload)
    payload = revision.model_dump(mode="python")
    payload["resulting_version"] = 3
    with pytest.raises(ValidationError):
        RevisionRecord.model_validate(payload)


def test_counterexample_means_a_found_deterministic_counterexample() -> None:
    record = CounterexampleRecord(
        counterexample_id="counterexample-1",
        hypothesis_version_id="hypothesis-thermal-v1",
        model_spec_id="thermal-model-v1",
        simulation_result_ids=("simulation-1",),
        verification_result_ids=("failed-result-1",),
        evidence_ids=("evidence-1",),
        description="A bounded valid input contradicted the registered prediction.",
        input_hash=HASH,
        observed_output_hash=sha256_hex(b"observed"),
        expected_output_hash=sha256_hex(b"expected"),
        discovered_by=_actor("counterexample-searcher"),
        discovered_at=NOW,
        governing_policy_hash=HASH,
    )
    assert record.verification_result_ids == ("failed-result-1",)


def test_admission_keeps_authority_and_metrics_separate_from_confidence() -> None:
    decision = HypothesisAdmissionDecision(
        admission_decision_id="admission-thermal-v2",
        hypothesis_version_id="hypothesis-thermal-v2",
        hypothesis_id="hypothesis-thermal",
        version=2,
        imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
        model_spec_ids=("thermal-model-v2",),
        verification_result_ids=("passed-result-v2", "search-result-v2"),
        counterexample_search_result_ids=("search-result-v2",),
        counterexample_ids=("counterexample-v1",),
        revision_ids=("revision-thermal-v2",),
        evaluator_audit_id="hypothesis-evaluator-audit",
        measurement_id="hypothesis-measurement",
        rollback_hypothesis_version_id="hypothesis-thermal-v1",
        outcome=AdmissionOutcome.ACCEPT,
        rationale="All retained deterministic and independent admission gates passed.",
        decided_by=_actor("hypothesis-integrator"),
        decided_at=NOW,
        governing_policy_hash=HASH,
    )
    assert not hasattr(decision, "confidence")
    assert decision.counterexample_search_result_ids == ("search-result-v2",)


def test_simulation_result_binds_exact_input_output_and_bounded_counts() -> None:
    model_input = ModelInput(
        model_input_id="thermal-input-1",
        schema_id="thermal-chamber-input-v1",
        values=(NumericField(name="steps", value=2),),
        deterministic_seed=7,
    )
    model_output = ModelOutput(
        model_output_id="thermal-output-1",
        schema_id="thermal-chamber-output-v1",
        values=(NumericField(name="final_temperature", value=24.0),),
        steps=2,
        state_bytes=128,
    )
    result = SimulationResult(
        simulation_result_id="simulation-1",
        hypothesis_version_id="hypothesis-thermal-v1",
        model_spec_id="thermal-model-v1",
        execution_mode=ExecutionMode.BUILTIN_DETERMINISTIC_SIMULATOR,
        model_input=model_input,
        model_output=model_output,
        deterministic_seed=7,
        completed_at=NOW,
        governing_policy_hash=HASH,
    )
    assert result.deterministic_seed == result.model_input.deterministic_seed

    payload = result.model_dump(mode="python")
    payload["deterministic_seed"] = 8
    with pytest.raises(ValidationError):
        SimulationResult.model_validate(payload)


def test_learned_result_remains_a_learned_judge_record() -> None:
    parsed = VERIFICATION_RESULT_ADAPTER.validate_python(valid_learned_judge_result_payload())
    assert isinstance(parsed, LearnedJudgeResult)


def test_numeric_records_support_exact_lookup_and_reject_duplicate_output_names() -> None:
    model_input = ModelInput(
        model_input_id="lookup-input",
        schema_id="lookup-input-v1",
        values=(NumericField(name="temperature", value=21.5),),
        deterministic_seed=3,
    )
    model_output = ModelOutput(
        model_output_id="lookup-output",
        schema_id="lookup-output-v1",
        values=(NumericField(name="prediction", value=22.0),),
        steps=1,
        state_bytes=16,
    )

    assert model_input.numeric_value("temperature") == 21.5
    assert model_output.numeric_value("prediction") == 22.0
    with pytest.raises(KeyError, match="missing"):
        model_input.numeric_value("missing")
    with pytest.raises(KeyError, match="missing"):
        model_output.numeric_value("missing")
    with pytest.raises(ValidationError, match="numeric field names must be unique"):
        ModelOutput(
            model_output_id="duplicate-output",
            schema_id="lookup-output-v1",
            values=(
                NumericField(name="prediction", value=22.0),
                NumericField(name="prediction", value=23.0),
            ),
            steps=1,
            state_bytes=16,
        )


def test_model_and_hypothesis_identifiers_fail_closed_on_ambiguous_shapes() -> None:
    builtin_with_artifact = valid_model_spec_payload()
    builtin_with_artifact["artifact_hash"] = HASH
    builtin_with_artifact["artifact_media_type"] = "application/octet-stream"
    builtin_with_artifact["artifact_size_bytes"] = 1
    with pytest.raises(ValidationError, match="builtin simulator cannot carry artifact"):
        ExecutableModelSpec.model_validate(builtin_with_artifact)

    missing_builtin = valid_model_spec_payload()
    missing_builtin["builtin_simulator_id"] = None
    with pytest.raises(ValidationError, match="requires a simulator identifier"):
        ExecutableModelSpec.model_validate(missing_builtin)

    hypothesis_payload = valid_hypothesis().model_dump(mode="python")
    hypothesis_payload["evidence_ids"] = ("evidence-1", "evidence-1")
    with pytest.raises(ValidationError, match="evidence_ids must be unique"):
        HypothesisSpec.model_validate(hypothesis_payload)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"simulation_result_ids": ("simulation-1", "simulation-1")},
            "simulation_result_ids must be unique",
        ),
        (
            {"counterexample_search_performed": False, "counterexample_found": True},
            "found counterexample requires retained search evidence",
        ),
        (
            {
                "outcome": VerificationOutcome.PASS,
                "counterexample_search_performed": True,
                "counterexample_found": True,
            },
            "found counterexample must fail",
        ),
        (
            {
                "outcome": VerificationOutcome.FAIL,
                "provenance": _provenance(result=AssessmentOutcome.PASSED),
            },
            "outcome must match assessment provenance",
        ),
        (
            {
                "provenance": _provenance(
                    category=VerificationLevel.INDEPENDENT_LEARNED_JUDGE,
                    deterministic_or_learned="LEARNED",
                )
            },
            "deterministic checks require independent deterministic provenance",
        ),
    ],
)
def test_deterministic_result_rejects_ambiguous_search_and_provenance(
    change: dict[str, object],
    message: str,
) -> None:
    payload = _valid_deterministic_result().model_dump(mode="python")
    payload.update(change)

    with pytest.raises(ValidationError, match=message):
        DeterministicCheckResult.model_validate(payload)


def test_learned_result_rejects_deterministic_provenance() -> None:
    payload = valid_learned_judge_result_payload()
    payload["provenance"] = _provenance(result=AssessmentOutcome.ABSTAINED)

    with pytest.raises(ValidationError, match="learned results require learned-judge provenance"):
        LearnedJudgeResult.model_validate(payload)


def test_counterexample_revision_and_admission_identifiers_are_unambiguous() -> None:
    counterexample = CounterexampleRecord(
        counterexample_id="counterexample-unique",
        hypothesis_version_id="hypothesis-thermal-v1",
        model_spec_id=None,
        simulation_result_ids=(),
        verification_result_ids=("failed-result-1",),
        evidence_ids=("evidence-1",),
        description="A retained search found a contradictory observation.",
        input_hash=HASH,
        observed_output_hash=sha256_hex(b"observed-unique"),
        expected_output_hash=sha256_hex(b"expected-unique"),
        discovered_by=_actor("counterexample-author"),
        discovered_at=NOW,
        governing_policy_hash=HASH,
    )
    duplicate = counterexample.model_dump(mode="python")
    duplicate["verification_result_ids"] = ("failed-result-1", "failed-result-1")
    with pytest.raises(ValidationError, match="verification_result_ids must be unique"):
        CounterexampleRecord.model_validate(duplicate)

    prior = valid_hypothesis()
    revision = RevisionRecord(
        revision_id="revision-distinct",
        hypothesis_id=prior.hypothesis_id,
        prior_hypothesis_version_id=prior.hypothesis_version_id,
        prior_version=1,
        resulting_hypothesis_version_id="hypothesis-thermal-v2",
        resulting_version=2,
        triggering_verification_result_ids=("failed-result-1",),
        considered_counterexample_ids=(counterexample.counterexample_id,),
        assumptions_added=(),
        assumptions_removed=(),
        assumptions_changed=(),
        variables_added=(),
        variables_removed=(),
        variables_changed=(),
        mechanism_changes=("Retained a stricter checker.",),
        preserved_elements=("Bounded state transition.",),
        changed_predictions=("The revised bounded prediction holds.",),
        changed_falsification_conditions=("A valid contrary run falsifies the revision.",),
        author=_actor("revision-author-distinct"),
        revised_at=NOW,
        governing_policy_hash=HASH,
    )
    same_version = revision.model_dump(mode="python")
    same_version["resulting_hypothesis_version_id"] = prior.hypothesis_version_id
    with pytest.raises(ValidationError, match="resulting hypothesis version must be distinct"):
        RevisionRecord.model_validate(same_version)

    admission = HypothesisAdmissionDecision(
        admission_decision_id="admission-subset",
        hypothesis_version_id="hypothesis-thermal-v1",
        hypothesis_id="hypothesis-thermal",
        version=1,
        imported_pattern_status=ImportedPatternStatus.TRANSFER_VALIDATED,
        model_spec_ids=("thermal-model-v1",),
        verification_result_ids=("passed-result",),
        counterexample_search_result_ids=("passed-result",),
        counterexample_ids=(),
        revision_ids=(),
        evaluator_audit_id="audit-subset",
        measurement_id="measurement-subset",
        rollback_hypothesis_version_id=None,
        outcome=AdmissionOutcome.ACCEPT,
        rationale="The invalid search reference must not be accepted.",
        decided_by=_actor("integrator-subset"),
        decided_at=NOW,
        governing_policy_hash=HASH,
    ).model_dump(mode="python")
    admission["counterexample_search_result_ids"] = ("unretained-search",)
    with pytest.raises(ValidationError, match="counterexample search results"):
        HypothesisAdmissionDecision.model_validate(admission)


def _valid_deterministic_result() -> DeterministicCheckResult:
    return DeterministicCheckResult(
        mechanism_type="DETERMINISTIC_CHECKER",
        verification_result_id="deterministic-search-result",
        hypothesis_version_id="hypothesis-thermal-v1",
        mechanism_spec_id="thermal-checker-v1",
        model_spec_id="thermal-model-v1",
        simulation_result_ids=("simulation-1",),
        outcome=VerificationOutcome.PASS,
        findings=("No bounded counterexample was found.",),
        provenance=_provenance(),
        counterexample_search_performed=True,
        counterexample_found=False,
        checked_invariants=("bounded-temperature",),
    )
