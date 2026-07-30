from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

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
from super_scientist.domain.primitives import canonical_json_bytes
from super_scientist.quality.policy_records import (
    QualityPolicyApprovalRecord,
    QualityPolicyProposal,
)

NOW = datetime(2026, 7, 27, 20, 0, tzinfo=UTC)


def _actor(actor_id: str, kind: ActorKind = ActorKind.HUMAN) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=kind, created_at=NOW)


def _proposal_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "proposal_id": "quality-policy-0.2.0-wheel-install",
        "proposer": _actor("task-18-proposer", ActorKind.SERVICE),
        "prior_registry_hash": "1" * 64,
        "proposed_registry_hash": "2" * 64,
        "source_diff_hash": "3" * 64,
        "firewall_policy_sha256": "5" * 64,
        "allowed_attribution_paths": ("docs/a.md", "docs/b.md"),
        "governing_policy_hash": "4" * 64,
        "quality_policy_hash": "36db4f9b9c290029f4d628c1934bbabefab731ac7729a5a9446998df396a132b",
        "measurement_id": "measurement-1",
        "evaluator_audit_id": "audit-1",
        "rationale": "Add one fixed built-wheel installation and CLI smoke check.",
        "regression_tests": (
            "tests/adversarial/test_imported_pattern_tampering.py",
            "tests/unit/quality/test_runner.py",
        ),
        "rollback_commit": "29342b17de5f9169921cba425ba8765de5828478",
    }


def test_pending_proposal_and_human_approval_are_separate_canonical_records() -> None:
    from super_scientist.quality.policy_records import (
        QualityPolicyApprovalRecord,
        QualityPolicyProposal,
        canonical_record_bytes,
        canonical_record_hash,
    )

    proposal = QualityPolicyProposal.model_validate(_proposal_payload())
    proposal_bytes = canonical_record_bytes(proposal)
    approval = QualityPolicyApprovalRecord(
        approval_id="approval-1",
        proposal_id=proposal.proposal_id,
        proposal_sha256=canonical_record_hash(proposal),
        measurement_id=proposal.measurement_id,
        measurement_sha256="6" * 64,
        evaluator_audit_id=proposal.evaluator_audit_id,
        evaluator_audit_sha256="7" * 64,
        approver=_actor("human-approver"),
        approval_text="push to pr.",
        approved_at=NOW,
        governing_policy_hash=proposal.governing_policy_hash,
    )

    assert proposal_bytes == canonical_json_bytes(proposal.model_dump(mode="json"))
    assert proposal.model_fields_set == set(_proposal_payload())
    assert approval.proposal_sha256 == canonical_record_hash(proposal)
    with pytest.raises(ValidationError):
        QualityPolicyProposal.model_validate(
            {**_proposal_payload(), "independent_human_approval": "push to pr."}
        )
    with pytest.raises(ValidationError):
        approval.model_copy(update={"approval_text": "approved"}).model_validate(
            approval.model_dump(mode="python") | {"approval_text": "approved"}
        )


def test_admission_accepts_exact_measurement_audit_and_human_approval_chain() -> None:
    from super_scientist.quality.policy_records import admit_quality_policy_records

    proposal, measurement, audit, approval = _record_chain()

    admitted = admit_quality_policy_records(
        proposal=proposal,
        measurement=measurement,
        evaluator_audit=audit,
        approval=approval,
    )

    assert admitted == approval


@pytest.mark.parametrize(
    "missing",
    ("proposal", "measurement", "evaluator_audit", "approval"),
)
def test_admission_rejects_a_missing_canonical_record(missing: str) -> None:
    from super_scientist.quality.policy_records import admit_quality_policy_records

    proposal, measurement, audit, approval = _record_chain()
    proposal_arg = None if missing == "proposal" else proposal
    measurement_arg = None if missing == "measurement" else measurement
    audit_arg = None if missing == "evaluator_audit" else audit
    approval_arg = None if missing == "approval" else approval

    with pytest.raises(ValueError, match="complete canonical record chain"):
        admit_quality_policy_records(
            proposal=proposal_arg,
            measurement=measurement_arg,
            evaluator_audit=audit_arg,
            approval=approval_arg,
        )


