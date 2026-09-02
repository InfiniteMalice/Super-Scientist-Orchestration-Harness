# Super Scientist Orchestration Harness

Version 0.3.0 is a **governed cognitive-orchestration vertical slice**, not a complete
scientific research system. It retains the 0.2.0 governed-adaptation surface and adds
typed capability, cohort, collaboration, procedure, guidance, model-by-harness, trace,
and reward evidence. The shared transaction coordinator admits every durable change,
and the cognitive plane has no claim, policy, promotion, execution, or protected-answer
authority. Passing admission does not establish scientific truth.

## Why This Exists

Automated scientific work needs more than a capable model. It needs a durable place to
store evidence, a controlled path from proposal to committed state, and a way to tell
the difference between "a model said this" and "the system admitted this under known
rules."

This repository is the beginning of that harness. Its core rule is:

> Models, tools, and humans may propose. The harness owns committed evidence, claim
> state, validation, provenance, governance policy, audit history, and rollback
> boundaries.

Use this repo when you want to build or inspect the foundation for a research workflow
where scientific claims are decomposed into typed records, linked to exact evidence
spans, admitted by deterministic checks where possible, and rejected with durable reasons
when they do not satisfy the current policy. The first slice is intentionally local and
model-free so the integrity boundary can be tested before adding orchestration,
providers, memory, experiments, or training.

Do not use it as a finished autonomous scientist, a truth oracle, a benchmark
reproduction, or a generic agent framework. It is a small kernel for trustworthy
research state, not a claim that automation can replace scientific judgment.

## Design Philosophy

- **Proposal is not commitment.** Model output, CLI input, and tool output are treated
  as untrusted proposals until admitted.
- **Evidence stays external and inspectable.** Raw evidence bytes are content-addressed;
  summaries and claims never replace the source artifact.
- **State changes are transactional.** Accepted and rejected proposals are recorded with
  policy attribution, idempotency handling, and a tamper-evident audit chain.
- **Authority is separated.** A proposer cannot approve its own proposal, and agreement
  from the same model family or adapter is not treated as independent validation.
- **Governance changes are protected.** The current slice rejects policy replacement
  rather than silently weakening the active rules.
- **Learning is quarantined.** Future QLoRA or harness-evolution work must remain
  procedural, evaluable, reversible, and unable to rewrite its own admission criteria.
- **Claims remain conditional.** Passing implemented checks means a proposal satisfied
  declared constraints. It does not mean the underlying scientific proposition is true.

## What You Can Do Today

- Initialize a local kernel workspace.
- Add local evidence through the CLI and have its bytes rehashed before admission.
- Propose atomic claims and inspect claim history.
- Exercise deterministic admission failures such as self-approval, missing evidence,
  illegal claim transitions, and idempotency conflicts.
- Verify the audit chain, stored policy, projections, and artifact bytes.
- Export an integrity-checked canonical workspace bundle and reconstruct it in an empty
  workspace through the same coordinator intents.
- Record verified, self-reported, and unknown capabilities; form a bounded cohort;
  retain peer contributions and topology events; and diagnose diversity without
  treating diversity as reviewer independence.
- Compile accepted method evidence into immutable `VALID` or `INVALID` procedure
  history, retain either status in an accepted transaction, and bind only a `VALID`
  compilation to one canonical `ProgressPlan`. Attempting to bind an `INVALID`
  compilation is rejected with transaction code `INVALID_PROCEDURE`.
- Retain all four guidance conditions, exact model-by-harness cells, generation-metadata
  availability, matched execution traces, and reward-hacking findings without granting
  those records promotion authority.
- Inspect one of the 18 fixed cognitive record kinds through the read-only CLI after
  whole-workspace integrity verification.
- Run the deterministic 21-step governed-adaptation example with synthetic
  thermal-chamber and equipment-incident data.
- Run the model-free governed cognitive/procedure example, including verify, export,
  fresh import, and exact replay.
