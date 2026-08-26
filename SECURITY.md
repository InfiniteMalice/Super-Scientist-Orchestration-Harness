# Security Policy And Boundaries

## Runtime Authority

The epistemic-kernel runtime has no arbitrary shell, network, or model authority. Its
CLI reads explicit local files, writes one local workspace, and submits typed proposals
through deterministic admission. Retrieved or user-supplied evidence is untrusted data:
its text cannot introduce commands, permissions, policies, or executable tool
definitions.

`KernelService` revalidates direct objects and intent-factory results before hashing,
lookup, attribute access, or artifact handling. Malformed input with safe durable IDs is
stored only as a typed `InvalidProposal` rejection. If proposal or idempotency identity
cannot be recovered, the stable rejection is intentionally non-durable rather than
inventing authority or leaking serializer/storage exceptions.

Intent factories receive no authority to define their own durable identity: a typed
attempt envelope fixes proposal ID, idempotency key, proposer, and kind before invocation.
Expected model/input validation failures are durably rejected and replayed from storage.
Unexpected programming or storage exceptions are not relabeled as invalid user input;
they propagate while the unit of work rolls back. External configuration, identity,
evidence, span, artifact, claim, evidence-link, and nested proposal models reject extras.
Durable validation diagnostics are fixed redacted text with no rejected values, field
names, or dynamic locations. An intent retry must match a service-owned transaction and
audit fingerprint of its canonical input digest, IDs, logical proposer, and proposal
kind; public proposals cannot populate it and same-key mismatches are audited conflicts.
Direct submission cannot replay an intent-owned transaction without that fingerprint;
cross-mode key reuse is an audited conflict.

`scientist-harness quality-gate` is a separate development command. It invokes only its
nine fixed source-controlled argument vectors and exposes no arbitrary command, path,
selection, skip, or threshold input. Its dependency audit may use network access. Its
wheel-install check executes the installed model-free example in a fresh environment.
This fixed developer tooling is not research-run execution authority.

## Cognitive Authority Boundary

The cognitive plane has no retained control-plane capability. The sealed stateless
`CognitiveOrchestrationService` receives an exact coordinator only for the duration of
one `submit` call. The sealed stateless `ResearchCoordinator` receives the service and
coordinator only for one declared tuple and stops after the first rejection. Neither
object retains a repository, unit of work, connection, artifact store, protected reader,
callable, closure, registry token, tool executor, model provider, or command runner.

When the coordinator receives any of the 18 governed proposal families, it classifies
the exact family without invoking caller serializers or nested hooks. For a trace, the
coordinator creates one fresh exact owned proposal snapshot and uses it for context,
decision, projection, storage, and audit. A hostile caller therefore cannot mutate a
validated trace into a different stored trace between stages. Run
`python -m pytest tests/integration/application/test_transaction_coordinator.py
tests/adversarial/test_cognitive_authority.py tests/adversarial/test_trace_reward_tampering.py -q`
to verify these authority and single-snapshot properties.

Procedure records cannot supply Python imports, module names, shell commands, providers,
dynamic imports, arbitrary tools, protected evaluators, or self-selected governance.
The compiler accepts only declared current evidence receipts and fixed catalog entries;
the validator rejects forbidden operations, method anchoring, recursive delegation,
undeclared artifacts, tools, validators, and resources. The deterministic example's toy
validator reads bounded artifact bytes and compares an expected SHA-256 digest. Its
actor provenance is `TOOL`, never `HUMAN`, and it never executes artifact content.

Hidden chain-of-thought or private-reasoning fields are absent from the public cognitive,
procedure, trace, and reward schemas. Callers may submit bounded observable outputs,
artifacts, decisions, diagnostics, and provenance only. Static schema/import tests and
handler-boundary attacks run with `python -m pytest tests/adversarial/test_cognitive_authority.py
tests/adversarial/test_procedure_escalation.py -q`.

## Model Execution Boundary

Hypothesis model records are data, not executable authority. `METADATA_ONLY` records
require content-addressed artifact metadata and are never executed. The only executable
mode selects one of two immutable, source-controlled deterministic simulators by fixed
identifier. Unknown identifiers reject. Records cannot provide import paths, entry
points, source text, argument vectors, shell commands, filesystem paths, or network
locations.

Built-in simulation accepts strict numeric input, exact schemas and seeds, and bounded
step and state sizes. It uses in-memory state and has no filesystem, network,
subprocess, dynamic-import, `eval`, or `exec` path. A submitted simulation result is
re-executed through the fixed registry before it can be retained. Model type describes
metadata and never broadens this boundary.

## Filesystem And Artifact Boundary

Artifact references are SHA-256-derived relative paths beneath the configured root.
Absolute paths, parent traversal, digest/path disagreement, non-regular files, hash or
size mismatch, symlinks, and Windows reparse points fail closed. Existing digest paths
are verified instead of overwritten. Temporary files are created under the target
directory and synchronized before linking to the final content address.

Submitted `EvidenceRecord` metadata is not a verification authority. Records default to
`UNVERIFIED`, and `KernelService` is the only boundary that promotes them to
`HASH_VERIFIED`. It reads through the configured artifact store, validates containment,
size, digest, media handling, and text-span binding, and durably rejects failures as
`EVIDENCE_HASH_MISMATCH`. CLI and direct API submissions use this same path.