@pytest.mark.parametrize(
    "mismatch",
    (
        "proposal_id",
        "proposal_hash",
        "measurement_id",
        "measurement_hash",
        "audit_id",
        "audit_hash",
        "evaluator",
        "governing_policy",
        "rollback",
    ),
)
def test_admission_rejects_mismatched_record_lineage(mismatch: str) -> None:
    from super_scientist.quality.policy_records import admit_quality_policy_records

    proposal, measurement, audit, approval = _record_chain()
    if mismatch == "proposal_id":
        approval = approval.model_copy(update={"proposal_id": "other-proposal"})
    elif mismatch == "proposal_hash":
        approval = approval.model_copy(update={"proposal_sha256": "f" * 64})
    elif mismatch == "measurement_id":
        measurement = measurement.model_copy(update={"measurement_id": "other-measurement"})
    elif mismatch == "measurement_hash":
        approval = approval.model_copy(update={"measurement_sha256": "f" * 64})
    elif mismatch == "audit_id":
        audit = audit.model_copy(update={"evaluator_audit_id": "other-audit"})
    elif mismatch == "audit_hash":
        approval = approval.model_copy(update={"evaluator_audit_sha256": "f" * 64})
    elif mismatch == "evaluator":
        measurement = measurement.model_copy(
            update={"evaluator": _actor("other-evaluator", ActorKind.SERVICE)}
        )
    elif mismatch == "governing_policy":
        audit = audit.model_copy(update={"governing_policy_hash": "f" * 64})
    elif mismatch == "rollback":
        measurement = measurement.model_copy(update={"rollback_target_id": "other-rollback"})

    with pytest.raises(ValueError, match="record linkage"):
        admit_quality_policy_records(
            proposal=proposal,
            measurement=measurement,
            evaluator_audit=audit,
            approval=approval,
        )


@pytest.mark.parametrize(
    "authority_failure",
    ("failed_audit", "rejected_measurement", "nonhuman_decider", "different_approver"),
)
def test_admission_requires_passed_audit_accepted_decision_and_human_authority(
    authority_failure: str,
) -> None:
    from super_scientist.quality.policy_records import admit_quality_policy_records

    proposal, measurement, audit, approval = _record_chain()
    if authority_failure == "failed_audit":
        audit = audit.model_copy(update={"result": AssessmentOutcome.FAILED})
        approval = approval.model_copy(update={"evaluator_audit_sha256": _canonical_hash(audit)})
    elif authority_failure == "rejected_measurement":
        measurement = measurement.model_copy(update={"decision": MeasurementDecision.REJECTED})
        approval = approval.model_copy(update={"measurement_sha256": _canonical_hash(measurement)})
    elif authority_failure == "nonhuman_decider":
        measurement = measurement.model_copy(
            update={"decision_authority": _actor("automated-decider", ActorKind.SERVICE)}
        )
        approval = approval.model_copy(update={"measurement_sha256": _canonical_hash(measurement)})
    elif authority_failure == "different_approver":
        approval = approval.model_copy(update={"approver": _actor("other-human-approver")})

    with pytest.raises(ValueError, match="independent human authority"):
        admit_quality_policy_records(
            proposal=proposal,
            measurement=measurement,
            evaluator_audit=audit,
            approval=approval,
        )


