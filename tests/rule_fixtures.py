from __future__ import annotations

from datetime import UTC, datetime

from super_scientist.domain.behavioral_rules.models import (
    BehavioralRuleVersion,
    ConflictClassification,
    OverlapClassification,
    RecommendationDisposition,
    ReviewerAssessment,
    ReviewerRole,
    RuleAction,
    RuleAuthority,
    RuleIncident,
    RuleIncidentKind,
    RuleRegressionCase,
    RuleStatus,
)
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.domain.improvement.classification import VerificationLevel
from super_scientist.domain.improvement.models import (
    ActorRelationship,
    AssessmentOutcome,
    AssessmentProvenance,
)
from super_scientist.domain.primitives import sha256_hex

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
POLICY_HASH = sha256_hex(b"task-9-policy")


def actor(actor_id: str, kind: ActorKind = ActorKind.HUMAN) -> ActorIdentity:
    model_fields: dict[str, str] = {}
    if kind is ActorKind.MODEL:
        model_fields = {
            "provider_id": f"provider-{actor_id}",
            "model_id": f"model-{actor_id}",
            "configuration_hash": sha256_hex(f"configuration-{actor_id}".encode()),
        }
    return ActorIdentity(
        actor_id=actor_id,
        kind=kind,
        created_at=NOW,
        **model_fields,
    )


def incident(
    incident_id: str,
    *,
    reporter: ActorIdentity | None = None,
) -> RuleIncident:
    return RuleIncident(
        incident_id=incident_id,
        incident_kind=RuleIncidentKind.VERIFIED_FAILURE,
        summary=f"Retained failure {incident_id}",
        evidence_ids=(f"evidence-{incident_id}",),
        observed_at=NOW,
        reported_by=reporter or actor("incident-reporter"),
        recorded_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )


def rule(
    rule_version_id: str = "rule-1-v1",
    *,
    rule_id: str = "rule-1",
    semantic_version: str = "1.0.0",
    incidents: tuple[str, ...] = ("incident-1",),
    statement: str = "Require a retained source before changing canonical behavior.",
    triggers: tuple[str, ...] = ("a canonical behavior change is proposed",),
    required_behavior: tuple[str, ...] = ("retain the motivating incident",),
    prohibited_behavior: tuple[str, ...] = ("delete incident history",),
    exceptions: tuple[str, ...] = (),
    decision_boundary: str = "Apply whenever canonical behavior could change.",
    status: RuleStatus = RuleStatus.UNDER_REVIEW,
    creator: ActorIdentity | None = None,
    approver: ActorIdentity | None = None,
    supersedes: tuple[str, ...] = (),
) -> BehavioralRuleVersion:
    return BehavioralRuleVersion(
        rule_version_id=rule_version_id,
        rule_id=rule_id,
        semantic_version=semantic_version,
        title="Retain incident-backed rule history",
        canonical_statement=statement,
        rationale="Concrete incidents are authoritative and cannot be replaced by summaries.",
        authority=RuleAuthority.PROJECT,
        scope=("behavioral-rule governance",),
        triggers=triggers,
        required_behavior=required_behavior,
        prohibited_behavior=prohibited_behavior,
        exceptions=exceptions,
        decision_boundary=decision_boundary,
        precedence_rule_ids=(),
        source_incident_ids=incidents,
        evidence_ids=tuple(f"evidence-{item}" for item in incidents),
        counterexamples=("A speculative suggestion without an incident is insufficient.",),
        regression_test_ids=tuple(f"test-{item}" for item in incidents),
        retrieval_terms=("incident retention", "rule consolidation"),
        aliases=("retain-rule-history",),
        related_rule_ids=(),
        conflict_rule_ids=(),
        supersedes_rule_version_ids=supersedes,
        status=status,
        creator=creator or actor("rule-author"),
        approver=approver,
        created_at=NOW,
        approved_at=NOW if approver is not None else None,
        governing_policy_hash=POLICY_HASH,
    )


