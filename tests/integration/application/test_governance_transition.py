from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, update

from super_scientist.application.transactions.coordinator import TransactionCoordinator
from super_scientist.application.transactions.governance import (
    ProposeGovernancePolicyTransitionHandler,
    _GovernanceTransitionContext,
)
from super_scientist.application.workspace_integrity import verify_workspace
from super_scientist.config.loader import policy_hash
from super_scientist.config.models import (
    AdaptationRequirement,
    GovernancePolicy,
    GovernancePolicyV2,
    PolicySnapshot,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import (
    ChangeTarget,
    ExternalGrounding,
    ImprovementSignal,
    LoopClosure,
    PersistenceScope,
    VerificationLevel,
)
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    ChangeClassification,
    EvaluatorAuditRecord,
    MeasurementDecision,
    MetricObservation,
    PerformanceTrajectoryPoint,
    ResourceBudget,
    ResourceUsage,
    ResourceUsageBreakdown,
    SelfImprovementMeasurementRecord,
    TrajectoryObservation,
)
from super_scientist.domain.research_runs.models import ResearchRun, RunBudgetAllocation
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    Approval,
    ProposeGovernancePolicyTransition,
    RejectionCode,
)
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)
from super_scientist.providers.storage.domain_records import (
    EvaluatorAuditRepository,
    ResearchRunRepository,
    SelfImprovementMeasurementRepository,
)
from super_scientist.providers.storage.repositories import TransactionRepository
from super_scientist.providers.storage.schema import audit_events, governance_state

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
DECIDED_AT = datetime(2026, 7, 18, 12, 0, 1, tzinfo=UTC)


def test_v2_cannot_authorize_its_own_transition_without_independent_human(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "self-authorized").model_copy(
        update={"approval": None}
    )

    decision = runtime.coordinator.submit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    _assert_rejected_transition_is_durable_without_projection(runtime)
    runtime.engine.dispose()


def test_governance_transition_requires_passed_independent_evaluator_audit(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "failed-audit")
    failed_audit = proposal.evaluator_audit.model_copy(update={"result": AssessmentOutcome.FAILED})
    proposal = proposal.model_copy(
        update={
            "evaluator_audit": failed_audit,
            "measurement": proposal.measurement.model_copy(
                update={"evaluator_audit_id": failed_audit.evaluator_audit_id}
            ),
        }
    )

    decision = runtime.coordinator.submit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    _assert_rejected_transition_is_durable_without_projection(runtime)
    runtime.engine.dispose()


def test_governance_transition_rejects_weak_passed_evaluator_audit(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "weak-confidence-audit")
    weak_audit = proposal.evaluator_audit.model_copy(
        update={"auditor_category": VerificationLevel.MODEL_CONFIDENCE}
    )
    weak_proposal = proposal.model_copy(update={"evaluator_audit": weak_audit})
    handler = ProposeGovernancePolicyTransitionHandler()
    context = handler.build_context(
        proposal,
        _TransitionReadCapabilities(runtime.prior),
    )

    handler_decision = handler.decide(weak_proposal, context)
    decision = runtime.coordinator.submit(weak_proposal)

    assert handler_decision.accepted is False
    assert handler_decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INVALID_PROPOSAL
    _assert_rejected_transition_is_durable_without_projection(runtime)
    runtime.engine.dispose()


@pytest.mark.parametrize("overspend_scope", ("measurement", "run"))
def test_governance_transition_rejects_category_cross_subsidy_and_run_overspend(
    tmp_path: Path,
    overspend_scope: str,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, f"{overspend_scope}-overspend")
    if overspend_scope == "measurement":
        proposal = proposal.model_copy(
            update={
                "measurement": proposal.measurement.model_copy(update={"search_budget": _budget(0)})
            }
        )
    else:
        proposal = proposal.model_copy(
            update={
                "research_run": proposal.research_run.model_copy(
                    update={
                        "budget_allocation": proposal.research_run.budget_allocation.model_copy(
                            update={"search": _budget(0)}
                        )
                    }
                )
            }
        )

    decision = runtime.coordinator.submit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.UNMATCHED_BUDGETS
    _assert_rejected_transition_is_durable_without_projection(runtime)
    runtime.engine.dispose()


