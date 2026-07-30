from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from super_scientist.application import harness_eval as harness_eval_facade
from super_scientist.application.harness_eval.capabilities import (
    CandidateExecutionContext,
    InMemoryPublicTaskInputReader,
    OutputOnlyEvaluatorExecutor,
    create_candidate_execution_context,
    walk_object_graph_types,
)
from super_scientist.domain.harness_eval.models import (
    EvaluationBudget,
    FeedbackMode,
    FixedCheckerConfiguration,
    FixedCheckerKind,
    HarnessPartition,
    PublicTaskInput,
    fixed_checker_configuration_hash,
)
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import sha256_hex
from super_scientist.providers.storage.protected_evaluation import (
    ProtectedAnswerReader,
    ProtectedCheckerResult,
    ProtectedEvaluationStore,
    ProtectedResultGateway,
    ProtectedResultValidator,
)

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
SECRET = b"literal-held-out-answer-task-15"


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Reader:
    def read_expected_output(self, task_id: str) -> bytes:
        assert task_id == "task-1"
        return SECRET

    def close(self) -> None:
        pass


class _Validator:
    def validate_result(self, result: ProtectedCheckerResult) -> ProtectedCheckerResult:
        return ProtectedCheckerResult.model_validate(result)

    def close(self) -> None:
        pass


def test_candidate_graph_contains_only_public_input_and_immutable_budget_authority() -> None:
    context = _candidate_context()

    graph_types = walk_object_graph_types(context)

    assert ProtectedAnswerReader not in graph_types
    assert ProtectedEvaluationStore not in graph_types
    assert ProtectedResultValidator not in graph_types
    assert ProtectedResultGateway not in graph_types
    assert OutputOnlyEvaluatorExecutor not in graph_types
    assert context.budget.model_config.get("frozen") is True


def test_public_reader_exposes_input_only_for_the_bound_campaign_and_task() -> None:
    context = _candidate_context()

    public = context.input_reader.get_task_input("campaign-v1", "task-1")

    assert public.payload == b"public-question"
    assert SECRET not in public.model_dump_json().encode()
    with pytest.raises(KeyError, match="public task input is unavailable"):
        context.input_reader.get_task_input("campaign-v1", "protected-task")


def test_output_only_evaluator_never_invokes_candidate_objects() -> None:
    invoked = False

    class CandidateCode:
        def __call__(self) -> bytes:
            nonlocal invoked
            invoked = True
            return SECRET

        def __bytes__(self) -> bytes:
            nonlocal invoked
            invoked = True
            return SECRET

    evaluator = _evaluator()
    with pytest.raises(TypeError, match="candidate output must be exact bytes") as captured:
        evaluator.evaluate("campaign-v1", "task-1", CandidateCode(), _checker())  # type: ignore[arg-type]

    assert invoked is False
    assert SECRET.decode() not in str(captured.value)


def test_evaluator_returns_only_hashes_aggregates_and_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    evaluator = _evaluator()

    result = evaluator.evaluate("campaign-v1", "task-1", SECRET, _checker())

    assert result.outcome is AssessmentOutcome.PASSED
    assert result.expected_output_hash == sha256_hex(SECRET)
    assert result.candidate_output_hash == sha256_hex(SECRET)
    serialized = result.model_dump_json().encode()
    assert SECRET not in serialized
    assert SECRET not in caplog.text.encode()
    assert not (
        {"expected_output", "answer_bytes", "answer_reference"}
        & set(ProtectedCheckerResult.model_fields)
    )


def test_wrong_candidate_output_cannot_enter_errors_logs_or_indirect_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    candidate = b"candidate-output-that-must-not-be-logged"

    result = _evaluator().evaluate("campaign-v1", "task-1", candidate, _checker())

    assert result.outcome is AssessmentOutcome.FAILED
    assert candidate not in result.model_dump_json().encode()
    assert candidate not in caplog.text.encode()
    with pytest.raises(ValidationError):
        ProtectedCheckerResult.model_validate(
            result.model_dump(mode="python") | {"reversible_answer_reference": "artifact://secret"}
        )