The artifact root is private/trusted against malicious concurrent namespace mutation:
static traversal, symlink, and Windows reparse-point escapes fail closed, while hostile
replacement between filesystem checks remains a residual local-filesystem risk. Run the
workspace on a local filesystem whose directory permissions exclude untrusted writers;
do not place the artifact root on a hostile shared namespace.

SQLite is also a local trust boundary. Append-only triggers reject updates and deletes
to policy snapshots, evidence records, claim versions, transactions, and audit events,
but an attacker with database-file or process-level access is outside the kernel's
authorization boundary.

## Workspace Exchange Boundary

An imported bundle and its source artifact store are untrusted. The strict
`WorkspaceExport` schema rejects extras and coercions; canonical proposal, bundle,
policy, decision, and projection hashes are checked before the target is declared
equivalent. Records are submitted through stable coordinator intents under their
recorded governing policy. Same-identity content changes produce audited conflicts
rather than replacement.

The JSON bundle contains digest-only artifact references. Artifact bytes move between
explicit stores and are checked for media type, size, digest, and content-derived
location. Exported object graphs are scanned fail closed for protected-answer fields,
protected-store or database references, live paths, and executable configuration. This
is a structural boundary, not a data-loss-prevention system: operators must still keep
secrets and sensitive research content out of ordinary evidence and proposal fields.

Import is intended for an empty workspace or an exact idempotent replay. It is not a
general merge, backup encryption, sandbox, signature, origin authentication, or
authorization protocol. Bundle hashing detects modification under the local hash
assumptions but does not establish who produced the bundle.

## Integrity And Secrets

Audit events use trusted sequence-derived identifiers, hash canonical payloads, and link
each event to its predecessor. Repository reads validate redundant SQLite columns
against canonical records and verify the chain. Shared workspace verification also
reconciles policy, claim heads and history, evidence projections, transactions, and
audit decisions, then rehashes every authoritative artifact. It runs before mutation,
before exact replay returns, and through `audit verify`. Corruption, policy mismatch,
projection inconsistency, evidence replacement, and artifact mismatch stop the affected
operation; a genuinely empty workspace remains valid. A database with any durable
kernel row but no active governance pointer is corrupt, not uninitialized. `init` cannot
repair or overwrite that authority gap; storage-only `audit verify` reports it.
All registered policy rows and governance-state cardinality are verified. Stored audit
envelopes require an explicit integer schema version 1 and reject unknown fields, so
tampering cannot disappear during model parsing.
Persisted evidence, claim, transaction, policy, and audit decoding failures are reported
as storage corruption. Transaction timestamps and intent fingerprints are validated on
read, and fingerprints are bound into the audit chain.
Audit events also bind a strict `transaction_persisted` fact. Workspace verification
requires exact bidirectional agreement for accepted and rejected decisions, preventing
lost rejection rows from reopening an idempotency key.

Durable state requires an active registered policy, and each audit event's governing
and stored policy references, plus any configured policy reference it carries, are
checked against registered snapshots. Unregistered stale-service configuration is not
recorded as authoritative policy attribution.
Exact replay does not readmit under current policy, but it fails closed when that policy
registration or active pointer is missing or inconsistent.

Hypothesis downstream stages require exact accepted transaction and audit receipts.
Receipt hashes and trusted committed transaction and audit times bind content and causal
bounds. Caller timestamps confer no authority, cannot predate retained dependencies,
and cannot exceed the current transaction's persistence time; audit sequence preserves
durable order and breaks equal-time ties. Workspace verification replays accepted
hypothesis mutations using the same fixed handlers and compares rebuilt records and
heads with storage. Deleted or altered stage records, forged receipts,
non-reproducible simulations, unresolved counterexamples, and tampered hypothesis heads
therefore fail closed.

Harness traces bind exact protocol/cell coordinates, output artifacts, verifier results,
environment identity, transformations, generation metadata, reward observations, and
accepted provenance. Each optional metadata value is paired with an explicit
`AVAILABLE`, `UNAVAILABLE`, or `UNKNOWN` state; an unavailable field cannot carry a
fabricated value. Reward assessments bind the same accepted trace and retain every
invalidating finding. High reward cannot erase verifier failure, environment tampering,
answer leakage, trace inconsistency, proxy gaming, cherry-picking, contamination,
premature termination, or resource evasion. The trace/reward adversarial test command
above verifies rejection and unchanged authoritative heads.

The kernel has no secret store, credential broker, runtime redaction service, or
dedicated secret scanner. Do not put API keys, credentials, private tokens, or regulated
data in evidence files, policy files, CLI arguments, logs, or the workspace. Core use
requires no paid API or model credential. Dependency auditing is a vulnerability check,
not secret detection.

## Vulnerability Disclosure

Do not include sensitive exploit details in a public issue. Use the repository hosting
provider's private vulnerability-reporting channel when it is enabled; otherwise contact
the repository maintainer privately through the hosting profile. Include the affected
version or commit, operating system and filesystem, reproduction steps, impact, and any
known mitigation. Avoid attaching real secrets or sensitive research data.

## Residual Risks

The slice is local-first, not a hardened multi-user service. Residual risks include
malicious local administrators or processes, concurrent namespace replacement between
checks, direct database-file tampering, denial of service through large local inputs,
dependency and build-tool compromise, and disclosure through user-managed backups or
logs. The audit chain is tamper-evident under its hash and storage assumptions; it is not
an external timestamp, signature, remote attestation, or guarantee of scientific truth.
See `THREAT_MODEL.md` for assets, trust boundaries, attacker capabilities, controls,
and explicit non-goals.
