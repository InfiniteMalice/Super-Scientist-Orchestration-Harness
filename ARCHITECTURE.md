# Epistemic Kernel Architecture

## Scope

This document describes the implemented transactional epistemic-kernel slice. The
slice accepts typed evidence, claim, governed-adaptation, and hypothesis-loop proposals,
makes deterministic decisions, and persists records locally. It has no autonomous
orchestration loop, live experiment control, arbitrary model provider, learned memory,
or training subsystem.

## Dependency Rule

Dependencies point inward toward typed domain contracts and deterministic kernel
policy:

```text
CLI and composition
    -> bounded domain application services
        -> shared transaction coordinator and fixed proposal router
            -> domain admission handlers and narrow projectors
                -> audit, transactions, evidence, identity, canonical primitives

SQLite and filesystem providers
    -> typed domain and kernel records
```

Domain records do not import the CLI or storage providers. Admission does not import or
call models, networks, or arbitrary tools. The CLI is the composition boundary that
constructs the current SQLite unit of work and filesystem artifact store. The
application service currently types its unit-of-work factory to that SQLite
implementation; provider interchangeability is not delivered by this slice.

The separation between durable transaction history and deterministic effective-state
projections is informed by raw-record-preserving projected-view concepts [S07]. This
implementation uses project-specific, deterministic SQLite projections; it contains no
learned projection and does not reproduce HarnessBridge.

## Storage Boundaries

SQLite stores policy snapshots, the active policy reference, evidence metadata, claim
versions and heads, governed-adaptation records, hypothesis/model/checker/revision
records and hypothesis heads, proposals and decisions, and audit events. Alembic owns
the schema.
Each application submission opens `BEGIN IMMEDIATE`; accepted projection changes, the
transaction decision, and the audit append commit or roll back together. Append-only
tables have SQLite triggers that reject update and delete. Claim heads and the active
policy pointer are effective-state projections whose referenced canonical records are
validated on read.

Evidence bytes live outside SQLite under
`artifacts/sha256/<two-character-prefix>/<sha256>`. A write uses a temporary file in the
target directory, flushes and synchronizes its bytes, then creates the immutable digest
path. An existing digest must contain matching bytes. Reads verify the recorded size and
SHA-256 digest. See `SECURITY.md` for the filesystem threat boundary.

`EvidenceRecord` input defaults to `UNVERIFIED`; caller-supplied verification claims are
not authority. `KernelService` owns the artifact-store dependency, validates the
digest-derived path, bytes, size, digest, media handling, and any text span, then
projects a `HASH_VERIFIED` copy. Failure is a durable audited
`EVIDENCE_HASH_MISMATCH` rejection.

## Proposal And Admission Flow

The public proposal union includes the original evidence and claim operations plus the
fixed governed-adaptation domains. The hypothesis slice contributes exactly eight
operations: propose a hypothesis version, register a model, register a verification
mechanism, record a simulation, record a verification, record a counterexample, revise,
and admit. `InvalidProposal` remains an internal durable rejection envelope. Every
normal proposal carries a proposal identifier, idempotency key, proposer identity, and
optional approval. Intent submission first supplies a typed `ProposalAttempt`
containing stable proposal/idempotency identifiers, proposer identity, and expected
proposal kind.
Submission follows this flow:

```text
typed attempt envelope or untrusted direct proposal
-> BEGIN IMMEDIATE
-> shared workspace integrity and idempotency lookup
-> invoke an intent factory only when no decision exists
-> strict TypeAdapter normalization and exact attempt-envelope matching
-> canonical proposal hash
-> active policy hash and proposal identity checks
-> independent-approval check
-> entity, status-transition, and evidence-span checks
-> accept or reject with structured reasons
-> accepted projection update
-> append transaction decision and audit event atomically
```

An exact idempotent replay first verifies related stored state and returns the stored
decision without readmission, another mutation, or another audit. Reuse of a key with
different canonical content is rejected and audited. Policy mismatch,
self-approval, duplicate entities, illegal transitions, missing evidence, and unresolved
required checks fail closed.

Malformed direct-service input with recoverable proposal and idempotency identifiers is
normalized into an audited `INVALID_PROPOSAL` transaction. When either identifier is
unusable, the service returns a stable `INVALID_PROPOSAL` decision before storage; no
truthful durable identity exists for an audit record. Expected Pydantic/input decoding
failures inside an intent factory use the prevalidated attempt identity and become one
audited, replayable `INVALID_PROPOSAL`; retries do not invoke the factory. Unexpected
programming and storage exceptions propagate and roll back. Factory results must match
the attempt's ID, key, proposer, and kind. A service-owned transaction column and its
audit record carry a fingerprint of the canonical intent digest, proposal ID, key,
logical proposer, and kind; public proposals cannot set it. Only an exact fingerprint
and proposal/decision identity replay, while a mismatch is an audited
`IDEMPOTENCY_CONFLICT`. Durable validation diagnostics are fixed redacted text and never
include rejected input values, field names, or dynamic locations.
Direct submission and intent submission are distinct replay modes: a direct request has
no trusted fingerprint and therefore conflicts with an intent-owned transaction even
when the proposal JSON is identical.
Evidence ingestion actors, initial claim
creators, and transition creators must match their proposers. External config and
nested domain records forbid unknown fields, while JSON arrays/objects remain accepted
for declared tuple/mapping fields.

