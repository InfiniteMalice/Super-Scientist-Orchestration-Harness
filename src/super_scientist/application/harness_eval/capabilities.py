from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import TypeAdapter, ValidationError

from super_scientist.domain.harness_eval.models import (
    EvaluationBudget,
    FixedCheckerConfiguration,
    FixedCheckerKind,
    HarnessCampaignReport,
    HarnessDecision,
    PublicTaskInput,
)
from super_scientist.domain.improvement.models import AssessmentOutcome
from super_scientist.domain.primitives import (
    StableIdentifier,
    UtcTimestamp,
    canonical_json_bytes,
    sha256_hex,
)
from super_scientist.providers.storage.protected_evaluation import (
    MetricValue,
    ProtectedAnswerReader,
    ProtectedCheckerResult,
    ProtectedResultValidator,
)

if TYPE_CHECKING:
    from super_scientist.kernel.transactions.models import (
        CreateHarnessCampaign,
        DecideHarnessCampaign,
        RecordHarnessConfound,
        RecordHarnessIteration,
        RecordHarnessProtectedResult,
        TransactionDecision,
    )

_IDENTIFIER_ADAPTER: TypeAdapter[StableIdentifier] = TypeAdapter(StableIdentifier)


class EvaluationClock(Protocol):
    def now(self) -> UtcTimestamp: ...


@runtime_checkable
class PublicTaskInputReader(Protocol):
    def get_task_input(self, campaign_id: str, task_id: str) -> PublicTaskInput: ...


@runtime_checkable
class CampaignCoordinatorCapability(Protocol):
    def create_campaign(self, proposal: CreateHarnessCampaign) -> TransactionDecision: ...

    def record_iteration(self, proposal: RecordHarnessIteration) -> TransactionDecision: ...

    def record_protected_result(
        self,
        proposal: RecordHarnessProtectedResult,
    ) -> TransactionDecision: ...

    def record_confound(self, proposal: RecordHarnessConfound) -> TransactionDecision: ...

    def decide_campaign(self, proposal: DecideHarnessCampaign) -> TransactionDecision: ...


@runtime_checkable
class EvaluatorExecutorCapability(Protocol):
    def evaluate(
        self,
        campaign_id: str,
        task_id: str,
        candidate_output: bytes,
        checker: FixedCheckerConfiguration,
    ) -> ProtectedCheckerResult: ...


@runtime_checkable
class DecisionAuthorityCapability(Protocol):
    def authorize(self, report: HarnessCampaignReport) -> HarnessDecision: ...


class InMemoryPublicTaskInputReader:
    """A fixed public-input projection with no protected-store reference."""

    __slots__ = ("_inputs",)

    def __init__(self, inputs: tuple[PublicTaskInput, ...]) -> None:
        normalized = tuple(PublicTaskInput.model_validate(item) for item in inputs)
        keys = tuple((item.campaign_id, item.task_id) for item in normalized)
        if len(keys) != len(set(keys)):
            raise ValueError("public task inputs must have unique campaign/task identities")
        self._inputs: Mapping[tuple[str, str], PublicTaskInput] = MappingProxyType(
            dict(zip(keys, normalized, strict=True))
        )

    def get_task_input(self, campaign_id: str, task_id: str) -> PublicTaskInput:
        campaign = _IDENTIFIER_ADAPTER.validate_python(campaign_id)
        task = _IDENTIFIER_ADAPTER.validate_python(task_id)
        try:
            return self._inputs[(campaign, task)]
        except KeyError:
            raise KeyError("public task input is unavailable") from None


@dataclass(frozen=True, slots=True)
class CandidateExecutionContext:
    """The complete authority offered to candidate code."""

    input_reader: PublicTaskInputReader
    budget: EvaluationBudget

    def __post_init__(self) -> None:
        if not isinstance(self.input_reader, PublicTaskInputReader):
            raise TypeError("candidate context requires a public task input reader")
        if type(self.budget) is not EvaluationBudget:
            raise TypeError("candidate context requires an exact immutable evaluation budget")
        try:
            canonical = EvaluationBudget.model_validate(self.budget)
        except (TypeError, ValidationError):
            raise ValueError("candidate evaluation budget is invalid") from None
        object.__setattr__(self, "budget", canonical)