def test_checker_configuration_cannot_carry_code_imports_or_dynamic_entry_points() -> None:
    payload = _checker().model_dump(mode="python")
    for field, value in (
        ("callable", lambda: None),
        ("module", "secret_checker"),
        ("entry_point", "secret_checker:run"),
        ("command", "python checker.py"),
        ("expected_output", SECRET),
        ("answer_reference", "protected://task-1"),
    ):
        with pytest.raises(ValidationError):
            FixedCheckerConfiguration.model_validate(payload | {field: value})


def test_non_validating_checker_copies_are_revalidated_before_protected_reads() -> None:
    reads = 0

    class CountingReader(_Reader):
        def read_expected_output(self, task_id: str) -> bytes:
            nonlocal reads
            reads += 1
            return super().read_expected_output(task_id)

    malformed = _checker().model_copy(update={"checker_kind": "DYNAMIC_PYTHON"})
    evaluator = OutputOnlyEvaluatorExecutor(CountingReader(), _Validator(), _Clock())

    with pytest.raises(ValueError, match="fixed checker configuration is invalid"):
        evaluator.evaluate("campaign-v1", "task-1", b"candidate", malformed)
    assert reads == 0


def test_candidate_context_rejects_protected_and_evaluator_capabilities_directly() -> None:
    payload = {
        "input_reader": _Reader(),
        "budget": _budget(),
    }
    with pytest.raises((TypeError, ValidationError)):
        CandidateExecutionContext(**payload)  # type: ignore[arg-type]


def test_public_reader_rejects_duplicate_authority_bindings() -> None:
    public = _candidate_context().input_reader.get_task_input("campaign-v1", "task-1")

    with pytest.raises(ValueError, match="unique campaign/task"):
        InMemoryPublicTaskInputReader((public, public))


def test_public_and_sealed_readers_fail_closed_on_missing_or_ambiguous_inputs() -> None:
    public = _public_input()
    reader = InMemoryPublicTaskInputReader((public,))

    assert reader.get_task_input(public.campaign_id, public.task_id) == public
    with pytest.raises(KeyError, match="unavailable"):
        reader.get_task_input(public.campaign_id, "missing-task")
    with pytest.raises(ValueError, match="unique campaign/task"):
        create_candidate_execution_context((public, public), _budget())
    with pytest.raises(TypeError, match="exact public task inputs"):
        create_candidate_execution_context([public], _budget())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="exact immutable evaluation budget"):
        CandidateExecutionContext(
            input_reader=_candidate_context().input_reader,
            budget=object(),  # type: ignore[arg-type]
        )


def test_candidate_context_revalidates_nonvalidating_budget_copies() -> None:
    invalid_budget = _budget().model_copy(update={"attempts": 0})

    with pytest.raises(ValueError, match="evaluation budget is invalid"):
        CandidateExecutionContext(
            input_reader=_candidate_context().input_reader,
            budget=invalid_budget,
        )


def test_output_only_evaluator_rejects_wrong_capabilities_and_safe_input_types() -> None:
    with pytest.raises(TypeError, match="answer-reader"):
        OutputOnlyEvaluatorExecutor(object(), _Validator(), _Clock())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="result-validator"):
        OutputOnlyEvaluatorExecutor(_Reader(), object(), _Clock())  # type: ignore[arg-type]

    class NonBytesReader(_Reader):
        def read_expected_output(self, task_id: str) -> bytes:
            del task_id
            return "not-bytes"  # type: ignore[return-value]

    evaluator = OutputOnlyEvaluatorExecutor(NonBytesReader(), _Validator(), _Clock())
    with pytest.raises(ValueError, match="input is unavailable"):
        evaluator.evaluate("campaign-v1", "task-1", b"candidate", _checker())
    with pytest.raises(ValueError, match="fixed checker configuration is invalid"):
        _evaluator().evaluate("", "task-1", b"candidate", _checker())
    with pytest.raises(ValueError, match="fixed checker configuration is invalid"):
        _evaluator().evaluate("campaign-v1", "task-1", b"candidate", object())  # type: ignore[arg-type]


def test_object_graph_walker_handles_cycles_containers_and_unset_slots() -> None:
    class PartialSlots:
        __slots__ = ("child", "unset")

        def __init__(self) -> None:
            self.child: object = self

    root = {"items": [PartialSlots(), {"numbers": {1, 2}}]}

    graph_types = walk_object_graph_types(root)

    assert dict in graph_types
    assert list in graph_types
    assert PartialSlots in graph_types
    assert int in graph_types


