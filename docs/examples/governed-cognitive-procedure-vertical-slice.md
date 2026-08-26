# Governed Cognitive-Procedure Vertical Slice

## What This Demonstrates

This deterministic, offline example exercises the governed cognitive records added in
workspace format 0.3 through their public application boundary. It uses a stateless
`CognitiveOrchestrationService` and `ResearchCoordinator` with the runtime's
`TransactionCoordinator`; every reported outcome is projected from the resulting
typed domain object, transaction decision, or workspace verification result.

The fixed scenario demonstrates:

- verified, self-reported, and unknown capability evidence;
- two same-family model actors with different prompt strategies, without claiming
  independence;
- a bounded collaboration challenge after a declared topology edge is disabled;
- one retained invalid procedure compilation and one valid compilation bound to a
  `ProgressPlan`;
- all four guidance conditions;
- a two-model by two-harness evaluation grid with both available and unavailable
  generation metadata;
- a registered deterministic toy validator that hashes declared bytes without executing
  them, including rejection of a tampered high-reward artifact as promotion evidence; and
- source verification, workspace 0.3 export, fresh import, and idempotent replay.

The fixtures are architecture demonstrations, not scientific observations or model
quality evidence. They do not call a model API, network, GPU, subprocess, provider, or
optional dependency, and they grant no execution or protected-evaluation authority.

## Run It

Install the project and run from the repository root with a path that does not yet
exist:

```powershell
python examples/governed_cognitive_procedure_vertical_slice.py `
  --root .example-governed-cognitive-procedure `
  --json
```

The command creates the source workspace at the supplied root and an imported replay
workspace beneath `imported`. It prints exactly one stable JSON object and exits zero
after all assertions and integrity checks succeed. The root path is deliberately
absent from the JSON, so running the example against two different empty roots yields
equal objects.

Important report fields include:

- `capabilities`: the three evidence states and their actual capability dispositions;
- `diversity.independent`: `false`, alongside the shared model family and different
  prompt strategies that justify that result;
- `collaboration`: the accepted topology operation and bounded challenge receipt;
- `invalid_compilation` and `valid_compilation`: retained compiler report status and
  finding evidence;
- `valid_binding`: the accepted binding, exact compilation ID, and progress-plan ID;
- `guidance` and `model_harness`: the actual admitted cell inventories;
- `invalid_reward`: the accepted history decision, observed high value, checker-derived
  expected/actual artifact hashes, invalid assessment status, and empty
  promotion-evidence result; and
- `workspace`: the source/import integrity results and replay outcome.

## Trust Boundary

The script uses bounded synthetic inputs and retains them through normal governed
proposals. It does not infer that prompt diversity is agent independence, treat a high
reward as correctness, turn an invalid compilation into a plan, or bypass the
transaction coordinator during import. Workspace replay validates the same retained
audit identity and projections rather than copying authoritative 0.3 rows directly.