@pytest.mark.parametrize(
    "cycle",
    ("self_audit", "self_approval", "approval_auditor_cycle"),
)
def test_admission_rejects_self_or_circular_governance_records(cycle: str) -> None:
    from super_scientist.quality.policy_records import admit_quality_policy_records

    proposal, measurement, audit, approval = _record_chain()
    if cycle == "self_audit":
        audit = audit.model_copy(update={"auditor": audit.evaluator})
        approval = approval.model_copy(update={"evaluator_audit_sha256": _canonical_hash(audit)})
    elif cycle == "self_approval":
        self_approver = approval.approver
        proposal = proposal.model_copy(update={"proposer": self_approver})
        audit = audit.model_copy(
            update={"proposer": self_approver, "candidate_producer": self_approver}
        )
        measurement = measurement.model_copy(update={"proposer": self_approver})
        approval = approval.model_copy(
            update={
                "proposal_sha256": _canonical_hash(proposal),
                "measurement_sha256": _canonical_hash(measurement),
                "evaluator_audit_sha256": _canonical_hash(audit),
            }
        )
    elif cycle == "approval_auditor_cycle":
        measurement = measurement.model_copy(update={"decision_authority": audit.auditor})
        approval = approval.model_copy(
            update={
                "approver": audit.auditor,
                "measurement_sha256": _canonical_hash(measurement),
            }
        )

    with pytest.raises(ValueError, match="self or circular"):
        admit_quality_policy_records(
            proposal=proposal,
            measurement=measurement,
            evaluator_audit=audit,
            approval=approval,
        )


@pytest.mark.parametrize("shared_field", ("configuration_hash", "provider_id", "model_id"))
def test_admission_rejects_correlated_human_approval(shared_field: str) -> None:
    from super_scientist.quality.policy_records import (
        admit_quality_policy_records,
        canonical_record_hash,
    )

    proposal, measurement, audit, approval = _record_chain()
    shared_value = "b" * 64 if shared_field == "configuration_hash" else f"shared-{shared_field}"
    correlated_evaluator = audit.evaluator.model_copy(update={shared_field: shared_value})
    audit = audit.model_copy(update={"evaluator": correlated_evaluator})
    measurement = measurement.model_copy(update={"evaluator": correlated_evaluator})
    correlated_approver = approval.approver.model_copy(update={shared_field: shared_value})
    measurement = measurement.model_copy(update={"decision_authority": correlated_approver})
    approval = approval.model_copy(
        update={
            "approver": correlated_approver,
            "measurement_sha256": canonical_record_hash(measurement),
            "evaluator_audit_sha256": canonical_record_hash(audit),
        }
    )

    with pytest.raises(ValueError, match="self or circular"):
        admit_quality_policy_records(
            proposal=proposal,
            measurement=measurement,
            evaluator_audit=audit,
            approval=approval,
        )


def test_ledger_append_is_idempotent_but_changed_content_conflicts(tmp_path: Path) -> None:
    from super_scientist.quality.policy_records import (
        QualityPolicyRecordConflict,
        QualityPolicyRecordLedger,
    )

    proposal, _, _, _ = _record_chain()
    ledger = QualityPolicyRecordLedger(tmp_path / "ledger")

    first = ledger.append(proposal)
    duplicate = ledger.append(proposal)

    assert duplicate == first
    changed = proposal.model_copy(update={"rationale": "Different canonical content."})
    with pytest.raises(QualityPolicyRecordConflict, match="different canonical content"):
        ledger.append(changed)


def test_ledger_verifies_the_linked_chain_and_detects_record_tampering(
    tmp_path: Path,
) -> None:
    from super_scientist.quality.policy_records import (
        QualityPolicyRecordIntegrityError,
        QualityPolicyRecordLedger,
    )

    proposal, measurement, audit, approval = _record_chain()
    root = tmp_path / "ledger"
    ledger = QualityPolicyRecordLedger(root)
    for record in (proposal, audit, measurement, approval):
        ledger.append(record)

    assert ledger.verify_approval(approval.approval_id) == approval

    proposal_path = next((root / "records" / "proposals").glob("*.json"))
    proposal_path.write_bytes(proposal_path.read_bytes() + b" ")
    with pytest.raises(QualityPolicyRecordIntegrityError, match="content-addressed"):
        ledger.verify_approval(approval.approval_id)


