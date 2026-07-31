# Governed-Adaptation Vertical Slice

## What This Demonstrates

The 0.2.0 example executes the exact 21-step governed-adaptation scenario over
independently authored synthetic SSOH thermal-chamber and equipment-incident data. It
exercises real typed contracts, a real V1-to-V2 coordinator transaction, fixed
deterministic simulation, governed decisions, content-addressed artifacts, and complete
workspace verification.

The scenario is an offline deterministic fake for testing architecture and authority.
It is not a scientific experiment, benchmark reproduction, safe recursive
self-improvement result, general-improvement claim, or compatibility test for S29. It
uses no model, API, network, GPU, training framework, imported source code, subprocess,
or arbitrary shell.

## Run It

Install the project and run from the repository root with a new workspace:

```powershell
python examples/governed_adaptation_vertical_slice.py `
  --root .example-governed-adaptation
python -m super_scientist.cli.main audit verify `
  --root .example-governed-adaptation `
  --json
```

The example and the subsequent ordinary CLI audit use the same root. Each command
prints exactly one JSON object. A successful example result has:

```json
{
  "audit_valid": true,
  "failed_hypothesis_preserved": true,
  "false_finish_rejected": true,
  "first_harness_candidate_status": "BENCHMARK_SPECIFIC",
  "policy_versions": [1, 2],
  "second_harness_candidate_status": "ADMITTED"
}
```

The actual object also contains all 21 completed `steps`. Run against a second empty
directory to verify byte-identical output. The SQLite and artifact paths do not enter
the report. The CLI audit exits zero with `success=true` and `data.valid=true`, proving
that the example's `scientist-harness.db` and `artifacts` layout is the public workspace
format rather than a parallel example-only format.

## Ordered Proof

| Step | Stable code | Implemented observation |
| ---: | --- | --- |
| 1 | `initialize_v1_kernel` | Registers and activates the schema-version-1 bootstrap policy in an empty SQLite workspace. |
| 2 | `approve_v1_to_v2_transition` | Submits an explicit V1-to-V2 transition with a dedicated run, complete measurement, passed independent audit, human approval, and V1 rollback target. V1 remains the governing authority for that transaction. |
| 3 | `add_synthetic_source_evidence` | Stores the SSOH-authored incident note content-addressably and admits a hash-verified immutable evidence record under V2. |
| 4 | `create_research_run_and_progress_plan` | Admits a strict research run, its two-subtask progress plan, and the initial run event with explicit budgets and a final validator. |
| 5 | `propose_competing_thermal_hypotheses` | Retains bounded-heating and runaway-heating alternatives with assumptions, predictions, and falsification conditions. |
| 6 | `register_builtin_thermal_simulator` | Selects only the fixed `thermal-chamber-v1` deterministic simulator with a strict numeric input and bounded resource metadata. No record supplies executable code. |
| 7 | `record_predictions_and_falsification_criteria` | Executes the fixed model and checks the retained peak-temperature boundaries against both hypotheses. |
| 8 | `construct_and_validate_natural_evidence_trail` | Binds exact spans from the incident artifact to structural locations and SHA-256 hashes. Trail coherence remains evidence, not proof. |
| 9 | `validate_partial_progress` | Calculates official validated progress separately from provisional work; the transfer subtask remains provisional. |
| 10 | `reject_false_finish` | Detects the premature completion assertion and returns `FALSE_FINISH` because the independent final condition is unsatisfied. |
| 11 | `preserve_failed_hypothesis_and_revision` | Keeps the falsified runaway hypothesis and creates an explicit immutable revision instead of erasing the failure. |
| 12 | `record_incident_and_propose_rule` | Retains two synthetic sensor incidents and proposes an explicit sensor-disagreement boundary rule. |
| 13 | `import_five_reviewer_roles` | Creates one strict, independent deterministic assessment for each of the five reviewer roles; reviewers do not mutate canonical state. |
| 14 | `consolidate_canonical_boundary_rule` | Classifies semantic overlap and builds an explicit canonical candidate diff with the retained decision boundary. |
| 15 | `preserve_incident_regression_cases` | Keeps both incidents as separate regression cases linked to the candidate rule. |
| 16 | `link_rule_and_verify_source_mapping` | Links the rule to a behavior, links that behavior to a source symbol, and uses Python AST inspection to verify the symbol exists. The handbook remains non-authoritative. |
| 17 | `compare_matched_budget_harness_candidate` | Constructs baseline and candidate variants with exactly matched evaluation budgets and isolated campaign partitions. |
| 18 | `reject_benchmark_specific_discovery_gain` | Records a discovery gain without transfer and classifies it `BENCHMARK_SPECIFIC`; it cannot be admitted as a general improvement. |
| 19 | `admit_held_out_transfer_candidate` | Produces `ADMITTED` only for the second candidate report after held-out transfer, safety/regression checks, independent audit, accepted measurement, and human authority. Admission remains relative to this declared fixture. |
| 20 | `export_self_improvement_measurement_report` | Serializes the transition measurement canonically, stores it content-addressably, and verifies its bytes. It contains complete trajectory, failure, regression, rollback, and budget evidence. |
| 21 | `verify_workspace_and_mixed_policy_audit` | Reconciles the whole database, artifacts, projections, transactions, and audit chain and confirms the retained policy history is exactly `(1, 2)`. |

## Authority Boundaries

Steps 1-20 admit their governed records through repositories and the transaction
coordinator into the durable SQLite and content-addressed-artifact workspace. Step 21
opens a fresh repository boundary, verifies the whole workspace, and derives every
reported outcome from durable transactions, projections, artifacts, and audit history.

The first harness result cannot authorize the second. The failed hypothesis, provisional
progress, incidents, dissent-capable assessments, negative observation boundaries, and
rollback evidence remain represented instead of being summarized away. Human actors in
the fixture are typed independent identities; their presence demonstrates enforcement
of a contract, not real-world human review.

For full environment, testing, packaging, and repeatability instructions, see
`REPRODUCIBILITY.md`. For attacker capabilities and protected-data limits, see
`THREAT_MODEL.md`.
