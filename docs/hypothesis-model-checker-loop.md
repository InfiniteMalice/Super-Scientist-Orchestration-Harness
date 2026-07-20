# Hypothesis, Model, Checker, and Revision Loop

## Purpose

The hypothesis loop records a bounded scientific control process without granting a
model, artifact, checker, or proposer authority to execute code or admit its own work.
It preserves immutable hypothesis versions, exact model and checker metadata,
reproducible built-in simulations, verification provenance, counterexamples, revision
lineage, and an independently authorized admission decision.

The loop is domain-neutral. Its contracts contain statements, assumptions, scope,
variables, predictions, falsification conditions, schemas, provenance, and stable
record identifiers. They do not contain benchmark-specific fields or executable
instructions.

## Durable Stages

The shared transaction coordinator routes exactly eight hypothesis proposal kinds:

1. `propose_hypothesis_version`
2. `register_executable_model`
3. `register_verification_mechanism`
4. `record_simulation_result`
5. `record_verification_result`
6. `record_counterexample`
7. `revise_hypothesis`
8. `admit_hypothesis`

Every stage uses the same workspace-integrity, idempotency, policy-attribution, audit,
and atomic-commit boundary as the rest of the kernel. A downstream proposal names an
accepted receipt containing the exact upstream proposal hash, audit event identifier,
and audit event hash. The coordinator resolves that receipt from committed transaction
and audit history. Caller-authored timestamps are descriptive metadata; receipt audit
sequence establishes chronology.

Hypothesis versions and stage records are append-only. `HypothesisHead` is a derived
projection. Revisions preserve the failed predecessor and append a contiguous successor
whose changed predictions and falsification conditions are explicit. Only accepted
hypothesis admission can advance the head, and a successor admission must name the
current head as its rollback target.

## Safe Model Boundary

`ExecutableModelSpec` separates model description from execution authority:

- `METADATA_ONLY` requires content-addressed artifact metadata and cannot name a
  simulator. The artifact is never imported or executed.
- `BUILTIN_DETERMINISTIC_SIMULATOR` cannot carry an artifact and must name one entry in
  the immutable source-controlled registry: `thermal-chamber-v1` or
  `exponential-decay-v1`.

Model records have no import path, entry point, source text, argument vector, shell
command, filesystem path, or network location. The registry accepts strict numeric
records, exact input/output schema identifiers, and a deterministic seed. It enforces
model step and state-size limits, keeps state in memory, and exposes no filesystem,
network, subprocess, dynamic-import, `eval`, or `exec` path. A stored simulation is
accepted only when the coordinator reproduces its output through the same fixed
registry and exact retained model, input, schemas, seed, and bounds.

## Verification and Counterexamples

Verification mechanisms and results are strict discriminated unions:

- formal verifier records require formal deterministic provenance;
- deterministic checker records require independent deterministic-check provenance
  and retained counterexample-search evidence; and
- learned judge records remain explicitly learned and cannot claim formal or
  deterministic authority.

Mechanism creator, verification actor, candidate, evidence, policy, model, simulation,
and receipt lineage must agree exactly. Evidence must already be hash verified and
classified as controlled-experiment grounding. A successful search that finds no
counterexample remains a retained verification result. A `CounterexampleRecord`, by
contrast, represents a found counterexample: it must bind failing retained verification
evidence and blocks admission of that hypothesis version. The version can proceed only
through an explicit successor revision and a new verification chain.

## Governed Admission

All hypothesis mutations use the fixed classification
`RESEARCH_PROCESS / HUMAN_IN_LOOP / RUN_LOCAL /
INDEPENDENT_DETERMINISTIC_CHECK / CONTROLLED_EXPERIMENT /
EMPIRICAL_MEASUREMENT`. The active V2 policy must authorize that exact run-local
classification, and every mutation requires an independent human approval. Distinct
roles in a stage must also be independent; a configuration-only alias of the same model
identity is not an independent reviewer.

Admission additionally requires:

- a `TRANSFER_VALIDATED` candidate and an accepting admission decision;
- at least one registered built-in deterministic model;
- passing retained verification results and a passing deterministic counterexample
  search that found no counterexample;
- complete, contiguous revision history and no counterexample for the candidate
  version;
- exact hash-verified controlled-experiment evidence and active-policy provenance;
- an accepted self-improvement measurement and passed evaluator audit whose checks,
  evidence, evaluator, authority, candidate, baseline, and rollback bindings agree;
- an independent human decision authority; and
- exact admitted representational-primitive heads, when primitives are referenced.

Confidence, self-consistency, learned-judge agreement, caller timestamps, or proposer
approval cannot replace these gates. Admission is not a scientific-truth guarantee; it
is a deterministic statement that the recorded candidate satisfied this policy and
evidence boundary.

## Transfer Evaluation

The offline evaluation exercises the same domain-neutral loop on four independently
authored deterministic fixtures: thermal chamber, exponential decay, an immutable
synthetic equipment-incident document, and an in-memory software-maintenance manifest.
It compares direct deterministic reasoning, ordinary plan-and-execute, retry with
checker feedback, and the typed revision loop. Metrics are recomputed from condition
attempts and remain separate: correctness, checker accuracy, false admission,
diversity, revision utility, unsupported-model rate, abstention, cost, transfer, and
regression.

The document and manifest fixtures are inert test records, not runtime model types or
new execution backends. The evaluation imports no external implementation and grants
no filesystem, shell, network, or model-provider authority.

## Integrity and Limits

Workspace verification replays accepted hypothesis transactions in audit order using
the fixed handlers, then compares every rebuilt authoritative record and hypothesis
head with storage. Missing receipts, deleted records, altered proposal JSON, forged
heads, inconsistent chronology, or non-reproducible simulations fail closed.

This slice supplies typed application operations, not new CLI commands, live laboratory
control, arbitrary scientific code, a network model provider, automatic promotion, or
a general workflow language.