def test_tracked_quality_policy_records_form_the_canonical_approved_chain() -> None:
    from super_scientist.quality.policy_records import (
        QualityPolicyApprovalRecord,
        QualityPolicyProposal,
        admit_quality_policy_records,
        canonical_record_hash,
    )

    root = Path(__file__).parents[3] / "docs" / "reviews" / "quality-policy-records"
    proposal = QualityPolicyProposal.model_validate_json(
        (root / "pending-proposal.json").read_bytes()
    )
    measurement = SelfImprovementMeasurementRecord.model_validate_json(
        (root / "measurement.json").read_bytes()
    )
    audit = EvaluatorAuditRecord.model_validate_json((root / "evaluator-audit.json").read_bytes())
    approval = QualityPolicyApprovalRecord.model_validate_json(
        (root / "approval.json").read_bytes()
    )

    assert {
        "proposal": canonical_record_hash(proposal),
        "measurement": canonical_record_hash(measurement),
        "audit": canonical_record_hash(audit),
        "approval": canonical_record_hash(approval),
    } == {
        "proposal": "222347082e430fc490ae9720c52f25649f708a6a8ce5a113fd80defc3bffcaea",
        "measurement": "135ba5c235701c5b76298563d94f7b290b96904e10f8d0efd936bd8162dc60aa",
        "audit": "1a98c921e7b8495da0dbf9eb5505f604c6688cf05cb488c071e33ba811d8a390",
        "approval": "48b90474878ccaaaef98ef35ecd61e17cbd771dc96bb5f6a17195f1e91a55145",
    }
    assert (
        admit_quality_policy_records(
            proposal=proposal,
            measurement=measurement,
            evaluator_audit=audit,
            approval=approval,
        )
        == approval
    )


def test_ledger_append_has_no_source_or_registry_edit_authority(tmp_path: Path) -> None:
    from super_scientist.quality.policy_records import QualityPolicyRecordLedger

    sentinel = tmp_path / "src" / "super_scientist" / "quality" / "runner.py"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("immutable registry sentinel", encoding="utf-8")
    ledger_root = tmp_path / "governance-records"
    ledger = QualityPolicyRecordLedger(ledger_root)
    proposal, _, _, _ = _record_chain()

    ledger.append(proposal)

    assert sentinel.read_text(encoding="utf-8") == "immutable registry sentinel"
    assert all(path.is_relative_to(ledger_root) for path in ledger_root.rglob("*"))


def test_ledger_rejects_a_symlinked_record_parent_before_append_can_escape(
    tmp_path: Path,
) -> None:
    from super_scientist.quality.policy_records import (
        QualityPolicyRecordIntegrityError,
        QualityPolicyRecordLedger,
    )

    root = tmp_path / "ledger"
    outside = tmp_path / "outside"
    outside.mkdir()
    ledger = QualityPolicyRecordLedger(root)
    records = root / "records"
    records.mkdir()
    _symlink_or_skip(records / "proposals", outside, target_is_directory=True)
    proposal, _, _, _ = _record_chain()

    with pytest.raises(QualityPolicyRecordIntegrityError, match="symlink or reparse"):
        ledger.append(proposal)

    assert tuple(outside.iterdir()) == ()


def test_ledger_rejects_a_reparse_record_entry_before_idempotent_append(
    tmp_path: Path,
) -> None:
    from super_scientist.quality.policy_records import (
        QualityPolicyRecordIntegrityError,
        QualityPolicyRecordLedger,
    )

    root = tmp_path / "ledger"
    ledger = QualityPolicyRecordLedger(root)
    proposal, _, _, _ = _record_chain()
    ledger.append(proposal)
    record_path = next((root / "records" / "proposals").glob("*.json"))
    outside = tmp_path / "outside-proposal"
    outside.mkdir()
    record_path.unlink()
    _symlink_or_skip(record_path, outside, target_is_directory=True)

    with pytest.raises(QualityPolicyRecordIntegrityError, match="symlink or reparse"):
        ledger.append(proposal)

    assert tuple(outside.iterdir()) == ()