@pytest.mark.parametrize(
    ("minimum_verification", "permitted_grounding", "expected_code"),
    (
        (
            VerificationLevel.FORMAL_VERIFIER,
            frozenset({ExternalGrounding.CONTROLLED_EXPERIMENT}),
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
        ),
        (
            VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            frozenset({ExternalGrounding.HUMAN_JUDGMENT}),
            RejectionCode.INSUFFICIENT_GROUNDING,
        ),
    ),
)
def test_active_v2_requirement_governs_every_v2_to_v2_transition(
    tmp_path: Path,
    minimum_verification: VerificationLevel,
    permitted_grounding: frozenset[ExternalGrounding],
    expected_code: RejectionCode,
) -> None:
    runtime = _runtime(
        tmp_path,
        minimum_verification=minimum_verification,
        permitted_grounding=permitted_grounding,
    )
    assert runtime.coordinator.submit(
        _transition(runtime.prior, runtime.candidate, "strict-bootstrap")
    ).accepted
    active_v2_coordinator = TransactionCoordinator(
        runtime.uow_factory,
        runtime.candidate,
        _Clock(),
        FileArtifactStore(tmp_path / "artifacts"),
    )
    next_candidate = _v2_snapshot(
        required_claim_checks=("source_exists", "candidate_is_measured"),
    )

    decision = active_v2_coordinator.submit(
        _transition(runtime.candidate, next_candidate, "weak-v2-transition")
    )

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code
    runtime.engine.dispose()


def test_active_v2_requirement_governs_v2_to_v1_rollback(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        minimum_verification=VerificationLevel.FORMAL_VERIFIER,
    )
    assert runtime.coordinator.submit(
        _transition(runtime.prior, runtime.candidate, "rollback-bootstrap")
    ).accepted
    active_v2_coordinator = TransactionCoordinator(
        runtime.uow_factory,
        runtime.candidate,
        _Clock(),
        FileArtifactStore(tmp_path / "artifacts"),
    )
    proposal = _transition(runtime.candidate, runtime.prior, "weak-v1-rollback")
    proposal = proposal.model_copy(
        update={
            "rollback_policy_hash": runtime.prior.policy_hash,
            "measurement": proposal.measurement.model_copy(
                update={"rollback_target_id": runtime.prior.policy_hash}
            ),
        }
    )

    decision = active_v2_coordinator.submit(proposal)

    assert decision.accepted is False
    assert decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    runtime.engine.dispose()


@pytest.mark.integration
def test_measurement_backed_v1_to_v2_transition_is_atomic_and_attributed_to_v1(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "accepted")

    decision = runtime.coordinator.submit(proposal)

    assert decision.accepted is True
    with runtime.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        repositories = unit_of_work.repositories()
        assert ResearchRunRepository(connection).get(proposal.research_run.run_id) == (
            proposal.research_run
        )
        assert (
            EvaluatorAuditRepository(connection).get(proposal.evaluator_audit.evaluator_audit_id)
            == proposal.evaluator_audit
        )
        assert (
            SelfImprovementMeasurementRepository(connection).get(
                proposal.measurement.measurement_id
            )
            == proposal.measurement
        )
        assert tuple(
            snapshot.policy.schema_version for snapshot in repositories.policies.list_all()
        ) == (
            1,
            2,
        )
        assert repositories.policies.get_active() == runtime.candidate
        assert len(repositories.transactions.list_all()) == 1
        event = repositories.audit.list_all()[0]
        assert event.payload["policy_hash"] == runtime.prior.policy_hash
        assert event.payload["prior_policy_hash"] == runtime.prior.policy_hash
        assert event.payload["candidate_policy_hash"] == runtime.candidate.policy_hash
        assert event.payload["rollback_policy_hash"] == runtime.prior.policy_hash
    runtime.engine.dispose()


def test_workspace_integrity_derives_active_policy_pointer_from_transitions(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "pointer-tamper")
    assert runtime.coordinator.submit(proposal).accepted
    with runtime.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        unit_of_work.connection.execute(
            update(governance_state).values(active_policy_hash=runtime.prior.policy_hash)
        )
    with runtime.uow_factory() as unit_of_work:
        result = verify_workspace(unit_of_work.repositories(), runtime.artifact_store)

    assert result.valid is False
    assert "active policy pointer" in (result.reason or "")
    runtime.engine.dispose()