- Run the repository quality gate that CI uses.

Run this focused command to verify the `INVALID` domain-status and rejection-code
distinction:

```powershell
python -m pytest `
  tests/integration/application/test_procedure_service.py::test_invalid_compilation_is_history_but_creates_no_plan `
  tests/integration/application/test_procedure_service.py::test_binding_rejects_invalid_compilation_without_projecting_plan `
  tests/integration/application/test_harness_eval_extensions.py::test_invalid_reward_is_retained_but_excluded_from_positive_evidence `
  -q
```

See `docs/examples/kernel-vertical-slice.md` for the original byte-compatible kernel
walk-through and `docs/examples/governed-adaptation-vertical-slice.md` for the 0.2.0
offline demonstration.
See `docs/examples/governed-cognitive-procedure-vertical-slice.md` for the 0.3.0
workflow and its explicit limitations.

See the [User Manual](docs/USER_MANUAL.md) for installation, roles, authority limits,
workflow stages, troubleshooting, security boundaries, and LLM assignment guidance.

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
scientist-harness cognitive inspect --root .kernel --kind capability-profile --id profile-1 --json
```

The second command reports `valid: true` and `checked_events: 0`. See
`docs/examples/kernel-vertical-slice.md` for the deterministic offline evidence and
claim example. The inspect command reports `not_found` until an accepted transaction
admits `profile-1`; initialization alone does not create a capability profile. `init`
activates a policy only for a genuinely empty kernel database.
If durable state exists but its active governance pointer is missing, initialization
fails closed; `audit verify` still opens that storage and reports the integrity error.

When `--json` is present, command parsing, required-option, option-value, and input-path
failures also return exactly one schema-version-1 JSON error envelope with exit status
2. Without `--json`, Typer's normal human help and error rendering is unchanged.
Policy file validation reports `INVALID_POLICY`; other command/domain validation reports
`INVALID_ARGUMENT` or an `INVALID_PROPOSAL` decision. The CLI pins Typer 0.19.2 with
Click 8.3.3 so parser translation uses the public Click hierarchy on a dependency line
with no known audited vulnerability. Governance policy files require schema version 1,
valid UTF-8 JSON, and no unknown fields. Externally parsed identities, evidence, claims,
and nested proposal records also reject unknown fields.

Exact retries compare a stored trusted-attempt fingerprint; reusing a key with different
intent content, proposal identity, logical proposer, or proposal kind returns an audited
`IDEMPOTENCY_CONFLICT`. Orphaned governance is reported as storage corruption by ordinary
runtime commands as well as `audit verify`.
Evidence intent identity includes the resolved input path because that path is retained
as authoritative provenance; equal bytes from different paths are therefore distinct
ingestions.

## Security Boundaries

The control plane owns policy checks, transactions, audit, storage, artifacts, and
protected evaluation. The cognitive plane submits typed evidence through a stateless
service and cannot retain the coordinator or its capabilities. The kernel runtime has
no model, network, or arbitrary shell authority. Retrieved or local evidence content
remains untrusted data. Artifact paths are content-derived and
must remain beneath a private local artifact root; static traversal, symlink, and
Windows reparse-point escapes fail closed. Submitted evidence begins unverified; the
application service rehashes it before projecting a hash-verified record. Audit
verification reconciles registered policy authority, transactions, projections,
history, and artifact bytes. Configuration-only model aliases cannot approve one
another, and unsupported proof-bearing claim statuses fail closed for review.
Integrity errors stop the affected operation. See `SECURITY.md` for details.

Workspace exchange serializes only strict schema-versioned policy snapshots,
authoritative proposals and decisions, rebuildable projection expectations, and
content-addressed artifact metadata. Artifact bytes move out of band and are rehashed.
The bundle excludes protected answers, protected-store references, live filesystem
paths, and executable configuration. Import into a nonempty workspace is not a merge:
identical stable intents replay, while changed content under an existing identity
produces an audited conflict.

The quality command is a development operation, separate from scientific runtime
authority. It executes only these nine source-controlled checks, in order: `format`,
`lint`, `types`, `tests`, `security`, `dependencies`, `build`, `package`, and
`wheel-install`:

```bash
scientist-harness quality-gate
```

There are no command, path, selection, skip, or threshold options. `--json` changes only
reporting and records every fixed check and result. `wheel-install` verifies the exact
built wheel in a fresh environment and runs its installed governed cognitive example;
`tests/unit/quality/test_runner.py` verifies the nine-entry registry.

## Sources And Attribution

The architecture is a project-specific synthesis. It is **inspired by** cited research
and development-governance systems, but no cited system is marked as reproduced, and no
source repository compatibility is claimed without tests.

Source metadata, versions consulted, repository commits where available, limitations,
and adoption status are recorded in
[docs/sources/source-register.yaml](docs/sources/source-register.yaml). The narrative
mapping from source ideas to project components is in
[docs/research-inspirations.md](docs/research-inspirations.md).
Use those files as the source of truth for attribution.

The implemented kernel slice most directly adapts these ideas:

| Source | What this repo adapts | Boundary |
| --- | --- | --- |
| [S02] SciConBench and SciConHarness | Atomic scientific conclusion framing and evidence-linked factual checks | This repo does not import benchmark content or reproduce SciConHarness. |
| [S07] HarnessBridge | Raw-record-preserving projected views and typed observation/action boundaries | This repo uses deterministic projections, not learned controllers. |
| [S12] Mnemosyne | Untrusted proposals, deterministic admission, append-only transitions, and effective-state projection | Transaction validity is not scientific truth. |
| [S19] Superpowers | Development workflow discipline: brainstorm, design, plan, TDD, review, verification | It governs development practice, not research conclusions. |
| [S20] RepoQualityGate | Spec-first and quality-gated development discipline | The local quality command is this repo's independent implementation. |

The wider roadmap is informed by sources such as ATLAS for competing hypotheses [S01],
HORMA for source-linked memory [S04], EurekAgent for permissions and budgets [S05],
AgentBeats for agent-neutral evaluation [S06], HIPIF for plan hierarchies [S08],
Socratic agents for falsification pressure [S09], QLoRA for optional procedural adapter
training [S18], and others listed in the register. These are roadmap inspirations unless
the implementation docs explicitly say otherwise.

S30-S35 inform the 0.3.0 method/procedure, cognitive-cohort, capability-grounding,
guidance, trace, and reward contracts. All six entries are `not_reproduced`; no source
code is imported or reused. In particular, Co-RL agreement [S33] is not truth,
DeAR-style routing [S34] does not decentralize governance, and LEGO-RL [S35] contributes
trace-fidelity concepts without live reinforcement learning.

## Roadmap

The implemented slice is local, deterministic, and offline. Its built-in simulators,
metadata-only training fixture, and synthetic examples are deterministic fakes for
testing authority boundaries; they are not empirical scientific reproduction.
Strict cognitive contracts, pure analyses, governed persistence, replay, bundles, and
single-record inspection are implemented. Fixed in-memory peers, synthetic capability
evidence, deterministic procedure execution, synthetic guidance, and model-by-harness
cells are example only. Operational-diversity, guidance-gradient, interaction, and
reward-hacking analyses are experimental diagnostics. Live model grounding, live peer
adapters, provider-native generation metadata, training-payload handoff, reinforcement
learning, arbitrary external harnesses, learned admission, and self-modifying governance
remain interface-only or deferred.

The implemented architecture, governance, claim lifecycle, and security limits are
documented in `ARCHITECTURE.md`, `GOVERNANCE.md`, `CLAIM_LEDGER.md`, and `SECURITY.md`.
Operational reproduction and the attacker model are in `REPRODUCIBILITY.md` and
`THREAT_MODEL.md`.
