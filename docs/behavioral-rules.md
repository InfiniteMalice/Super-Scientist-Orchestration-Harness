# Governed behavioral rules

Behavioral rules are immutable, incident-backed versions of canonical operating behavior. They
are not free-form memory and do not gain authority because a model repeats them, reviewers agree,
or a newer version exists. Durable changes pass through retained incidents, five independent
reviews, a measured candidate, independent human approval, and one constrained integrator.

## Retained records

The rule history is append only:

- `RuleIncident` retains a verified failure, human review, reproduced bug, workflow failure,
  security incident, quality-gate failure, repeated mistake, or validated counterexample with its
  evidence, reporter, time, and governing policy.
- `BehavioralRuleVersion` retains semantic version, authority, scope, triggers, required and
  prohibited behavior, exceptions, decision boundary, precedence, incidents, evidence,
  counterexamples, regression tests, retrieval terms, related/conflicting rules, supersession,
  actors, status, and policy.
- `ReviewerAssessment` retains one of the five required roles (`SEMANTIC`, `CONFLICT`,
  `ABSTRACTION`, `ADVERSARIAL`, or `VERIFICATION`), complete assessment provenance, findings,
  overlap/conflict classification, candidate wording, counterexamples, uncertainty, tests, and a
  recommendation.
- `RuleConsolidationDecision` records every consumed assessment and incident, the action and
  boundary, accepted and rejected recommendations, preserved dissent, result, integrator, time,
  and policy.
- `RuleRegressionCase` binds a concrete scenario and expected behavior to retained incidents and
  the resulting rule version.

Only a rule head is mutable. It is a rebuildable projection of an accepted consolidation; the
authoritative incidents, versions, assessments, decisions, and regression cases are never edited
or deleted.

## Governed workflow

The fixed transaction router accepts exactly four behavioral-rule proposal kinds:

1. `record_rule_incident` records one immutable, evidence-backed incident.
2. `propose_behavioral_rule` records a non-active `PROPOSED` or `UNDER_REVIEW` version. It cannot
   carry an approver or approval timestamp as if it were already canonical.
3. `import_reviewer_assessment` imports one typed assessment tied to the exact accepted rule
   proposal, retained rule versions, and retained incidents. Exact reimports are idempotent;
   changed content under an existing assessment ID is an audited `IDEMPOTENCY_CONFLICT`.
4. `consolidate_behavioral_rule` consumes all five roles and produces the canonical decision. A
   rule-producing action also appends the candidate and regression cases and advances the rule
   head atomically. `REJECT` and `ESCALATE_TO_HUMAN` retain the decision without projecting a
   candidate or head.

Every proposal uses the exact fixed classification: persistent `BEHAVIORAL_RULE`,
`HUMAN_IN_LOOP`, `INDEPENDENT_DETERMINISTIC_CHECK`, `PRIMARY_SOURCE`, and
`EXTRINSIC_GROUNDED_EXPERIENCE`. Governance V1, a missing V2 behavioral-rule requirement, a
different classification, insufficient grounding, or a non-human/dependent approval fails
closed. Policy-required protected evaluation and rollback metadata are enforced.

Consolidation additionally binds an accepted `SelfImprovementMeasurementRecord`, its passed
`EvaluatorAuditRecord`, the exact candidate and rollback versions, the active policy, the
integrator, the human decision authority, protected metrics when required, and an independent
auditor. A proposal cannot use measurement or audit records belonging to another change.

## Duplicate, conflict, and recurrence handling

`classify_overlap()` deterministically distinguishes exact duplicate, semantic duplicate,
narrower instance, broader reformulation, partial overlap, same-trigger/different-action,
different-trigger/same-action, and non-redundant rules. Exact duplicates are rejected. Semantic
duplicates must enter review instead of silently creating another rule.

`build_candidate_diff()` canonicalizes assessment order by reviewer role and binds each
recommendation to one explained disposition. It preserves every finding and every uncertainty;
rejected recommendations remain visible as dissent.

A contradiction is not resolved by newest-rule-wins, majority vote, or arbitrary priority. The
candidate must retain both motivating incidents, name a separating variable, encode an explicit
precondition or exception boundary, and retain regression cases for every contradictory failure.
Conflicting reviewer classifications cannot be collapsed into a single answer.

A recurrence is evidence that abstraction, trigger, retrieval, enforcement, or scope needs
repair. The consolidation names at least one such repair, preserves all prior and recurrence
incidents, and binds each recurrence to a regression case. Supersession adds history; it never
replaces or deletes it.

## Independent review and capability limits

All five reviewer roles must be present exactly once. Reviewers are pairwise independent and the
integrator is independent of every reviewer. Independence is recomputed from actor ID and model
provider, model, adapter, and configuration identity; aliases, shared model/configuration
fingerprints, missing model configuration, and declared-but-not-real independence are rejected.
The independent human approver must also be independent of the proposer and every incident,
rule, and reviewer authority actor involved in the mutation.

Reviewer capabilities can read retained incidents/rules and append only typed assessments. They
have no rule-head, governance, quality-registry, protected-test, threshold, or promotion writer.
Rule proposers cannot update heads. The integrator capability is the only rule capability that can
append a candidate, decision, and regression cases and update the rule head; it still cannot
write governance or the quality registry. The transaction coordinator owns the database unit of
work and audit event, so a projection failure rolls back the entire accepted mutation.

## Replay and recovery

Whole-workspace verification reconstructs behavioral-rule state in audit order. For every
historically accepted rule transaction it reruns the same four live handlers against only the
policy and records available at that point in history. It then independently rebuilds incidents,
versions, assessments, decisions, regression cases, and heads and compares them with storage.

Wrong historical authority, correlated reviewers, missing predecessors, changed stable keys,
forged measurement/audit bindings, extra or missing records, and rewound or otherwise tampered
heads therefore invalidate the workspace before the next mutation. Recovery requires restoring
the authoritative transaction/audit history and rebuilding its projections; editing immutable
rule history is not a supported repair.