def test_workspace_integrity_binds_transition_policy_hashes_to_audit(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "audit-tamper")
    assert runtime.coordinator.submit(proposal).accepted
    with runtime.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        event = unit_of_work.repositories().audit.list_all()[0]
        payload = dict(event.payload)
        payload["candidate_policy_hash"] = "f" * 64
        replacement = append_event(None, event.event_type, payload, event.occurred_at)
        unit_of_work.connection.exec_driver_sql("DROP TRIGGER audit_events_no_update")
        unit_of_work.connection.execute(
            update(audit_events).values(
                event_id=replacement.event_id,
                previous_hash=replacement.previous_hash,
                payload_hash=replacement.payload_hash,
                event_hash=replacement.event_hash,
                event_json=replacement.model_dump_json(),
            )
        )
    with runtime.uow_factory() as unit_of_work:
        result = verify_workspace(unit_of_work.repositories(), runtime.artifact_store)

    assert result.valid is False
    assert "transition audit candidate policy hash" in (result.reason or "")
    runtime.engine.dispose()


@pytest.mark.integration
def test_unexpected_fault_after_transition_projection_rolls_back_every_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "fault")

    def fail_transaction_append(
        self: TransactionRepository, *args: object, **kwargs: object
    ) -> None:
        del self, args, kwargs
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(TransactionRepository, "add", fail_transaction_append)
    with pytest.raises(RuntimeError, match="injected transaction failure"):
        runtime.coordinator.submit(proposal)

    with runtime.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        repositories = unit_of_work.repositories()
        assert ResearchRunRepository(connection).list_all() == ()
        assert EvaluatorAuditRepository(connection).list_all() == ()
        assert SelfImprovementMeasurementRepository(connection).list_all() == ()
        assert repositories.policies.get_active() == runtime.prior
        assert repositories.policies.list_all() == (runtime.prior,)
        assert repositories.transactions.list_all() == ()
        assert repositories.audit.list_all() == ()
    runtime.engine.dispose()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        (
            lambda proposal: proposal.model_copy(
                update={
                    "research_run": proposal.research_run.model_copy(
                        update={"creator": _model_actor("other-run-creator")}
                    )
                }
            ),
            RejectionCode.ENTITY_ID_MISMATCH,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "evaluator_audit": proposal.evaluator_audit.model_copy(
                        update={"proposer": _model_actor("other-audit-proposer")}
                    )
                }
            ),
            RejectionCode.ENTITY_ID_MISMATCH,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "measurement": proposal.measurement.model_copy(
                        update={"proposer": _model_actor("other-measurement-proposer")}
                    )
                }
            ),
            RejectionCode.ENTITY_ID_MISMATCH,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "measurement": proposal.measurement.model_copy(
                        update={"decision_authority": _human_actor("other-authority")}
                    )
                }
            ),
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
        ),
        (
            lambda proposal: _with_classification(
                proposal,
                target=ChangeTarget.OUTPUT,
            ),
            RejectionCode.PERMISSION_DENIED,
        ),
        (
            lambda proposal: _with_classification(
                proposal,
                persistence=PersistenceScope.EPHEMERAL_OUTPUT,
            ),
            RejectionCode.PERMISSION_DENIED,
        ),
        (
            lambda proposal: _with_classification(
                proposal,
                loop_closure=LoopClosure.CLOSED_LOOP,
            ),
            RejectionCode.PROHIBITED_CLOSED_LOOP,
        ),
        (
            lambda proposal: _with_classification(
                proposal,
                verification_level=VerificationLevel.MODEL_CONFIDENCE,
            ),
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
        ),
        (
            lambda proposal: _with_classification(
                proposal,
                grounding=ExternalGrounding.NONE,
            ),
            RejectionCode.INSUFFICIENT_GROUNDING,
        ),
        (
            lambda proposal: _with_classification(
                proposal,
                signal=ImprovementSignal.INTRINSIC_EVALUATIVE_FEEDBACK,
            ),
            RejectionCode.INSUFFICIENT_GROUNDING,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "research_run": proposal.research_run.model_copy(
                        update={"active_governance_policy_hash": "f" * 64}
                    )
                }
            ),
            RejectionCode.POLICY_HASH_MISMATCH,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "measurement": proposal.measurement.model_copy(
                        update={"run_id": "unrelated-run"}
                    )
                }
            ),
            RejectionCode.INVALID_LINEAGE,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "measurement": proposal.measurement.model_copy(
                        update={
                            "classification": proposal.classification.model_copy(
                                update={"loop_closure": LoopClosure.HUMAN_ON_LOOP}
                            )
                        }
                    )
                }
            ),
            RejectionCode.INVALID_LINEAGE,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "measurement": proposal.measurement.model_copy(
                        update={"baseline_version_id": "f" * 64}
                    )
                }
            ),
            RejectionCode.INVALID_LINEAGE,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "measurement": proposal.measurement.model_copy(
                        update={"decision": MeasurementDecision.REJECTED}
                    )
                }
            ),
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
        ),
        (
            lambda proposal: proposal.model_copy(
                update={
                    "measurement": proposal.measurement.model_copy(update={"protected_metrics": ()})
                }
            ),
            RejectionCode.INDEPENDENT_REVIEW_REQUIRED,
        ),
    ),
)
def test_governance_handler_revalidates_every_constitutional_boundary(
    tmp_path: Path,
    mutation: Callable[[ProposeGovernancePolicyTransition], ProposeGovernancePolicyTransition],
    expected_code: RejectionCode,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = _transition(runtime.prior, runtime.candidate, "boundary")
    handler = ProposeGovernancePolicyTransitionHandler()
    context = handler.build_context(proposal, _TransitionReadCapabilities(runtime.prior))

    decision = handler.decide(mutation(proposal), context)

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code
    runtime.engine.dispose()


@pytest.mark.parametrize(
    ("proposal_factory", "context_update", "expected_code"),
    (
        (
            lambda prior, candidate: _transition(prior, candidate, "active-mismatch"),
            lambda proposal, context: context.model_copy(
                update={
                    "active_policy": _v2_snapshot(required_claim_checks=("different-active-check",))
                }
            ),
            RejectionCode.POLICY_HASH_MISMATCH,
        ),
        (
            lambda prior, candidate: _transition(prior, candidate, "missing-rollback"),
            lambda proposal, context: context.model_copy(update={"rollback_policy": None}),
            RejectionCode.INVALID_LINEAGE,
        ),
        (
            lambda prior, candidate: _transition(prior, candidate, "existing-run"),
            lambda proposal, context: context.model_copy(
                update={"existing_run": proposal.research_run}
            ),
            RejectionCode.ENTITY_ALREADY_EXISTS,
        ),
        (
            lambda prior, candidate: _transition(prior, prior, "v1-candidate"),
            lambda proposal, context: context,
            RejectionCode.INVALID_LINEAGE,
        ),
        (
            lambda prior, candidate: _transition(
                prior,
                _v2_snapshot(required_claim_checks=("candidate-only-check",)),
                "weakened-checks",
            ),
            lambda proposal, context: context,
            RejectionCode.PERMISSION_DENIED,
        ),
        (
            lambda prior, candidate: _transition(
                prior,
                _snapshot_for_policy(
                    candidate.policy.model_copy(update={"human_approval_for": frozenset()})
                ),
                "missing-human-approval",
            ),
            lambda proposal, context: context,
            RejectionCode.PERMISSION_DENIED,
        ),
        (
            lambda prior, candidate: _transition(prior, candidate, "stored-mismatch"),
            lambda proposal, context: context.model_copy(
                update={
                    "stored_candidate": _v2_snapshot(
                        required_claim_checks=("different-stored-check",)
                    )
                }
            ),
            RejectionCode.POLICY_HASH_MISMATCH,
        ),
    ),
)
def test_governance_handler_revalidates_context_and_candidate_lineage(
    tmp_path: Path,
    proposal_factory: Callable[[PolicySnapshot, PolicySnapshot], ProposeGovernancePolicyTransition],
    context_update: Callable[
        [ProposeGovernancePolicyTransition, _GovernanceTransitionContext],
        _GovernanceTransitionContext,
    ],
    expected_code: RejectionCode,
) -> None:
    runtime = _runtime(tmp_path)
    proposal = proposal_factory(runtime.prior, runtime.candidate)
    handler = ProposeGovernancePolicyTransitionHandler()
    context = handler.build_context(proposal, _TransitionReadCapabilities(runtime.prior))

    decision = handler.decide(proposal, context_update(proposal, context))

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code
    runtime.engine.dispose()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("missing-requirement", RejectionCode.PERMISSION_DENIED),
        ("missing-approval", RejectionCode.INDEPENDENT_REVIEW_REQUIRED),
        ("missing-protected", RejectionCode.INDEPENDENT_REVIEW_REQUIRED),
        ("missing-rollback", RejectionCode.INVALID_LINEAGE),
    ),
)
def test_active_v2_policy_revalidates_each_transition_requirement(
    mutation: str,
    expected_code: RejectionCode,
) -> None:
    active = _v2_snapshot()
    if mutation == "missing-requirement":
        active_policy = active.policy.model_copy(
            update={
                "adaptation_requirements": (
                    active.policy.adaptation_requirements[0].model_copy(
                        update={
                            "change_target": ChangeTarget.PROMPT,
                            "persistence": PersistenceScope.PERSISTENT_SKILL,
                        }
                    ),
                )
            }
        )
        active = _snapshot_for_policy(active_policy)
    candidate = _v2_snapshot(required_claim_checks=active.policy.required_claim_checks)
    proposal = _transition(active, candidate, f"active-v2-{mutation}")
    rollback: PolicySnapshot | None = active
    if mutation == "missing-approval":
        proposal = proposal.model_copy(update={"approval": None})
    elif mutation == "missing-protected":
        proposal = proposal.model_copy(
            update={
                "measurement": proposal.measurement.model_copy(update={"protected_metrics": ()})
            }
        )
    elif mutation == "missing-rollback":
        rollback = None
    context = _GovernanceTransitionContext(
        active_policy=active,
        prior_policy=active,
        rollback_policy=rollback,
        existing_run=None,
        existing_audit=None,
        existing_measurement=None,
        stored_candidate=None,
    )

    decision = ProposeGovernancePolicyTransitionHandler().decide(proposal, context)

    assert decision.accepted is False
    assert decision.reasons[0].code is expected_code