def test_object_graph_walker_inspects_bound_methods_defaults_and_closures() -> None:
    reader = _Reader()
    captured = reader

    def closure(value: object = reader, *, keyword: object = reader) -> object:
        del value, keyword
        return captured

    graph_types = walk_object_graph_types((reader.read_expected_output, closure))

    assert _Reader in graph_types
    assert type(closure) in graph_types


def test_sealed_context_rejects_reversible_reference_bytes() -> None:
    public = _public_input().model_copy(
        update={
            "payload": b"artifact://protected-answer",
            "payload_hash": sha256_hex(b"artifact://protected-answer"),
        }
    )

    with pytest.raises(ValueError, match="reversible protected reference"):
        create_candidate_execution_context((public,), _budget())


def test_candidate_context_rejects_raw_protected_content_field() -> None:
    context = _candidate_context()
    object.__setattr__(
        context.input_reader,
        "_inputs",
        {"protected_answer": SECRET},
    )

    with pytest.raises(ValueError, match="protected content"):
        CandidateExecutionContext(
            input_reader=context.input_reader,
            budget=_budget(),
        )


def test_candidate_context_allows_field_name_text_as_an_ordinary_value() -> None:
    context = _candidate_context()
    object.__setattr__(
        context.input_reader,
        "_inputs",
        {"label": "protected_answer"},
    )

    CandidateExecutionContext(
        input_reader=context.input_reader,
        budget=_budget(),
    )


def test_candidate_context_factory_seals_public_input_authority() -> None:
    public = _public_input()

    context = create_candidate_execution_context((public,), _budget())

    assert context.input_reader.get_task_input("campaign-v1", "task-1") == public
    assert not hasattr(context.input_reader, "read_expected_output")
    assert context.input_reader.__class__.__name__ != InMemoryPublicTaskInputReader.__name__


def test_package_facade_constructs_only_the_sealed_candidate_context() -> None:
    context = harness_eval_facade.create_candidate_execution_context(
        (_public_input(),),
        _budget(),
    )

    assert context.input_reader.get_task_input("campaign-v1", "task-1") == _public_input()
    with pytest.raises((TypeError, ValidationError)):
        CandidateExecutionContext(
            input_reader=InMemoryPublicTaskInputReader((_public_input(),)),  # type: ignore[arg-type]
            budget=_budget(),
        )


def test_candidate_context_rejects_dual_role_and_recursively_nested_authority(
    tmp_path: Path,
) -> None:
    class DualRole(_Reader):
        def get_task_input(self, campaign_id: str, task_id: str) -> PublicTaskInput:
            del campaign_id, task_id
            return _public_input()

    class DictionaryWrapper:
        def __init__(self, authority: object) -> None:
            self.nested = {"authority": authority}

        def get_task_input(self, campaign_id: str, task_id: str) -> PublicTaskInput:
            del campaign_id, task_id
            return _public_input()

    class SlotWrapper:
        __slots__ = ("authority",)

        def __init__(self, authority: object) -> None:
            self.authority = authority

        def get_task_input(self, campaign_id: str, task_id: str) -> PublicTaskInput:
            del campaign_id, task_id
            return _public_input()

    class Gateway:
        def append_result(self, result: ProtectedCheckerResult) -> None:
            del result

        def close(self) -> None:
            return None

    reader = _Reader()
    store = ProtectedEvaluationStore(tmp_path / "protected")
    try:
        closure = lambda: reader.read_expected_output("task-1")  # noqa: E731
        protected_authorities = (reader, store, _evaluator(), _Validator(), Gateway())
        forbidden = (
            DualRole(),
            *(DictionaryWrapper(item) for item in protected_authorities),
            *(SlotWrapper(item) for item in protected_authorities),
            closure,
            reader.read_expected_output,
            *protected_authorities,
            {"nested": {"answer_reference": "protected://task-1"}},
            {"nested": "artifact://reversible-answer"},
        )
        for authority in forbidden:
            with pytest.raises((TypeError, ValueError), match=r"candidate|authority|public"):
                CandidateExecutionContext(
                    input_reader=authority,  # type: ignore[arg-type]
                    budget=_budget(),
                )
    finally:
        store.close()


