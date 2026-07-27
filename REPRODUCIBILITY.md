# Reproducibility

## Scope

This document reproduces the local software behavior of Super Scientist Orchestration
Harness 0.2.0. It does not reproduce S21-S29, validate their reported results, establish
scientific truth, or show safe recursive self-improvement. The governed-adaptation
example is an implemented deterministic test scenario over synthetic SSOH data.
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
python examples/kernel_vertical_slice.py --workspace .repro-kernel
python examples/governed_adaptation_vertical_slice.py --workspace .repro-governed
```

The governed example prints one canonical JSON object. It must report policy versions
`[1,2]`, a rejected false finish, preservation of the failed hypothesis, first and
second harness statuses `BENCHMARK_SPECIFIC` and `ADMITTED`, a valid audit, and exactly
21 completed ordered steps. A second run in another empty directory must print the
same bytes. Reusing a populated example directory is outside this clean-run contract.

The walkthrough and the semantic meaning of every step are documented in
`docs/examples/governed-adaptation-vertical-slice.md`.

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

The fixed repository gate independently runs formatting, lint, strict types, tests,
security scanning, dependency auditing, build checks, and package checks:

```powershell
scientist-harness quality-gate
```

For release packaging, build both distributions, inspect them, install the wheel into a
fresh environment, import the package from outside the repository, and run both example
scripts against empty workspaces. `python -m build`, `python -m twine check dist/*`, and
an isolated `python -m pip install <wheel>` are the corresponding standard commands.

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
limitations, and no-code-reuse boundaries for S21-S29 are in
`docs/sources/source-register.yaml`. Every one is marked `not_reproduced`.
`docs/research-inspirations.md` explains which ideas are adapted and which mechanisms
are project-original synthesis. The S29 site is unlicensed, not peer reviewed, reports
results that were not independently verified, and contributes no code or hidden task
assumption to this repository.