def test_active_v2_policy_accepts_exact_requirement_before_candidate_checks() -> None:
    active = _v2_snapshot()
    candidate = _v2_snapshot(required_claim_checks=active.policy.required_claim_checks)
    proposal = _transition(active, candidate, "active-v2-valid")
    context = _GovernanceTransitionContext(
        active_policy=active,
        prior_policy=active,
        rollback_policy=active,
        existing_run=None,
        existing_audit=None,
        existing_measurement=None,
        stored_candidate=None,
    )

    decision = ProposeGovernancePolicyTransitionHandler().decide(proposal, context)

    assert decision.accepted is True


def test_governance_handler_rejects_candidate_hash_and_weak_v2_requirement(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    handler = ProposeGovernancePolicyTransitionHandler()
    proposal = _transition(runtime.prior, runtime.candidate, "candidate-boundary")
    context = handler.build_context(proposal, _TransitionReadCapabilities(runtime.prior))
    forged_hash = proposal.model_copy(
        update={
            "candidate_policy_snapshot": proposal.candidate_policy_snapshot.model_copy(
                update={"policy_hash": "e" * 64}
            )
        }
    )
    weak_policy = runtime.candidate.policy.model_copy(
        update={
            "adaptation_requirements": (
                runtime.candidate.policy.adaptation_requirements[0].model_copy(
                    update={"required_approver_kind": ActorKind.MODEL}
                ),
            )
        }
    )
    weak_candidate = _snapshot_for_policy(weak_policy)
    weak_proposal = _transition(runtime.prior, weak_candidate, "weak-v2-candidate")
    weak_context = handler.build_context(
        weak_proposal,
        _TransitionReadCapabilities(runtime.prior),
    )

    hash_decision = handler.decide(forged_hash, context)
    weak_decision = handler.decide(weak_proposal, weak_context)

    assert hash_decision.reasons[0].code is RejectionCode.POLICY_HASH_MISMATCH
    assert weak_decision.reasons[0].code is RejectionCode.PERMISSION_DENIED
    runtime.engine.dispose()


def test_governance_projection_rejects_rejected_decision(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    handler = ProposeGovernancePolicyTransitionHandler()
    proposal = _transition(runtime.prior, runtime.candidate, "rejected-projection")
    context = handler.build_context(proposal, _TransitionReadCapabilities(runtime.prior))
    rejected = handler.decide(proposal.model_copy(update={"approval": None}), context)

    with pytest.raises(ValueError, match="rejected proposals cannot be projected"):
        handler.project(proposal, rejected, object())  # type: ignore[arg-type]
    runtime.engine.dispose()


def test_active_v2_rejects_wrong_approver_kind_and_allows_exact_v1_rollback() -> None:
    active = _v2_snapshot()
    v2_candidate = _v2_snapshot(required_claim_checks=active.policy.required_claim_checks)
    wrong_approver = _transition(active, v2_candidate, "wrong-approver-kind")
    wrong_approver = wrong_approver.model_copy(
        update={
            "approval": Approval(
                approver=_model_actor("model-approver"),
                approved_at=NOW,
            )
        }
    )
    v1_policy = GovernancePolicy(
        required_claim_checks=active.policy.required_claim_checks,
        human_approval_for=frozenset({"governance_change"}),
    )
    v1_candidate = PolicySnapshot(policy_hash=policy_hash(v1_policy), policy=v1_policy)
    rollback = _transition(active, v1_candidate, "exact-v1-rollback")
    context = _GovernanceTransitionContext(
        active_policy=active,
        prior_policy=active,
        rollback_policy=active,
        existing_run=None,
        existing_audit=None,
        existing_measurement=None,
        stored_candidate=None,
    )
    handler = ProposeGovernancePolicyTransitionHandler()

    wrong_decision = handler.decide(wrong_approver, context)
    rollback_decision = handler.decide(rollback, context)

    assert wrong_decision.reasons[0].code is RejectionCode.INDEPENDENT_REVIEW_REQUIRED
    assert rollback_decision.accepted is True


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Runtime:
    def __init__(
        self,
        coordinator: TransactionCoordinator,
        uow_factory: Callable[[], DatabaseUnitOfWork],
        engine: Engine,
        artifact_store: FileArtifactStore,
        prior: PolicySnapshot,
        candidate: PolicySnapshot,
    ) -> None:
        self.coordinator = coordinator
        self.uow_factory = uow_factory
        self.engine = engine
        self.artifact_store = artifact_store
        self.prior = prior
        self.candidate = candidate


class _TransitionReadCapabilities:
    def __init__(self, active_policy: PolicySnapshot) -> None:
        self._active_policy = active_policy

    def policy_snapshot(self) -> PolicySnapshot:
        return self._active_policy

    def get_policy(self, policy_hash_value: str) -> PolicySnapshot | None:
        if policy_hash_value == self._active_policy.policy_hash:
            return self._active_policy
        return None

    def get_run(self, run_id: str) -> ResearchRun | None:
        del run_id
        return None

    def get_evaluator_audit(self, evaluator_audit_id: str) -> EvaluatorAuditRecord | None:
        del evaluator_audit_id
        return None

    def get_measurement(
        self,
        measurement_id: str,
    ) -> SelfImprovementMeasurementRecord | None:
        del measurement_id
        return None


def _runtime(
    tmp_path: Path,
    *,
    minimum_verification: VerificationLevel = (VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK),
    permitted_grounding: frozenset[ExternalGrounding] = frozenset(
        {ExternalGrounding.CONTROLLED_EXPERIMENT}
    ),
) -> _Runtime:
    prior_policy = GovernancePolicy(required_claim_checks=("source_exists",))
    prior = PolicySnapshot(policy_hash=policy_hash(prior_policy), policy=prior_policy)
    candidate = _v2_snapshot(
        minimum_verification=minimum_verification,
        permitted_grounding=permitted_grounding,
    )
    database_url = f"sqlite:///{(tmp_path / 'transition.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_database_engine(database_url)

    def uow_factory() -> DatabaseUnitOfWork:
        return DatabaseUnitOfWork(engine)

    with uow_factory() as unit_of_work:
        unit_of_work.repositories().policies.add_and_activate(prior, NOW)
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    coordinator = TransactionCoordinator(
        uow_factory,
        prior,
        _Clock(),
        artifact_store,
    )
    return _Runtime(coordinator, uow_factory, engine, artifact_store, prior, candidate)


def _v2_snapshot(
    *,
    required_claim_checks: tuple[str, ...] = ("source_exists",),
    minimum_verification: VerificationLevel = (VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK),
    permitted_grounding: frozenset[ExternalGrounding] = frozenset(
        {ExternalGrounding.CONTROLLED_EXPERIMENT}
    ),
) -> PolicySnapshot:
    policy = GovernancePolicyV2(
        required_claim_checks=required_claim_checks,
        human_approval_for=frozenset({"governance_change"}),
        adaptation_requirements=(
            AdaptationRequirement(
                change_target=ChangeTarget.GOVERNANCE_POLICY,
                persistence=PersistenceScope.GOVERNANCE_POLICY,
                minimum_verification=minimum_verification,
                permitted_grounding=permitted_grounding,
                required_approver_kind=ActorKind.HUMAN,
                protected_evaluation_required=True,
                rollback_required=True,
            ),
        ),
    )
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _snapshot_for_policy(policy: GovernancePolicyV2) -> PolicySnapshot:
    return PolicySnapshot(policy_hash=policy_hash(policy), policy=policy)


def _with_classification(
    proposal: ProposeGovernancePolicyTransition,
    **updates: object,
) -> ProposeGovernancePolicyTransition:
    return proposal.model_copy(
        update={"classification": proposal.classification.model_copy(update=updates)}
    )


def _transition(
    prior: PolicySnapshot,
    candidate: PolicySnapshot,
    prefix: str,
) -> ProposeGovernancePolicyTransition:
    proposer = _model_actor(f"{prefix}-proposer")
    approver = _human_actor(f"{prefix}-approver")
    evaluator = _model_actor(f"{prefix}-evaluator")
    auditor = _human_actor(f"{prefix}-auditor")
    classification = ChangeClassification(
        target=ChangeTarget.GOVERNANCE_POLICY,
        loop_closure=LoopClosure.HUMAN_IN_LOOP,
        persistence=PersistenceScope.GOVERNANCE_POLICY,
        verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        grounding=ExternalGrounding.CONTROLLED_EXPERIMENT,
        signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
    )
    budget = _budget(10)
    run = ResearchRun(
        run_id=f"{prefix}-run",
        charter="Independently measure a candidate governance policy",
        scope=("offline constitutional transition",),
        creator=proposer,
        created_at=NOW,
        active_governance_policy_hash=prior.policy_hash,
        model_configuration_version_id=None,
        scaffold_configuration_version_id=None,
        budget_allocation=RunBudgetAllocation(
            execution=budget,
            search=budget,
            evaluation=budget,
            judging=budget,
            human=budget,
        ),
        final_validator=approver,
        final_validator_version="human-v1",
        environment_snapshot_id=f"{prefix}-environment",
    )
    audit = EvaluatorAuditRecord(
        evaluator_audit_id=f"{prefix}-audit",
        auditor=auditor,
        auditor_version="auditor-v1",
        auditor_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        evaluator=evaluator,
        evaluator_version="evaluator-v1",
        proposer=proposer,
        candidate_producer=proposer,
        auditor_to_evaluator=ActorRelationship.INDEPENDENT,
        auditor_to_proposer=ActorRelationship.INDEPENDENT,
        auditor_to_candidate_producer=ActorRelationship.INDEPENDENT,
        independence_enforced=True,
        evidence_ids=(f"{prefix}-protected-evidence",),
        checks_run=(f"{prefix}-audit-check",),
        assumptions=("policy fixtures are canonical",),
        limitations=("offline deterministic coverage",),
        result=AssessmentOutcome.PASSED,
        audited_at=NOW,
        governing_policy_hash=prior.policy_hash,
    )
    trajectory = (_point(prefix, 0), _point(prefix, 1))
    measurement = SelfImprovementMeasurementRecord(
        measurement_id=f"{prefix}-measurement",
        change_id=f"{prefix}-change",
        run_id=run.run_id,
        classification=classification,
        proposer=proposer,
        evaluator=evaluator,
        evaluator_version="evaluator-v1",
        evaluator_tier="protected-external",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        baseline_version_id=prior.policy_hash,
        candidate_version_id=candidate.policy_hash,
        protected_metrics=(_metric(f"{prefix}-protected", protected=True),),
        countermetrics=(_metric(f"{prefix}-countermetric", protected=False),),
        expected_final_index=1,
        trajectory=trajectory,
        peak_observation=_observation(trajectory[1]),
        final_observation=_observation(trajectory[1]),
        attempted_changes=(f"{prefix}-admitted", f"{prefix}-rejected"),
        admitted_changes=(f"{prefix}-admitted",),
        rejected_changes=(f"{prefix}-rejected",),
        regressions=("one retained countermetric regression",),
        rollback_events=(f"{prefix}-rollback-drill",),
        execution_budget=_budget(10),
        search_budget=_budget(20),
        evaluation_budget=_budget(30),
        judging_budget=_budget(40),
        human_budget=_budget(50),
        usage_by_category=_usage_breakdown(
            execution=_usage(),
            search=_usage(),
        ),
        usage=_usage(2),
        failures=("one failed candidate retained",),
        unmeasured_coverage_gaps=("live production behavior remained unmeasured",),
        rollback_target_id=prior.policy_hash,
        evaluator_audit_id=audit.evaluator_audit_id,
        decision=MeasurementDecision.ACCEPTED,
        decision_authority=approver,
        decided_at=DECIDED_AT,
        governing_policy_hash=prior.policy_hash,
    )
    return ProposeGovernancePolicyTransition(
        proposal_id=f"{prefix}-transition",
        idempotency_key=f"{prefix}-transition-key",
        proposer=proposer,
        approval=Approval(approver=approver, approved_at=NOW),
        research_run=run,
        evaluator_audit=audit,
        measurement=measurement,
        candidate_policy_snapshot=candidate,
        prior_policy_hash=prior.policy_hash,
        rollback_policy_hash=prior.policy_hash,
        classification=classification,
    )


def _assert_rejected_transition_is_durable_without_projection(runtime: _Runtime) -> None:
    with runtime.uow_factory() as unit_of_work:
        assert unit_of_work.connection is not None
        connection = unit_of_work.connection
        repositories = unit_of_work.repositories()
        assert ResearchRunRepository(connection).list_all() == ()
        assert EvaluatorAuditRepository(connection).list_all() == ()
        assert SelfImprovementMeasurementRepository(connection).list_all() == ()
        assert repositories.policies.get_active() == runtime.prior
        assert len(repositories.transactions.list_all()) == 1
        assert len(repositories.audit.list_all()) == 1
        assert verify_workspace(repositories, runtime.artifact_store).valid


def _point(prefix: str, index: int) -> PerformanceTrajectoryPoint:
    candidate_id = f"{prefix}-{'admitted' if index == 0 else 'rejected'}"
    usage = _usage()
    return PerformanceTrajectoryPoint(
        step_index=index,
        change_id=f"{prefix}-change",
        grounding=(ExternalGrounding.CONTROLLED_EXPERIMENT,),
        metrics=(_metric(f"{prefix}-trajectory-{index}", protected=False),),
        attempted_change_ids=(candidate_id,),
        admitted_change_ids=(candidate_id,) if index == 0 else (),
        rejected_change_ids=() if index == 0 else (candidate_id,),
        regressions=("one retained countermetric regression",) if index == 0 else (),
        rollback_event_ids=() if index == 0 else (f"{prefix}-rollback-drill",),
        usage_by_category=_usage_breakdown(
            execution=usage if index == 0 else None,
            search=usage if index == 1 else None,
        ),
        usage=usage,
    )


def _observation(point: PerformanceTrajectoryPoint) -> TrajectoryObservation:
    return TrajectoryObservation(step_index=point.step_index, metrics=point.metrics)


def _metric(identifier: str, *, protected: bool) -> MetricObservation:
    return MetricObservation(
        metric_id=identifier,
        value=0.5,
        source_id=f"{identifier}-source",
        protected=protected,
        external=True,
    )


def _budget(value: int) -> ResourceBudget:
    return ResourceBudget(
        cost_usd=float(value),
        compute_units=float(value),
        tokens=value,
        elapsed_seconds=float(value),
        tool_calls=value,
        human_interventions=value,
    )


def _usage(multiplier: int = 1) -> ResourceUsage:
    return ResourceUsage(
        cost_usd=float(multiplier),
        compute_units=float(multiplier),
        tokens=multiplier,
        elapsed_seconds=float(multiplier),
        tool_calls=multiplier,
        human_interventions=multiplier,
    )


def _zero_usage() -> ResourceUsage:
    return _usage(0)


def _usage_breakdown(
    *,
    execution: ResourceUsage | None = None,
    search: ResourceUsage | None = None,
) -> ResourceUsageBreakdown:
    return ResourceUsageBreakdown(
        execution=execution or _zero_usage(),
        search=search or _zero_usage(),
        evaluation=_zero_usage(),
        judging=_zero_usage(),
        human=_zero_usage(),
    )


def _human_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity(actor_id=identifier, kind=ActorKind.HUMAN, created_at=NOW)


def _model_actor(identifier: str) -> ActorIdentity:
    return ActorIdentity.model(identifier, f"provider-{identifier}", identifier, None, NOW)
