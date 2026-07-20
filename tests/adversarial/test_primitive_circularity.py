from __future__ import annotations

from datetime import UTC, datetime

from super_scientist.application.representations.service import (
    evaluator_independence_rejection,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.primitives import sha256_hex
from super_scientist.kernel.transactions.models import RejectionCode

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _model(
    actor_id: str,
    *,
    provider_id: str,
    model_id: str,
    adapter_id: str,
    configuration: str,
) -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        kind=ActorKind.MODEL,
        provider_id=provider_id,
        model_id=model_id,
        adapter_id=adapter_id,
        configuration_hash=sha256_hex(configuration.encode()),
        created_at=NOW,
    )


def _human(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=NOW)


def test_primitive_and_its_evaluator_cannot_approve_each_other() -> None:
    primitive_author = _model(
        "primitive-author",
        provider_id="provider-a",
        model_id="model-a",
        adapter_id="adapter-a",
        configuration="configuration-a",
    )

    assert (
        evaluator_independence_rejection(
            primitive_author=primitive_author,
            evaluator=primitive_author,
            check_actors=(_human("checker"),),
            approver=_human("approver"),
        )
        is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL
    )
    assert (
        evaluator_independence_rejection(
            primitive_author=primitive_author,
            evaluator=_human("evaluator"),
            check_actors=(_human("checker"),),
            approver=primitive_author,
        )
        is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL
    )


def test_independence_rejects_shared_provider_model_adapter_or_configuration() -> None:
    primitive_author = _model(
        "primitive-author",
        provider_id="provider-a",
        model_id="model-a",
        adapter_id="adapter-a",
        configuration="configuration-a",
    )
    candidates = (
        _model(
            "shared-provider",
            provider_id="provider-a",
            model_id="model-b",
            adapter_id="adapter-b",
            configuration="configuration-b",
        ),
        _model(
            "shared-model",
            provider_id="provider-b",
            model_id="model-a",
            adapter_id="adapter-b",
            configuration="configuration-b",
        ),
        _model(
            "shared-adapter",
            provider_id="provider-b",
            model_id="model-b",
            adapter_id="adapter-a",
            configuration="configuration-b",
        ),
        _model(
            "shared-configuration",
            provider_id="provider-b",
            model_id="model-b",
            adapter_id="adapter-b",
            configuration="configuration-a",
        ),
    )

    for evaluator in candidates:
        assert (
            evaluator_independence_rejection(
                primitive_author=primitive_author,
                evaluator=evaluator,
                check_actors=(_human("checker"),),
                approver=_human("approver"),
            )
            is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL
        )


def test_evaluator_checker_and_approver_must_be_pairwise_independent() -> None:
    primitive_author = _human("primitive-author")
    evaluator = _human("evaluator")

    assert (
        evaluator_independence_rejection(
            primitive_author=primitive_author,
            evaluator=evaluator,
            check_actors=(evaluator,),
            approver=_human("approver"),
        )
        is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL
    )
    assert (
        evaluator_independence_rejection(
            primitive_author=primitive_author,
            evaluator=evaluator,
            check_actors=(_human("checker"),),
            approver=evaluator,
        )
        is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL
    )


def test_model_independence_fails_closed_without_configuration_identity() -> None:
    primitive_author = _model(
        "primitive-author",
        provider_id="provider-a",
        model_id="model-a",
        adapter_id="adapter-a",
        configuration="configuration-a",
    )
    evaluator = _model(
        "evaluator",
        provider_id="provider-b",
        model_id="model-b",
        adapter_id="adapter-b",
        configuration="configuration-b",
    ).model_copy(update={"configuration_hash": None})

    assert (
        evaluator_independence_rejection(
            primitive_author=primitive_author,
            evaluator=evaluator,
            check_actors=(_human("checker"),),
            approver=_human("approver"),
        )
        is RejectionCode.CIRCULAR_EVALUATOR_APPROVAL
    )
