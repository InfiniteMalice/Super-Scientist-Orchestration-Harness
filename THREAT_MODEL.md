# Threat Model

## Scope And Security Claims

This model covers the local 0.2.0 runtime, SQLite workspace, content-addressed artifact
store, deterministic simulators, governed proposals, audit chain, and workspace
exchange. It assumes the Python interpreter, installed dependencies, operating system,
process, database connection, and private local storage namespace are trusted while an
operation runs.

The harness enforces typed authority and detects many integrity violations. It does not
provide a hardened multi-user service, operating-system sandbox, malware scanner,
secret store, encryption, signature, remote attestation, external timestamp, scientific
truth oracle, or proof of safe recursive self-improvement.

## Assets

- registered governance policies and the active-policy pointer;
- accepted and rejected proposal history, exact decisions, and idempotency identities;
- audit order, hashes, policy attribution, and transaction correspondence;
- authoritative evidence metadata and content-addressed artifact bytes;
- claim, research, progress, trail, rule, primitive, hypothesis, evaluator, and harness
  histories plus their rebuildable heads;
- protected evaluation separation, budgets, rollback lineage, and human authority; and
- canonical workspace bundles and out-of-band artifact transfers.

## Trust Boundaries And Adversaries

Proposal JSON, source text, evidence, model or tool output, reviewer assessments,
simulation submissions, evaluator reports, manifests, benchmark data, and imported
workspace bundles are untrusted. A proposer may try to approve itself, smuggle
executable configuration, alter a policy, expose a protected answer, forge provenance,
reuse an idempotency key, omit failures, or turn a local gain into general authority.

An ordinary local caller may control explicit input files and workspace paths but is not
trusted to construct valid internal records. An attacker with administrator,
process-memory, interpreter, dependency, or direct database/artifact-file control is
outside the authorization boundary and can cause denial of service or compromise
confidentiality. Hash and reconciliation checks make many direct changes detectable;
they do not prevent a fully privileged attacker from replacing code and state together.

## Threats And Controls

| Threat | Implemented control | Residual risk |
| --- | --- | --- |
| Unknown or coercible fields change meaning | Frozen strict schemas, exact schema versions, unknown-field rejection, canonical serialization | A valid but misleading value may remain semantically untrue |
| Self-promotion or circular evaluation | Prior-policy authority, constitutional floor, explicit actor relationships, independent human gates, rollback binding | Independence metadata depends on truthful external identity administration |
| Protected-answer leakage | Split-specific contracts, protected object-graph prohibitions in export, digest-only protected references | This is not semantic DLP; secrets in ordinary allowed strings are an operator failure |
| Proposal replay or identity substitution | Service-owned intent fingerprint, canonical proposal hash, exact replay, audited conflict on mismatch | Denial of service by repeated conflicts remains possible |
| Database or projection tampering | Append-only triggers, audit hashes, transaction/audit reconciliation, deterministic replay, head/history checks | Privileged code-and-state replacement is outside scope |
| Artifact traversal or replacement | Digest-derived paths, containment, regular-file and reparse checks, size/hash verification | Hostile concurrent namespace mutation remains a local-filesystem risk |
| Arbitrary code execution from records | No record-supplied import, command, source, path, network, `eval`, or `exec`; fixed bounded simulator registry | The development toolchain and Python dependencies execute trusted code |
| Proxy gaming or false finish | Separate provisional/official progress, final-validator authority, full trajectory and failure retention, protected transfer | Declared metrics or holdouts may still be scientifically inadequate |
| Benchmark overclaim | Matched budgets, partition separation, explicit confounds and transfer status, `BENCHMARK_SPECIFIC` rejection | One held-out fixture cannot demonstrate general improvement |
| Workspace-bundle substitution | Strict canonical bundle and record hashes, policy/hash verification, artifact rehash, exact re-export | No signature or origin authentication is provided |
| Import overwrites existing truth | Empty-target bootstrap only, stable coordinator intents, exact replay, audited `IDEMPOTENCY_CONFLICT` | General divergent-workspace merge is intentionally unsupported |

## Workspace Exchange Data Boundary

`WorkspaceExport` contains authoritative policy snapshots, proposal/decision records,
rebuildable projection expectations, and content-addressed artifact metadata. Records
and expectations are sorted by stable identity; replay order is explicit. It must not
contain protected expected outputs, protected-store or database references, live
filesystem paths, or executable configuration.

Artifact bytes are not embedded in the JSON bundle. The importer reads each referenced
digest from an explicit source store, verifies the bytes, writes through the target
content-addressed store, and verifies again. It reparses the bundle through the strict
schema and submits proposals under their retained policies. Projection expectations
confirm reconstruction; they are not write authority.

## Availability, Confidentiality, And Operations

Input size limits exist in selected models and simulators, but the local slice has no
global quota, authenticated tenant boundary, encrypted backup, or availability
guarantee. Use operating-system permissions to isolate the database and artifact root.
Do not store credentials, private tokens, regulated data, protected holdout answers, or
unreviewed sensitive evidence in logs, CLI arguments, public bundles, or reports.

Dependency audit and static security scanning reduce known development risk but do not
prove absence of vulnerabilities. The fixed quality command is development authority
and may access package infrastructure; it is not callable from scientific records.

## Scientific And Source Non-Goals

Passing admission proves only compliance with implemented constraints under recorded
inputs. Evidence-trail coherence is not proof, model confidence is not independent
evidence, a learned judge is not formal verification, and a deterministic fake is not
an empirical reproduction. The project does not claim solved hallucination, open-ended
autonomy, general improvement from local benchmarks, reproduction of S21-S29, or
compatibility with S29.

Report vulnerabilities as described in `SECURITY.md`. Operational reproduction steps
and evidence-retention guidance are in `REPRODUCIBILITY.md`.
