# Security Policy And Boundaries

## Runtime Authority

The epistemic-kernel runtime has no arbitrary shell, network, or model authority. Its
CLI reads explicit local files, writes one local workspace, and submits typed proposals
through deterministic admission. Retrieved or user-supplied evidence is untrusted data:
its text cannot introduce commands, permissions, policies, or executable tool
definitions.

`scientist-harness quality-gate` is a separate development command. It invokes only its
eight fixed source-controlled argument vectors and exposes no arbitrary command, path,
selection, skip, or threshold input. Its dependency audit may use network access. This
fixed developer tooling is not research-run execution authority.

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

## Integrity And Secrets

Audit events use trusted sequence-derived identifiers, hash canonical payloads, and link
each event to its predecessor. Repository reads validate redundant SQLite columns
against canonical records and verify the chain. Shared workspace verification also
reconciles policy, claim heads and history, evidence projections, transactions, and
audit decisions, then rehashes every authoritative artifact. It runs before mutation,
before exact replay returns, and through `audit verify`. Corruption, policy mismatch,
projection inconsistency, evidence replacement, and artifact mismatch stop the affected
operation; an empty workspace remains valid.

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
