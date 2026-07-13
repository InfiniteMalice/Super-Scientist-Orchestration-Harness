# Epistemic Kernel Architecture

## Scope

This document describes only the implemented epistemic-kernel vertical slice. The
slice accepts typed evidence and claim proposals, makes deterministic decisions, and
persists records locally. It has no orchestration loop, hypothesis or experiment model,
research-run state machine, learned memory, model provider, or training subsystem.

## Dependency Rule

Dependencies point inward toward typed domain contracts and deterministic kernel
policy:

```text
CLI and composition
    -> application transaction service
        -> admission, audit, transactions, claim checks
            -> evidence, claims, identity, canonical primitives

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
versions and heads, proposals and decisions, and audit events. Alembic owns the schema.
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

The implemented proposal union is `AddEvidence`, `ProposeClaim`, and `TransitionClaim`.
Every proposal carries a proposal identifier, idempotency key, proposer identity, and
optional approval. Submission follows this flow:

```text
typed proposal or intent factory
-> BEGIN IMMEDIATE
-> canonical proposal hash and idempotency lookup
-> invoke an intent factory only when no decision exists
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

`TransitionClaim` carries the complete intended next claim version. Admission validates
that exact state and the application service projects it unchanged; it does not
synthesize status-only transitions.

This untrusted-proposal versus deterministic-admission boundary, plus append-only
transition records and effective-state projection, is adapted conceptually from
transactional workflow validation [S12]. The transaction model is project-specific,
does not reproduce Mnemosyne, and does not make scientific-truth guarantees.

## Audit Chain

Each non-replayed decision produces an immutable audit event containing the proposal,
decision, and active policy hash. The event stores a canonical payload hash, previous
event hash, sequence, schema version, and an event hash over the canonical envelope.
Its identifier is generated solely from the trusted sequence. Genesis uses a zero hash.
Repository reads validate redundant columns against canonical event JSON and verify the
complete chain. Shared workspace verification also reconciles transactions with audit
decisions and exact projections, validates claim heads/history, and rehashes every
authoritative evidence artifact. `scientist-harness audit verify` exposes that whole
workspace check and returns nonzero for corruption; an empty chain is valid.

Source metadata for [S07] and [S12] is maintained in
`docs/sources/source-register.yaml`.