class OutputOnlyEvaluatorExecutor:
    """Evaluate already-produced bytes; this object has no candidate invocation API."""

    __slots__ = ("_answer_reader", "_clock", "_result_validator")

    def __init__(
        self,
        answer_reader: ProtectedAnswerReader,
        result_validator: ProtectedResultValidator,
        clock: EvaluationClock,
    ) -> None:
        if not isinstance(answer_reader, ProtectedAnswerReader):
            raise TypeError("evaluator requires a protected answer-reader capability")
        if not isinstance(result_validator, ProtectedResultValidator):
            raise TypeError("evaluator requires a protected result-validator capability")
        self._answer_reader = answer_reader
        self._result_validator = result_validator
        self._clock = clock

    def evaluate(
        self,
        campaign_id: str,
        task_id: str,
        candidate_output: bytes,
        checker: FixedCheckerConfiguration,
    ) -> ProtectedCheckerResult:
        if type(candidate_output) is not bytes:
            raise TypeError("candidate output must be exact bytes") from None
        if type(checker) is not FixedCheckerConfiguration:
            raise ValueError("fixed checker configuration is invalid") from None
        try:
            fixed_checker = FixedCheckerConfiguration.model_validate(checker)
            campaign = _IDENTIFIER_ADAPTER.validate_python(campaign_id)
            task = _IDENTIFIER_ADAPTER.validate_python(task_id)
        except (TypeError, ValidationError, ValueError):
            raise ValueError("fixed checker configuration is invalid") from None
        if fixed_checker.checker_kind is not FixedCheckerKind.EXACT_BYTES:
            raise ValueError("fixed checker configuration is invalid") from None
        expected_output = self._answer_reader.read_expected_output(task)
        if type(expected_output) is not bytes:
            raise ValueError("protected evaluator input is unavailable") from None
        expected_hash = sha256_hex(expected_output)
        candidate_hash = sha256_hex(candidate_output)
        matched = hmac.compare_digest(expected_output, candidate_output)
        evaluated_at = self._clock.now()
        result_identity_hash = sha256_hex(
            canonical_json_bytes(
                {
                    "campaign_id": campaign,
                    "task_id": task,
                    "candidate_output_hash": candidate_hash,
                    "expected_output_hash": expected_hash,
                    "checker_id": fixed_checker.checker_id,
                    "checker_version": fixed_checker.checker_version,
                    "evaluated_at": evaluated_at.isoformat(),
                }
            )
        )
        result = ProtectedCheckerResult(
            result_id=f"protected-result-{result_identity_hash}",
            campaign_id=campaign,
            task_id=task,
            expected_output_hash=expected_hash,
            candidate_output_hash=candidate_hash,
            checker_id=fixed_checker.checker_id,
            checker_version=fixed_checker.checker_version,
            outcome=AssessmentOutcome.PASSED if matched else AssessmentOutcome.FAILED,
            metric_values=tuple(
                MetricValue(metric_id=metric_id, value=Decimal(1 if matched else 0))
                for metric_id in fixed_checker.metric_ids
            ),
            evaluated_at=evaluated_at,
        )
        return self._result_validator.validate_result(result)


def walk_object_graph_types(root: object) -> frozenset[type[object]]:
    """Inspect concrete owned values without invoking arbitrary properties."""

    seen: set[int] = set()
    graph_types: set[type[object]] = set()
    pending = [root]
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        graph_types.add(type(current))
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (tuple, list, set, frozenset)):
            pending.extend(current)
        elif is_dataclass(current) and not isinstance(current, type):
            pending.extend(getattr(current, field.name) for field in fields(current))
        else:
            slots = getattr(type(current), "__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            for slot in slots:
                if isinstance(slot, str) and slot not in {"__weakref__", "__dict__"}:
                    try:
                        pending.append(object.__getattribute__(current, slot))
                    except AttributeError:
                        continue
    return frozenset(graph_types)


__all__ = [
    "CampaignCoordinatorCapability",
    "CandidateExecutionContext",
    "DecisionAuthorityCapability",
    "EvaluatorExecutorCapability",
    "InMemoryPublicTaskInputReader",
    "OutputOnlyEvaluatorExecutor",
    "PublicTaskInputReader",
    "walk_object_graph_types",
]