def test_ledger_rejects_a_reparse_record_entry_before_verification_read(
    tmp_path: Path,
) -> None:
    from super_scientist.quality.policy_records import (
        QualityPolicyRecordIntegrityError,
        QualityPolicyRecordLedger,
    )

    root = tmp_path / "ledger"
    ledger = QualityPolicyRecordLedger(root)
    proposal, measurement, audit, approval = _record_chain()
    for record in (proposal, audit, measurement, approval):
        ledger.append(record)
    approval_path = next((root / "records" / "approvals").glob("*.json"))
    outside = tmp_path / "outside-approval"
    outside.mkdir()
    approval_path.unlink()
    _symlink_or_skip(approval_path, outside, target_is_directory=True)

    with pytest.raises(QualityPolicyRecordIntegrityError, match="symlink or reparse"):
        ledger.verify_approval(approval.approval_id)


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as error:
        if target_is_directory and os.name == "nt":
            junction = subprocess.run(
                ("cmd.exe", "/c", "mklink", "/J", str(link), str(target)),
                check=False,
                capture_output=True,
                text=True,
            )
            if junction.returncode == 0:
                return
        pytest.skip(f"symlinks are unavailable in this environment: {error}")


def _record_chain() -> tuple[
    QualityPolicyProposal,
    SelfImprovementMeasurementRecord,
    EvaluatorAuditRecord,
    QualityPolicyApprovalRecord,
]:
    from super_scientist.quality.policy_records import (
        QualityPolicyApprovalRecord,
        QualityPolicyProposal,
        canonical_record_hash,
    )

    proposal = QualityPolicyProposal.model_validate(_proposal_payload())
    evaluator = _actor("quality-gate-evaluator", ActorKind.SERVICE)
    audit = EvaluatorAuditRecord(
        evaluator_audit_id=proposal.evaluator_audit_id,
        auditor=_actor("independent-auditor"),
        auditor_version="auditor-v1",
        auditor_category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
        evaluator=evaluator,
        evaluator_version="quality-gate-evaluator-v1",
        proposer=proposal.proposer,
        candidate_producer=proposal.proposer,
        auditor_to_evaluator=ActorRelationship.INDEPENDENT,
        auditor_to_proposer=ActorRelationship.INDEPENDENT,
        auditor_to_candidate_producer=ActorRelationship.INDEPENDENT,
        independence_enforced=True,
        evidence_ids=("old-eight-check-gate",),
        checks_run=("independent-quality-review",),
        assumptions=("repository evidence is complete",),
        limitations=("one repository state",),
        result=AssessmentOutcome.PASSED,
        audited_at=NOW,
        governing_policy_hash=proposal.governing_policy_hash,
    )
    point_0 = _trajectory_point(0, admitted=True)
    point_1 = _trajectory_point(1, admitted=False)
    aggregate_usage = _usage(2)
    measurement = SelfImprovementMeasurementRecord(
        measurement_id=proposal.measurement_id,
        change_id=proposal.proposal_id,
        run_id="task-18-quality-gate-run",
        classification=ChangeClassification(
            target=ChangeTarget.GOVERNANCE_POLICY,
            loop_closure=LoopClosure.HUMAN_IN_LOOP,
            persistence=PersistenceScope.GOVERNANCE_POLICY,
            verification_level=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            grounding=ExternalGrounding.INDEPENDENT_TEST_SUITE,
            signal=ImprovementSignal.EMPIRICAL_MEASUREMENT,
        ),
        proposer=proposal.proposer,
        evaluator=evaluator,
        evaluator_version=audit.evaluator_version,
        evaluator_tier="protected-independent",
        grounding=(ExternalGrounding.INDEPENDENT_TEST_SUITE,),
        baseline_version_id=proposal.prior_registry_hash,
        candidate_version_id=proposal.proposed_registry_hash,
        protected_metrics=(
            MetricObservation(
                metric_id="old-gate-pass-rate",
                value=1.0,
                source_id="old-eight-check-gate",
                protected=True,
                external=True,
            ),
        ),
        countermetrics=(
            MetricObservation(
                metric_id="failed-attempts-retained",
                value=1.0,
                source_id="task-18-quality-gate-run",
                protected=False,
                external=True,
            ),
        ),
        expected_final_index=1,
        trajectory=(point_0, point_1),
        peak_observation=TrajectoryObservation(step_index=1, metrics=point_1.metrics),
        final_observation=TrajectoryObservation(step_index=1, metrics=point_1.metrics),
        attempted_changes=("candidate-0", "candidate-1"),
        admitted_changes=("candidate-0",),
        rejected_changes=("candidate-1",),
        regressions=("attempt-1-failed",),
        rollback_events=("rollback-drill-1",),
        execution_budget=_budget(),
        search_budget=_budget(),
        evaluation_budget=_budget(),
        judging_budget=_budget(),
        human_budget=_budget(),
        usage_by_category=_usage_breakdown(aggregate_usage),
        usage=aggregate_usage,
        failures=("attempt-1-failed",),
        unmeasured_coverage_gaps=("built-wheel dependency installation remained unmeasured",),
        rollback_target_id=proposal.rollback_commit,
        evaluator_audit_id=audit.evaluator_audit_id,
        decision=MeasurementDecision.ACCEPTED,
        decision_authority=_actor("human-approver"),
        decided_at=NOW,
        governing_policy_hash=proposal.governing_policy_hash,
    )
    approval = QualityPolicyApprovalRecord(
        approval_id="approval-1",
        proposal_id=proposal.proposal_id,
        proposal_sha256=canonical_record_hash(proposal),
        measurement_id=measurement.measurement_id,
        measurement_sha256=canonical_record_hash(measurement),
        evaluator_audit_id=audit.evaluator_audit_id,
        evaluator_audit_sha256=canonical_record_hash(audit),
        approver=measurement.decision_authority,
        approval_text="push to pr.",
        approved_at=NOW,
        governing_policy_hash=proposal.governing_policy_hash,
    )
    return proposal, measurement, audit, approval