def test_fixed_checker_configuration_is_content_addressed_over_all_semantic_fields() -> None:
    checker = _checker()
    with pytest.raises(ValidationError, match="configuration_hash"):
        FixedCheckerConfiguration.model_validate(
            checker.model_dump(mode="python") | {"configuration_hash": "f" * 64}
        )

    for update in (
        {"checker_id": "other-checker"},
        {"checker_version": "other-version"},
        {"metric_ids": ("correctness", "safety")},
        {"metric_higher_is_better": (False,)},
        {"evaluator_id": "other-evaluator"},
        {"evaluator_version_id": "evaluator-v2"},
    ):
        payload = checker.model_dump(mode="python") | update
        expected = fixed_checker_configuration_hash(
            checker_id=str(payload["checker_id"]),
            checker_version=str(payload["checker_version"]),
            checker_kind=payload["checker_kind"],
            metric_ids=payload["metric_ids"],
            evaluator_id=str(payload["evaluator_id"]),
            evaluator_version_id=str(payload["evaluator_version_id"]),
            metric_higher_is_better=payload["metric_higher_is_better"],
        )
        assert expected != checker.configuration_hash


def test_result_identity_cannot_collide_across_checker_or_evaluator_lineage() -> None:
    first = _evaluator().evaluate("campaign-v1", "task-1", SECRET, _checker())
    changed = _checker(evaluator_version_id="evaluator-v2")

    second = _evaluator().evaluate("campaign-v1", "task-1", SECRET, changed)

    assert first.result_id != second.result_id


def test_lower_is_better_checker_emits_directional_values_and_distinct_identity() -> None:
    higher = _evaluator().evaluate("campaign-v1", "task-1", SECRET, _checker())
    lower_checker = _checker(metric_higher_is_better=(False,))

    lower_match = _evaluator().evaluate("campaign-v1", "task-1", SECRET, lower_checker)
    lower_miss = _evaluator().evaluate(
        "campaign-v1",
        "task-1",
        b"wrong-answer",
        lower_checker,
    )

    assert lower_match.metric_values[0].value == Decimal("0")
    assert lower_miss.metric_values[0].value == Decimal("1")
    assert lower_match.result_id != higher.result_id


def _candidate_context() -> CandidateExecutionContext:
    return create_candidate_execution_context((_public_input(),), _budget())


def _public_input() -> PublicTaskInput:
    return PublicTaskInput(
        campaign_id="campaign-v1",
        campaign_version=1,
        task_id="task-1",
        partition=HarnessPartition.HARNESS_DISCOVERY_TASKS,
        payload=b"public-question",
        payload_hash=sha256_hex(b"public-question"),
    )


def _budget() -> EvaluationBudget:
    return EvaluationBudget(
        model_id="model-1",
        model_version="model-v1",
        adapter_id=None,
        feedback_mode=FeedbackMode.NONE,
        tool_ids=(),
        attempts=1,
        token_limit=100,
        reasoning_limit=50,
        evaluator_call_limit=1,
        wall_clock_seconds=Decimal("10"),
        cost_limit=Decimal("1"),
        human_intervention_limit=0,
    )


def _checker(**updates: object) -> FixedCheckerConfiguration:
    payload: dict[str, object] = {
        "checker_id": "exact-match",
        "checker_version": "exact-match-v1",
        "checker_kind": FixedCheckerKind.EXACT_BYTES,
        "metric_ids": ("correctness",),
        "metric_higher_is_better": (True,),
        "evaluator_id": "evaluator",
        "evaluator_version_id": "evaluator-v1",
    }
    payload.update(updates)
    payload["configuration_hash"] = fixed_checker_configuration_hash(
        checker_id=str(payload["checker_id"]),
        checker_version=str(payload["checker_version"]),
        checker_kind=cast(FixedCheckerKind, payload["checker_kind"]),
        metric_ids=cast(tuple[str, ...], payload["metric_ids"]),
        evaluator_id=str(payload["evaluator_id"]),
        evaluator_version_id=str(payload["evaluator_version_id"]),
        metric_higher_is_better=cast(
            tuple[bool, ...],
            payload["metric_higher_is_better"],
        ),
    )
    return FixedCheckerConfiguration(
        **payload,  # type: ignore[arg-type]
    )


def _evaluator() -> OutputOnlyEvaluatorExecutor:
    reader: ProtectedAnswerReader = _Reader()
    validator: ProtectedResultValidator = _Validator()
    return OutputOnlyEvaluatorExecutor(reader, validator, _Clock())
