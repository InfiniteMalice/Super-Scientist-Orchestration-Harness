# Super Scientist Orchestration Harness

The project is currently an **epistemic-kernel vertical slice**, not a complete
scientific research system. It provides typed evidence and claim records, deterministic
proposal admission, SQLite-backed transactions, content-addressed local artifacts, an
active governance-policy hash, a tamper-evident audit chain, and a stable local CLI.
It does not claim to establish scientific truth.

## Install

Python 3.12 or newer is required. A core installation has no model SDK, GPU, training,
or paid API dependency:

```bash
python -m venv .venv
```

Activate that environment in Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or activate it in a POSIX shell:

```bash
source .venv/bin/activate
```

Then install and verify the CLI using the activated environment:

```bash
python -m pip install .
scientist-harness --help
```

Install the development tools only when running tests or the repository quality gate:

```bash
python -m pip install -e ".[dev]"
```

## Minimal CLI Use

Initialize a local kernel workspace and verify its empty audit chain:

```bash
scientist-harness init --root .kernel --json
scientist-harness audit verify --root .kernel --json
```

The second command reports `valid: true` and `checked_events: 0`. See
`docs/examples/kernel-vertical-slice.md` for the deterministic offline evidence and
claim example.

When `--json` is present, command parsing, required-option, option-value, and input-path
failures also return exactly one schema-version-1 JSON error envelope with exit status
2. Without `--json`, Typer's normal human help and error rendering is unchanged.

## Security Boundaries

The kernel runtime has no model, network, or arbitrary shell authority. Retrieved or
local evidence content remains untrusted data. Artifact paths are content-derived and
must remain beneath a private local artifact root; static traversal, symlink, and
Windows reparse-point escapes fail closed. Submitted evidence begins unverified; the
application service rehashes it before projecting a hash-verified record. Audit
verification reconciles transactions, projections, history, and artifact bytes.
Integrity errors stop the affected operation. See `SECURITY.md` for details.

The quality command is a development operation, separate from scientific runtime
authority. It executes only the eight source-controlled checks in its fixed registry:

```bash
scientist-harness quality-gate
```

There are no command, path, selection, skip, or threshold options. `--json` changes only
reporting and records every fixed check and result.

## Roadmap

Later subsystems may add hypotheses, experiments, orchestration, source-linked memory,
complete research runs, provider integrations, and separately governed model or
training support. None of those capabilities is available in this kernel slice.

The implemented architecture, governance, claim lifecycle, and security limits are
documented in `ARCHITECTURE.md`, `GOVERNANCE.md`, `CLAIM_LEDGER.md`, and `SECURITY.md`.