`TransitionClaim` carries the complete intended next claim version. Admission validates
that exact state and the application service projects it unchanged; it does not
synthesize status-only transitions.
Withdrawal preserves assumptions and evidence links exactly. Falsification,
supersession, reproduction, corroboration, and constraint validation remain
review-required until their typed proof records exist.

This untrusted-proposal versus deterministic-admission boundary, plus append-only
transition records and effective-state projection, is adapted conceptually from
transactional workflow validation [S12]. The transaction model is project-specific,
does not reproduce Mnemosyne, and does not make scientific-truth guarantees.

Hypothesis stages additionally bind exact accepted upstream receipts. Trusted committed
transaction and audit times bound caller-authored record and approval times: they may
neither predate retained dependencies nor exceed the current transaction's persistence
time. Audit sequence preserves durable order and breaks equal-time ties. A fixed registry
can execute only the source-controlled thermal-chamber and exponential-decay simulators;
metadata-only model artifacts remain inert. Admission alone can advance a hypothesis
head after transfer validation, exact evidence and revision lineage, deterministic
counterexample search, evaluator audit, self-improvement measurement, primitive-use
checks, rollback binding, and independent human authority. See
`docs/hypothesis-model-checker-loop.md` for the complete boundary.

## Audit Chain

Each durably attributable non-replayed decision produces an immutable audit event
containing the proposal, decision, registered governing policy hash, and stored-policy
attribution. Registered configured-policy attribution is included separately; an
unregistered stale-service hash is never promoted into an authoritative event. The event stores a canonical payload hash,
previous event hash, sequence, schema version, and an event hash over the canonical envelope.
Its identifier is generated solely from the trusted sequence. Genesis uses a zero hash.
Repository reads validate redundant columns against canonical event JSON and verify the
complete chain. Shared workspace verification also reconciles transactions with audit
decisions and exact projections, validates claim heads/history, and rehashes every
authoritative evidence artifact. `scientist-harness audit verify` exposes that whole
workspace check through a storage-only runtime and returns nonzero for corruption even
when a missing active policy prevents normal service construction; an empty chain in a
genuinely empty or initialized workspace is valid.

For hypothesis state, verification also replays accepted transactions in audit order
through the same fixed handlers with only their narrow write capabilities, then compares
the rebuilt append-only records and heads with storage. A forged or missing receipt,
record, or head therefore fails integrity verification rather than becoming a new
authority source.

Workspace verification decodes every registered governance policy, not only the active
or audit-referenced rows, and requires exactly zero or one `governance_state` row with
singleton ID 1. SQLite enforces that identifier with a check constraint. Stored audit
events require an explicit integer schema version 1 and reject unknown envelope fields.
Transaction reads validate their timestamps and canonical records; persisted decoding
failures are storage-integrity errors rather than user-input errors.
Every transaction-decision audit payload records whether that event persisted a
transaction. Verification requires bidirectional agreement, so loss of accepted or
rejected transaction rows fails closed while transactionless conflict events remain
explicit and valid.

JSON parser translation catches public Click exceptions. Typer 0.19.2 and Click 8.3.3
are pinned because this is a security-audited compatibility line where Typer still
subclasses the public Click hierarchy; later Typer releases vendor-incompatible Click
exception classes. Typer's documented custom `cls` extension still requires its
`TyperGroup` base, which has no top-level export in that compatibility line.

Source metadata for [S07] and [S12] is maintained in
`docs/sources/source-register.yaml`.

## Workspace Exchange

`export_workspace()` first runs whole-workspace integrity verification, then builds a
strict schema-version-1 `WorkspaceExport`. Policies, transaction records, rebuildable
projection expectations, and content-addressed artifact references are unique and
sorted by stable identity. Each proposal and the whole bundle have canonical SHA-256
hashes. Replay order is retained separately from sort order so reconstruction preserves
the original governing policy and event sequence.

The bundle is a portable integrity description, not a database dump. It contains no
SQLite path, artifact-root path, protected expected answer, protected-store reference,
or executable configuration. Artifact references contain only digest, byte count, and
media type; bytes are transferred between caller-supplied artifact stores and verified
against those references.

`import_workspace()` reparses the canonical JSON through the strict model, validates
every policy, proposal, decision, artifact, projection expectation, and bundle hash,
and bootstraps only a genuinely empty target. It then replays records in retained order
through `TransactionCoordinator.submit_intent()`. Exact stable intents are idempotent.
Changed canonical content under an existing proposal or idempotency identity becomes a
durable audited `IDEMPOTENCY_CONFLICT`; it is never treated as an update. A conflict
returns before claiming projection equivalence. A conflict-free import must re-export
to exactly the source bundle.

## Capability Status

Implemented components include transactional proposal admission, V1-to-V2 and governed
V2 policy transitions, deterministic workspace verification and exchange, progress and
evidence-trail contracts, append-only behavioral-rule records, fixed simulator
execution, hypothesis admission contracts, deterministic handbook generation, and
matched-budget harness decisions.

The vertical-slice simulator, measurement report, and metadata-only adapter records are
deterministic fakes used to test contracts. Learned evaluation, primitive evolution,
live model providers, experiment control, and training are interface-only,
experimental, or deferred. S21-S29 are source inspirations marked not reproduced; no
general improvement or compatibility with S29 follows from this architecture.
