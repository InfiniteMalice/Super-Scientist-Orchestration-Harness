# Reproducibility

## Scope

This document reproduces the local software behavior of Super Scientist Orchestration
Harness 0.3.0. It does not reproduce S21-S35, validate their reported results, establish
scientific truth, or show safe recursive self-improvement. The governed-adaptation and
governed cognitive/procedure examples are deterministic test scenarios over synthetic
SSOH data.
Built-in simulators and adapter-training records are deterministic fakes or bounded
interfaces, not live scientific instruments or model training.

## Environment

Use Python 3.12 or newer in a clean local virtual environment. Core execution requires
no API credential, model SDK, network call, GPU, training framework, or external
service. Installing the project and development checks may access the configured Python
package index.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The POSIX activation equivalent is `source .venv/bin/activate`.

## Deterministic Examples

Run the original kernel example and the governed-adaptation example in separate empty
workspaces:

```powershell
python examples/kernel_vertical_slice.py
python examples/governed_adaptation_vertical_slice.py --root .repro-governed
python examples/governed_cognitive_procedure_vertical_slice.py --root .repro-cognitive --json
```

The governed example prints one canonical JSON object. It must report policy versions
`[1,2]`, a rejected false finish, preservation of the failed hypothesis, first and
second harness statuses `BENCHMARK_SPECIFIC` and `ADMITTED`, a valid audit, and exactly
21 completed ordered steps. A second run in another empty directory must print the
same bytes. Reusing a populated example directory is outside this clean-run contract.

The walkthrough and the semantic meaning of every step are documented in
`docs/examples/governed-adaptation-vertical-slice.md`.

The cognitive example must report verified, self-reported, and unknown capability
evidence; same-model diversity without independence; a topology update; a bounded
challenge; accepted compilation records with domain statuses `INVALID` and `VALID`;
one accepted valid progress binding; all four guidance conditions; a 2-model by
2-harness grid; available and unavailable generation metadata; and an accepted
invalidating high reward with domain status `INVALID` and `promotion_evidence: false`.
The same summary must report `verified: true`,
`import_verified: true`, and successful exact replay. The deterministic toy validator
is a `TOOL` actor that compares bounded artifact bytes with the declared SHA-256 digest;
it does not infer validation from a caller's success flag or execute artifact content.
Run `python -m pytest tests/e2e/test_governed_cognitive_procedure_vertical_slice.py -q`
to verify the output contract.

Separately, the procedure service rejects an attempt to bind an accepted `INVALID`
compilation with transaction code `INVALID_PROCEDURE`. Run `python -m pytest
tests/integration/application/test_procedure_service.py::test_binding_rejects_invalid_compilation_without_projecting_plan
-q` to verify that rejection and the absence of a progress projection.

Run the example against two different empty roots and compare canonical JSON bytes.
Path, event time, database, and artifact-store locations are excluded from the stable
summary. The installed-wheel smoke test builds the wheel, creates a fresh environment,
loads every project module from the installed wheel rather than `src`, and runs the
installed example through verify, export, import, and replay.

## Verification

Run the focused release proof:

```powershell
python -m pytest tests/unit/docs tests/unit/test_package.py `
  tests/integration/application/test_workspace_exchange.py tests/e2e -v
```

Run the complete repository suite with branch coverage:

```powershell
python -m pytest --cov=super_scientist --cov-branch `
  --cov-report=term-missing --cov-fail-under=90
```

The fixed repository gate independently runs exactly nine checks: formatting, lint,
strict types, tests, security scanning, dependency auditing, build, package inspection,
and fresh-wheel installation:

```powershell
scientist-harness quality-gate
```

For release packaging, build both distributions, inspect them, install the wheel into a
fresh environment, import the package from outside the repository, and run both example
scripts against empty workspaces. `python -m build`, `python -m twine check dist/*`, and
an isolated `python -m pip install <wheel>` are the corresponding standard commands.

## Cognitive Evaluation Matching

An evaluation is reproducible only when its protocol identifier/version/hash, condition,
model, harness, partition, declared budget, output artifact, verifier result, trace, and
reward assessment resolve to the same accepted evidence chain. Currentness is exact:
each receipt must bind the accepted proposal hash, audit event identifier, audit event
hash, governing policy, and required earlier audit order. A later replacement in the
same source-snapshot family makes the older receipt stale.

Generation metadata uses three explicit states. `AVAILABLE` requires a value plus exact
retained evidence. `UNAVAILABLE` requires neither a value nor fake evidence. `UNKNOWN`
records that the workflow cannot determine availability. Missing metadata is not zero,
false, or an empty observation. Model-by-harness analysis retains confounds rather than
normalizing them away; cross-protocol, ambiguous, surplus, missing, stale, or invalid
reward evidence produces `UNMATCHED_EVALUATION` or the more specific fixed rejection.
Run `python -m pytest tests/integration/application/test_harness_eval_extensions.py
tests/adversarial/test_trace_reward_tampering.py -q` to verify these rules.

## Workspace Exchange Reproduction

`export_workspace()` requires an integrity-valid source workspace. Its
schema-version-1 bundle has canonical hashes, stable sorting, explicit replay order,
rebuildable projection expectations, and digest-only artifact references.
`import_workspace()` requires caller-supplied source and target artifact stores and
submits every retained proposal through stable coordinator intents.

A conflict-free import into an empty target must re-export exactly the same
`WorkspaceExport`. Reimporting identical content must replay. Changing proposal content
without changing stable identity must yield an audited `IDEMPOTENCY_CONFLICT`; it must
not mutate the earlier record. The integration suite above exercises all three cases,
schema/hash tampering, and the protected-safe object-graph boundary.

## Variability And Evidence Retention

SQLite and artifact locations are local operational details and are deliberately absent
from canonical example output and workspace bundles. Trusted event times in the example
come from a deterministic clock. Production callers supply their own clock and storage,
so byte-for-byte database files are not a portability promise.

Retain the interpreter version, installed dependency inventory, test output, coverage
report, source commit, built wheel hash, command line, and resulting workspace only when
those artifacts are appropriate for the data involved. Do not archive secrets,
protected holdouts, private evidence, or live filesystem paths in a public report.

## Source Boundary

The exact versions, licenses, repository commits, proposal/evidence distinctions,
limitations, and no-code-reuse boundaries for S21-S35 are in
`docs/sources/source-register.yaml`. Every one is marked `not_reproduced`.
`docs/research-inspirations.md` explains which ideas are adapted and which mechanisms
are project-original synthesis. The S29 site is unlicensed, not peer reviewed, reports
results that were not independently verified, and contributes no code or hidden task
assumption to this repository.

S30-S35 contribute attributed design signals only. Their reported paper, benchmark,
training, modularity, cohort, or agentic-reasoning results were not reproduced, and none
of their source code was imported or reused. S34 code was unavailable until acceptance.
