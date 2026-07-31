# ADR 0001: Protected evaluation transaction and worker lifecycle

- Status: Accepted
- Date: 2026-07-20
- Scope: Task 13 protected evaluation storage only

## Context

Task 13 requires a result gateway that appends a validated protected checker result
to the main transaction without carrying answer bytes or reversible protected-store
references. The first process-isolation implementation moved persistence into a
spawned worker. That worker opened a second SQLite connection, ignored the supplied
coordinator transaction, and competed with the coordinator's `BEGIN IMMEDIATE`
writer lock. The same implementation did not serialize shared IPC exchanges, retained
closed capabilities strongly, and allowed expected storage failures to escape child
processes as tracebacks.

## Decision

The protected evaluator boundary and coordinator persistence boundary have distinct
objects and authority:

1. An evaluator-facing spawned result validator accepts strict JSON, validates a
   `ProtectedCheckerResult`, and returns only that validated DTO. Its parent boundary
   first checks built-in `type()` identity and accepts only the exact DTO type, never a
   subclass or duck-typed object whose serialization could carry protected fields. It
   does not call `isinstance()`, which may consult an untrusted dynamic `__class__`
   attribute. Even an exact instance is revalidated into a fresh canonical DTO before
   serialization, because Pydantic `model_copy()` updates and `model_construct()` do
   not validate field values. The validator owns no main database object or
   protected-store path.
2. `ProtectedResultGateway` remains the coordinator-facing API. Its implementation is
   a local adapter over the supplied active SQLAlchemy connection and appends through
   that exact transaction. It necessarily owns coordinator repository/connection
   authority and must never be inserted into evaluator or candidate object graphs.
3. Each process capability serializes a complete request-id/send/poll/receive/payload-
   decode exchange and `close()` under one re-entrant lock. Timeout, EOF, transport
   failure, malformed envelope or role payload, or request-id mismatch permanently
   poisons the channel before another request can be sent.
4. Capability close is idempotent and race-safe, closes the transport, joins or
   terminates the child, then calls `BaseProcess.close()`. Stores weakly track issued
   role capabilities so closed or abandoned wrapper objects are not retained.
5. Reader and auditor workers catch expected SQLAlchemy, database, and filesystem
   failures. They return fixed typed errors or integrity findings without exception
   text, filesystem paths, answer data, or inherited child tracebacks.
6. Rejected checker-result objects and malformed worker payloads raise only fixed safe
   errors with no retained validation cause or formatted exception chain containing
   input values or paths. Coordinator record or repository validation failures use the
   same cause-free mapping as a defense behind the DTO boundary.

## Consequences

Protected result persistence is atomic with the coordinator unit of work and rolls
back with it. There is no second SQLite writer. Least authority at the evaluator
boundary is enforced by placement: the validator worker has no database authority,
while the coordinator adapter openly has the authority required to write its supplied
unit of work.

Shared capabilities are deterministic across threads, at the cost of serializing each
single duplex channel. A poisoned channel cannot be reused because a delayed response
could otherwise be mistaken for a later request.

The protected database, artifact layout, migration 0006, strict JSON framing,
`ProtectedResultGateway.append_result` signature, and Git object-ID contract do not
change.

## Verification

Regression coverage must include:

- committed-campaign append and rollback inside `DatabaseUnitOfWork`;
- a 32-thread shared reader and concurrent coordinator gateway appends;
- timeout/desynchronization poison behavior and concurrent idempotent close;
- answer-bearing result subclasses, malformed DTOs, exact results produced by
  `model_copy()`/`model_construct()`, objects with dynamic `__class__` attributes, and
  malformed reader, auditor, and validator payloads with no cause-chain leakage or
  channel reuse;
- portable weak-registry and closed-process checks plus Windows handle counts;
- structurally corrupt protected SQLite and unavailable artifacts with no path,
  answer, exception, or traceback leakage; and
- the existing capability graph, role allowlist, migration, property, and packaging
  gates.