def _canonical_hash(record: BaseModel) -> str:
    from super_scientist.quality.policy_records import canonical_record_hash

    return canonical_record_hash(record)


def _trajectory_point(step_index: int, *, admitted: bool) -> PerformanceTrajectoryPoint:
    usage = _usage()
    return PerformanceTrajectoryPoint(
        step_index=step_index,
        change_id="quality-policy-0.2.0-wheel-install",
        grounding=(ExternalGrounding.INDEPENDENT_TEST_SUITE,),
        metrics=(
            MetricObservation(
                metric_id="quality-gate-pass-rate",
                value=0.5 + step_index / 2,
                source_id=f"quality-gate-step-{step_index}",
                protected=False,
                external=True,
            ),
        ),
        attempted_change_ids=(f"candidate-{step_index}",),
        admitted_change_ids=(f"candidate-{step_index}",) if admitted else (),
        rejected_change_ids=() if admitted else (f"candidate-{step_index}",),
        regressions=() if admitted else ("attempt-1-failed",),
        rollback_event_ids=() if admitted else ("rollback-drill-1",),
        usage_by_category=_usage_breakdown(usage),
        usage=usage,
    )


def _usage(multiplier: int = 1) -> ResourceUsage:
    return ResourceUsage(
        cost_usd=0.1 * multiplier,
        compute_units=0.1 * multiplier,
        tokens=10 * multiplier,
        elapsed_seconds=1.0 * multiplier,
        tool_calls=1 * multiplier,
        human_interventions=0,
    )


def _zero_usage() -> ResourceUsage:
    return ResourceUsage(
        cost_usd=0.0,
        compute_units=0.0,
        tokens=0,
        elapsed_seconds=0.0,
        tool_calls=0,
        human_interventions=0,
    )


def _usage_breakdown(execution: ResourceUsage) -> ResourceUsageBreakdown:
    return ResourceUsageBreakdown(
        execution=execution,
        search=_zero_usage(),
        evaluation=_zero_usage(),
        judging=_zero_usage(),
        human=_zero_usage(),
    )


def _budget() -> ResourceBudget:
    return ResourceBudget(
        cost_usd=10.0,
        compute_units=10.0,
        tokens=1000,
        elapsed_seconds=100.0,
        tool_calls=100,
        human_interventions=10,
    )