def assessment(
    role: ReviewerRole,
    *,
    reviewer: ActorIdentity | None = None,
    assessment_id: str | None = None,
    proposal_id: str = "proposal-rule-1",
    rule_version_ids: tuple[str, ...] = ("rule-1-v1",),
    incident_ids: tuple[str, ...] = ("incident-1", "incident-2"),
    overlap: OverlapClassification | None = OverlapClassification.PARTIAL_OVERLAP,
    conflict: ConflictClassification | None = None,
    action: RuleAction = RuleAction.ACCEPT_WITH_REVISION,
    uncertainty: tuple[str, ...] | None = None,
) -> ReviewerAssessment:
    reviewer_actor = reviewer or actor(f"reviewer-{role.value.lower()}", ActorKind.MODEL)
    identifier = assessment_id or f"assessment-{role.value.lower()}"
    return ReviewerAssessment(
        assessment_id=identifier,
        role=role,
        provenance=AssessmentProvenance(
            actor=reviewer_actor,
            actor_version=f"{identifier}-v1",
            category=VerificationLevel.INDEPENDENT_DETERMINISTIC_CHECK,
            deterministic_or_learned="DETERMINISTIC",
            proposer_relationship=ActorRelationship.INDEPENDENT,
            assumptions=("All supplied incidents and rule versions are immutable.",),
            evidence_ids=tuple(f"evidence-{item}" for item in incident_ids),
            checks_run=(f"{role.value.lower()}-review",),
            limitations=("The assessment does not claim formal completeness.",),
            result=AssessmentOutcome.PASSED,
            meaningful_confidence=None,
            assessed_at=NOW,
            governing_policy_hash=POLICY_HASH,
        ),
        proposal_id=proposal_id,
        rule_version_ids=rule_version_ids,
        incident_ids=incident_ids,
        overlap=overlap,
        conflict=conflict,
        findings=(f"{role.value} finding must be retained.",),
        candidate_statement="Keep all incidents and introduce an explicit decision boundary.",
        scope=("behavioral-rule governance",),
        triggers=("a recurrence or conflict is observed",),
        exceptions=("Use the measured separating condition when failures conflict.",),
        counterexamples=(f"Counterexample supplied by {role.value}.",),
        regression_test_ids=tuple(f"test-{item}" for item in incident_ids),
        recommended_action=action,
        uncertainty=uncertainty
        if uncertainty is not None
        else (f"{role.value} uncertainty must remain visible.",),
    )


def five_assessments(
    *,
    conflict: ConflictClassification | None = None,
) -> tuple[ReviewerAssessment, ...]:
    return tuple(
        assessment(
            role,
            conflict=conflict if role is ReviewerRole.CONFLICT else None,
            action=(
                RuleAction.ESCALATE_TO_HUMAN
                if role is ReviewerRole.ADVERSARIAL
                else RuleAction.ACCEPT_WITH_REVISION
            ),
        )
        for role in ReviewerRole
    )


def dispositions(
    assessments: tuple[ReviewerAssessment, ...],
) -> tuple[RecommendationDisposition, ...]:
    return tuple(
        RecommendationDisposition(
            assessment_id=item.assessment_id,
            recommended_action=item.recommended_action,
            accepted=item.role is not ReviewerRole.ADVERSARIAL,
            explanation=(
                "Accepted because the recommendation improves the canonical boundary."
                if item.role is not ReviewerRole.ADVERSARIAL
                else "Rejected as the explicit boundary and regressions resolve the escalation."
            ),
        )
        for item in assessments
    )


def regression(
    regression_case_id: str,
    incident_id: str,
    *,
    rule_version_id: str = "rule-1-v2",
    creator: ActorIdentity | None = None,
) -> RuleRegressionCase:
    return RuleRegressionCase(
        regression_case_id=regression_case_id,
        rule_version_id=rule_version_id,
        incident_ids=(incident_id,),
        test_id=f"test-{incident_id}",
        scenario=f"Reproduce {incident_id} under its retained conditions.",
        expected_behavior=f"The canonical boundary handles {incident_id} explicitly.",
        created_by=creator or actor("integrator"),
        created_at=NOW,
        governing_policy_hash=POLICY_HASH,
    )
