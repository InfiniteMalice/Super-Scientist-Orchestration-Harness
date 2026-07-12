# Epistemic Kernel Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first independently testable vertical slice: typed identities and proposals, immutable evidence, atomic claims, deterministic admission, tamper-evident audit, SQLite persistence, and a minimal CLI that runs without a model API, network, shell, or GPU.

**Architecture:** Implement a pure Pydantic domain and kernel layer first, then place SQLAlchemy repositories and a content-addressed artifact provider behind kernel-owned protocols. Application services prepare artifacts and submit typed proposals; one SQLite `BEGIN IMMEDIATE` transaction performs idempotency checks, admission, projection updates, and audit append atomically.

**Tech Stack:** Python 3.12+, Pydantic v2, SQLAlchemy 2.x, Alembic, Typer, pytest, Hypothesis, Ruff, mypy, coverage, Bandit, pip-audit, build, and twine.

## Global Constraints

- Follow `docs/superpowers/specs/2026-07-11-super-scientist-foundation-design.md`.
- Preserve citations `[S02]`, `[S07]`, and `[S12]` where provenance materially affects architecture or algorithms.
- Core installation and this plan's tests require no network, paid API, model SDK, shell executor, GPU, or training library.
- Models and providers may propose; only deterministic kernel code may admit and commit.
- Evidence bytes and audit history are append-only and cannot be silently overwritten.
- All timestamps are timezone-aware UTC values supplied through a `Clock` protocol.
- All hashes use SHA-256 over bytes or RFC 8785-style deterministic JSON produced by the project's canonical encoder.
- Model-assisted checks can request independent review but cannot return deterministic certification.
- A proposer cannot approve its own proposal.
- Rejections are durable decisions with stable reason codes, not infrastructure exceptions.
- Use TDD for every behavioral change and commit after each task.
- This plan does not implement hypotheses, experiments, run orchestration, hierarchical memory, QLoRA, or the full release quality suite; each belongs to a later approved subsystem plan.

---

## File Map

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | Build metadata, dependencies, CLI entry point, and tool configuration |
| `src/super_scientist/__init__.py` | Package version export |
| `src/super_scientist/domain/primitives.py` | UTC timestamps, canonical JSON, SHA-256, identifiers |
| `src/super_scientist/domain/identity.py` | Actor identity and configuration-aware independence |
| `src/super_scientist/config/models.py` | Runtime and immutable governance policy schemas |
| `src/super_scientist/config/loader.py` | Typed configuration loading and policy hashing |
| `src/super_scientist/domain/evidence/models.py` | Evidence candidates, records, spans, and verification state |
| `src/super_scientist/providers/storage/artifacts.py` | Content-addressed artifact protocol and filesystem implementation |
| `src/super_scientist/kernel/audit/models.py` | Audit event and verification result contracts |
| `src/super_scientist/kernel/audit/chain.py` | Audit event hashing, append, and verification |
| `src/super_scientist/domain/claims/models.py` | Atomic claims, evidence links, status, and lineage |
| `src/super_scientist/domain/claims/transitions.py` | Declarative claim status transition policy |
| `src/super_scientist/evaluation/claim_drift/models.py` | Typed validator outcomes |
| `src/super_scientist/evaluation/claim_drift/deterministic.py` | Evidence, span, scope, and modality validators |
| `src/super_scientist/kernel/transactions/models.py` | Proposal union, decision envelope, and reason codes |
| `src/super_scientist/kernel/admission/engine.py` | Pure deterministic proposal admission |
| `src/super_scientist/providers/storage/schema.py` | SQLAlchemy tables and append-only database triggers |
| `src/super_scientist/providers/storage/database.py` | SQLite engine creation and transaction boundary |
| `src/super_scientist/providers/storage/repositories.py` | Evidence, claim, transaction, policy, and audit repositories |
| `alembic.ini` | Migration configuration |
| `alembic/env.py` | Migration environment using project metadata |
| `alembic/versions/0001_epistemic_kernel.py` | Initial schema and append-only triggers |
| `src/super_scientist/application/kernel_service.py` | Artifact preparation and transactional application use cases |
| `src/super_scientist/cli/main.py` | Typer root application and JSON result rendering |
| `src/super_scientist/cli/output.py` | Stable human and JSON decision envelopes |
| `src/super_scientist/cli/kernel.py` | Init, evidence, claim, transaction, and audit commands |
| `src/super_scientist/quality/runner.py` | Fixed developer quality-check registry |
| `tests/unit/` | Pure domain and kernel tests |
| `tests/property/` | Append-only, chain, transition, and idempotency properties |
| `tests/integration/` | SQLite, migration, application, and CLI tests |
| `tests/e2e/test_kernel_vertical_slice.py` | Offline kernel workflow |

---

### Task 1: Package and Test Foundation

**Files:**
- Create: `pyproject.toml`
- Create: `src/super_scientist/__init__.py`
- Create: `tests/unit/test_package.py`

**Interfaces:**
- Consumes: none
- Produces: importable `super_scientist` package and `__version__: str`

- [ ] **Step 1: Write the failing package test**

```python
from super_scientist import __version__


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 2: Run the test before creating the package**

Run: `python -m pytest tests/unit/test_package.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'super_scientist'`.

- [ ] **Step 3: Create package metadata and the package root**

Create `pyproject.toml` with this exact baseline:

```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "super-scientist-orchestration-harness"
version = "0.1.0"
description = "Transactional orchestration foundation for auditable scientific research"
requires-python = ">=3.12"
license = { file = "LICENSE" }
authors = [{ name = "InfiniteMalice" }]
dependencies = [
  "alembic>=1.13,<2",
  "pydantic>=2,<3",
  "sqlalchemy>=2,<3",
  "typer>=0.12,<1",
]

[project.optional-dependencies]
dev = [
  "bandit>=1.7,<2",
  "build>=1.2,<2",
  "hypothesis>=6,<7",
  "mypy>=1.11,<2",
  "pip-audit>=2.7,<3",
  "pytest>=8,<9",
  "pytest-cov>=5,<7",
  "ruff>=0.9,<1",
  "twine>=5,<7",
]

[project.scripts]
scientist-harness = "super_scientist.cli.main:main"

[tool.hatch.build.targets.wheel]
packages = ["src/super_scientist"]

[tool.pytest.ini_options]
addopts = "--strict-markers --strict-config"
testpaths = ["tests"]
markers = [
  "integration: tests using SQLite or filesystem boundaries",
  "property: property-based tests",
  "e2e: deterministic end-to-end workflows",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["super_scientist"]

[tool.coverage.run]
branch = true
source = ["super_scientist"]

[tool.coverage.report]
fail_under = 90
show_missing = true
skip_covered = true
```

Create `src/super_scientist/__init__.py`:

```python
"""Transactional orchestration foundation for scientific research."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Install the editable development environment and rerun the test**

Run: `python -m pip install -e ".[dev]"`

Expected: installation succeeds without installing model or training packages.

Run: `python -m pytest tests/unit/test_package.py -v`

Expected: one test passes.

- [ ] **Step 5: Run initial static checks**

Run: `python -m ruff check src tests && python -m mypy src`

Expected: both commands exit zero.

- [ ] **Step 6: Commit the foundation**

```bash
git add pyproject.toml src/super_scientist/__init__.py tests/unit/test_package.py
git commit -m "build: initialize typed Python package"
```

---

### Task 2: Deterministic Primitives and Actor Identity

**Files:**
- Create: `src/super_scientist/domain/primitives.py`
- Create: `src/super_scientist/domain/identity.py`
- Create: `tests/unit/domain/test_primitives.py`
- Create: `tests/unit/domain/test_identity.py`

**Interfaces:**
- Consumes: Pydantic v2
- Produces: `canonical_json_bytes(value) -> bytes`, `sha256_hex(data) -> str`, `UtcTimestamp`, `ActorIdentity`, and `are_independent(left, right) -> bool`

- [ ] **Step 1: Write failing canonicalization and identity tests**

```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from super_scientist.domain.identity import ActorIdentity, ActorKind, are_independent
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_sha256_hex_hashes_bytes() -> None:
    assert sha256_hex(b"evidence") == (
        "ee8250fb76e094b34b471f13a73dbbe51d1ae142e9df59d7c0d31ec20f0a0a8e"
    )


def test_actor_timestamp_must_be_utc() -> None:
    with pytest.raises(ValidationError):
        ActorIdentity(
            actor_id="actor-1",
            kind=ActorKind.HUMAN,
            created_at=datetime(2026, 7, 11),
        )


def test_same_model_and_adapter_are_not_independent() -> None:
    left = ActorIdentity.model("a", "provider", "model", "adapter", datetime.now(UTC))
    right = ActorIdentity.model("b", "provider", "model", "adapter", datetime.now(UTC))
    assert not are_independent(left, right)
```

- [ ] **Step 2: Run tests and verify missing modules**

Run: `python -m pytest tests/unit/domain/test_primitives.py tests/unit/domain/test_identity.py -v`

Expected: collection fails because the domain modules do not exist.

- [ ] **Step 3: Implement deterministic primitives**

Create `src/super_scientist/domain/primitives.py`:

```python
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, NewType

from pydantic import AfterValidator

EvidenceId = NewType("EvidenceId", str)
ClaimId = NewType("ClaimId", str)
TransactionId = NewType("TransactionId", str)
ActorId = NewType("ActorId", str)


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


UtcTimestamp = Annotated[datetime, AfterValidator(require_utc)]


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
```

- [ ] **Step 4: Implement configuration-aware actor identity**

Create `src/super_scientist/domain/identity.py`:

```python
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from super_scientist.domain.primitives import UtcTimestamp


class ActorKind(StrEnum):
    HUMAN = "human"
    MODEL = "model"
    TOOL = "tool"
    SERVICE = "service"


class ActorIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor_id: str
    kind: ActorKind
    created_at: UtcTimestamp
    provider_id: str | None = None
    model_id: str | None = None
    adapter_id: str | None = None
    configuration_hash: str | None = None

    @classmethod
    def model(
        cls,
        actor_id: str,
        provider_id: str,
        model_id: str,
        adapter_id: str | None,
        created_at: UtcTimestamp,
    ) -> ActorIdentity:
        return cls(
            actor_id=actor_id,
            kind=ActorKind.MODEL,
            provider_id=provider_id,
            model_id=model_id,
            adapter_id=adapter_id,
            created_at=created_at,
        )


def are_independent(left: ActorIdentity, right: ActorIdentity) -> bool:
    if left.actor_id == right.actor_id:
        return False
    if left.kind is ActorKind.MODEL and right.kind is ActorKind.MODEL:
        return (
            left.provider_id,
            left.model_id,
            left.adapter_id,
            left.configuration_hash,
        ) != (
            right.provider_id,
            right.model_id,
            right.adapter_id,
            right.configuration_hash,
        )
    return True
```

- [ ] **Step 5: Pass tests with the fixed SHA-256 vector**

Run: `python -m pytest tests/unit/domain/test_primitives.py tests/unit/domain/test_identity.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit primitives and identity**

```bash
git add src/super_scientist/domain tests/unit/domain
git commit -m "feat: add deterministic primitives and actor identity"
```

---

### Task 3: Typed Configuration and Governance Policy Hashing

**Files:**
- Create: `src/super_scientist/config/models.py`
- Create: `src/super_scientist/config/loader.py`
- Create: `tests/unit/config/test_loader.py`

**Interfaces:**
- Consumes: `canonical_json_bytes`, `sha256_hex`
- Produces: `RuntimeSettings`, `GovernancePolicy`, `PolicySnapshot`, and `load_policy(path) -> PolicySnapshot`

- [ ] **Step 1: Write failing policy tests**

```python
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from super_scientist.config.loader import load_policy
from super_scientist.config.models import GovernancePolicy


def test_policy_hash_is_content_addressed(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "required_claim_checks": ["source_exists", "evidence_span_exists"],
                "human_approval_for": ["governance_change", "adapter_promotion"],
            }
        ),
        encoding="utf-8",
    )
    first = load_policy(path)
    second = load_policy(path)
    assert first.policy_hash == second.policy_hash
    assert first.policy == second.policy


def test_policy_rejects_empty_required_checks() -> None:
    with pytest.raises(ValidationError):
        GovernancePolicy(
            schema_version=1,
            required_claim_checks=[],
            human_approval_for={"governance_change", "adapter_promotion"},
        )
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/unit/config/test_loader.py -v`

Expected: collection fails because configuration modules do not exist.

- [ ] **Step 3: Implement immutable settings and governance schemas**

Create `src/super_scientist/config/models.py`:

```python
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: str = "sqlite:///scientist-harness.db"
    artifact_root: Path = Path("artifacts")
    policy_path: Path = Path("governance-policy.json")


class GovernancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    required_claim_checks: list[str] = Field(min_length=1)
    human_approval_for: set[str] = Field(
        default_factory=lambda: {"governance_change", "adapter_promotion"}
    )


class PolicySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_hash: str
    policy: GovernancePolicy
```

Create `src/super_scientist/config/loader.py`:

```python
import json
from pathlib import Path

from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.primitives import canonical_json_bytes, sha256_hex


def load_policy(path: Path) -> PolicySnapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    policy = GovernancePolicy.model_validate(raw)
    canonical = canonical_json_bytes(policy.model_dump(mode="json"))
    return PolicySnapshot(policy_hash=sha256_hex(canonical), policy=policy)
```

- [ ] **Step 4: Pass tests and type checks**

Run: `python -m pytest tests/unit/config/test_loader.py -v && python -m mypy src`

Expected: tests and mypy pass.

- [ ] **Step 5: Commit configuration contracts**

```bash
git add src/super_scientist/config tests/unit/config
git commit -m "feat: add hashed governance policy configuration"
```

---

### Task 4: Content-Addressed Artifacts and Immutable Evidence

**Files:**
- Create: `src/super_scientist/domain/evidence/models.py`
- Create: `src/super_scientist/providers/storage/artifacts.py`
- Create: `tests/unit/evidence/test_models.py`
- Create: `tests/integration/storage/test_artifacts.py`
- Create: `tests/property/test_artifact_immutability.py`

**Interfaces:**
- Consumes: `UtcTimestamp`, `sha256_hex`
- Produces: `ArtifactRef`, `EvidenceRecord`, `EvidenceSpan`, `ArtifactStore`, and `FileArtifactStore.put(data) -> ArtifactRef`

- [ ] **Step 1: Write failing evidence and artifact tests**

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from super_scientist.domain.evidence.models import EvidenceRecord, EvidenceSpan
from super_scientist.providers.storage.artifacts import FileArtifactStore


def test_artifact_put_is_content_addressed(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    first = store.put(b"raw evidence", "text/plain")
    second = store.put(b"raw evidence", "text/plain")
    assert first == second
    assert store.read(first) == b"raw evidence"


def test_artifact_store_rejects_corrupted_existing_blob(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(b"original", "application/octet-stream")
    path = store.resolve(ref)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        store.put(b"original", "application/octet-stream")


def test_evidence_span_must_fit_extracted_text() -> None:
    with pytest.raises(ValueError, match="span offsets"):
        EvidenceSpan(start=0, end=10, text="short")


def test_evidence_record_hash_matches_artifact(tmp_path: Path) -> None:
    store_ref = FileArtifactStore(tmp_path).put(b"abc", "text/plain")
    record = EvidenceRecord(
        evidence_id="ev-1",
        evidence_type="document",
        source_locator="fixture://one",
        retrieved_at=datetime.now(UTC),
        artifact=store_ref,
        provenance={"collector": "test"},
        ingestion_actor_id="actor-1",
    )
    assert record.content_hash == store_ref.sha256
```

- [ ] **Step 2: Run tests and verify missing implementations**

Run: `python -m pytest tests/unit/evidence tests/integration/storage -v`

Expected: collection fails because evidence and storage modules do not exist.

- [ ] **Step 3: Implement evidence contracts**

Create `src/super_scientist/domain/evidence/models.py`:

```python
from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from super_scientist.domain.primitives import UtcTimestamp


class VerificationState(StrEnum):
    UNVERIFIED = "unverified"
    HASH_VERIFIED = "hash_verified"
    UNAVAILABLE = "unavailable"


class ArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    relative_path: str


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    text: str

    @model_validator(mode="after")
    def validate_bounds(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("span end must be greater than start")
        if self.end - self.start != len(self.text):
            raise ValueError("span offsets must match extracted text length")
        return self


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    evidence_type: str
    source_locator: str
    retrieved_at: UtcTimestamp
    artifact: ArtifactRef
    extracted_span: EvidenceSpan | None = None
    structured_observation: dict[str, Any] | None = None
    provenance: dict[str, str]
    license: str | None = None
    ingestion_actor_id: str
    verification_state: VerificationState = VerificationState.HASH_VERIFIED

    @property
    def content_hash(self) -> str:
        return self.artifact.sha256
```

- [ ] **Step 4: Implement the filesystem artifact store**

Create `src/super_scientist/providers/storage/artifacts.py`:

```python
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol

from super_scientist.domain.evidence.models import ArtifactRef
from super_scientist.domain.primitives import sha256_hex


class ArtifactStore(Protocol):
    def put(self, data: bytes, media_type: str) -> ArtifactRef:
        raise NotImplementedError

    def read(self, ref: ArtifactRef) -> bytes:
        raise NotImplementedError


class FileArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, media_type: str) -> ArtifactRef:
        digest = sha256_hex(data)
        relative = Path("sha256") / digest[:2] / digest
        target = self._contained(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_symlink() or sha256_hex(target.read_bytes()) != digest:
                raise ValueError("artifact hash mismatch for existing content address")
        else:
            descriptor, temporary_name = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, target)
            finally:
                Path(temporary_name).unlink(missing_ok=True)
        return ArtifactRef(
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
            relative_path=relative.as_posix(),
        )

    def read(self, ref: ArtifactRef) -> bytes:
        path = self.resolve(ref)
        data = path.read_bytes()
        if len(data) != ref.size_bytes or sha256_hex(data) != ref.sha256:
            raise ValueError("artifact hash mismatch")
        return data

    def resolve(self, ref: ArtifactRef) -> Path:
        return self._contained(Path(ref.relative_path))

    def _contained(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact path escapes configured root")
        resolved = (self._root / relative).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError("artifact path escapes configured root")
        return resolved
```

- [ ] **Step 5: Add a property test for repeated writes**

```python
from pathlib import Path

from hypothesis import given, strategies as st

from super_scientist.providers.storage.artifacts import FileArtifactStore


@given(st.binary(max_size=4096))
def test_repeated_put_never_changes_artifact(tmp_path: Path, payload: bytes) -> None:
    store = FileArtifactStore(tmp_path)
    ref = store.put(payload, "application/octet-stream")
    store.put(payload, "application/octet-stream")
    assert store.read(ref) == payload
```

- [ ] **Step 6: Run all artifact and evidence tests**

Run: `python -m pytest tests/unit/evidence tests/integration/storage tests/property/test_artifact_immutability.py -v`

Expected: all tests pass, including empty and binary payload examples.

- [ ] **Step 7: Commit immutable evidence storage**

```bash
git add src/super_scientist/domain/evidence src/super_scientist/providers/storage tests/unit/evidence tests/integration/storage tests/property/test_artifact_immutability.py
git commit -m "feat: add immutable evidence artifact storage"
```

---

### Task 5: Tamper-Evident Audit Chain

**Files:**
- Create: `src/super_scientist/kernel/audit/models.py`
- Create: `src/super_scientist/kernel/audit/chain.py`
- Create: `tests/unit/audit/test_chain.py`
- Create: `tests/property/test_audit_chain.py`

**Interfaces:**
- Consumes: canonical JSON and SHA-256 primitives
- Produces: `AuditEvent`, `AuditVerification`, `append_event(previous, event_id, event_type, payload, occurred_at)`, and `verify_chain(events)`

- [ ] **Step 1: Write failing chain and tampering tests**

```python
from datetime import UTC, datetime

from super_scientist.kernel.audit.chain import append_event, verify_chain


def test_audit_chain_verifies_in_order() -> None:
    first = append_event(None, "event-1", "transaction", {"decision": "accepted"}, datetime.now(UTC))
    second = append_event(first, "event-2", "transaction", {"decision": "rejected"}, datetime.now(UTC))
    result = verify_chain([first, second])
    assert result.valid
    assert result.checked_events == 2


def test_audit_chain_detects_payload_tampering() -> None:
    event = append_event(None, "event-1", "transaction", {"decision": "accepted"}, datetime.now(UTC))
    tampered = event.model_copy(update={"payload": {"decision": "rejected"}})
    result = verify_chain([tampered])
    assert not result.valid
    assert result.first_invalid_sequence == 1
```

- [ ] **Step 2: Run tests and verify missing audit modules**

Run: `python -m pytest tests/unit/audit/test_chain.py -v`

Expected: collection fails because audit modules do not exist.

- [ ] **Step 3: Implement immutable audit contracts**

Create `src/super_scientist/kernel/audit/models.py`:

```python
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from super_scientist.domain.primitives import UtcTimestamp

GENESIS_HASH = "0" * 64


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(ge=1)
    event_id: str
    event_type: str
    schema_version: int = 1
    occurred_at: UtcTimestamp
    payload: dict[str, Any]
    payload_hash: str
    previous_hash: str
    event_hash: str


class AuditVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    checked_events: int
    first_invalid_sequence: int | None = None
    reason: str | None = None
```

- [ ] **Step 4: Implement append and verification**

Create `src/super_scientist/kernel/audit/chain.py`:

```python
from collections.abc import Iterable
from typing import Any

from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes, sha256_hex
from super_scientist.kernel.audit.models import GENESIS_HASH, AuditEvent, AuditVerification


def append_event(
    previous: AuditEvent | None,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: UtcTimestamp,
) -> AuditEvent:
    sequence = 1 if previous is None else previous.sequence + 1
    previous_hash = GENESIS_HASH if previous is None else previous.event_hash
    payload_hash = sha256_hex(canonical_json_bytes(payload))
    envelope = {
        "sequence": sequence,
        "event_id": event_id,
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "payload_hash": payload_hash,
        "previous_hash": previous_hash,
    }
    return AuditEvent(
        **envelope,
        payload=payload,
        event_hash=sha256_hex(canonical_json_bytes(envelope)),
    )


def verify_chain(events: Iterable[AuditEvent]) -> AuditVerification:
    previous: AuditEvent | None = None
    checked = 0
    for event in events:
        expected = append_event(
            previous,
            event.event_id,
            event.event_type,
            event.payload,
            event.occurred_at,
        )
        checked += 1
        if expected != event:
            return AuditVerification(
                valid=False,
                checked_events=checked,
                first_invalid_sequence=event.sequence,
                reason="audit event hash or linkage mismatch",
            )
        previous = event
    return AuditVerification(valid=True, checked_events=checked)
```

- [ ] **Step 5: Add a property test that any payload mutation is detected**

```python
from datetime import UTC, datetime

from hypothesis import given, strategies as st

from super_scientist.kernel.audit.chain import append_event, verify_chain


@given(st.dictionaries(st.text(min_size=1), st.integers(), min_size=1, max_size=8))
def test_payload_mutation_breaks_chain(payload: dict[str, int]) -> None:
    event = append_event(None, "event-1", "property", payload, datetime.now(UTC))
    changed = dict(payload)
    first_key = next(iter(changed))
    changed[first_key] += 1
    tampered = event.model_copy(update={"payload": changed})
    assert not verify_chain([tampered]).valid
```

- [ ] **Step 6: Run audit tests**

Run: `python -m pytest tests/unit/audit tests/property/test_audit_chain.py -v`

Expected: all tests pass.

- [ ] **Step 7: Commit the audit chain**

```bash
git add src/super_scientist/kernel/audit tests/unit/audit tests/property/test_audit_chain.py
git commit -m "feat: add tamper-evident audit chain"
```

---

### Task 6: Atomic Claims, Status Policy, and Deterministic Drift Checks

**Files:**
- Create: `src/super_scientist/domain/claims/models.py`
- Create: `src/super_scientist/domain/claims/transitions.py`
- Create: `src/super_scientist/evaluation/claim_drift/models.py`
- Create: `src/super_scientist/evaluation/claim_drift/deterministic.py`
- Create: `tests/unit/claims/test_transitions.py`
- Create: `tests/unit/evaluation/test_claim_drift.py`
- Create: `tests/property/test_claim_transitions.py`

**Interfaces:**
- Consumes: evidence models and actor identity
- Produces: `AtomicClaim`, `ClaimStatus`, `EvidenceLink`, `validate_transition`, `CheckResult`, and `run_deterministic_checks`

- [ ] **Step 1: Write failing transition and drift tests**

```python
from super_scientist.domain.claims.models import ClaimStatus, EvidenceLink
from super_scientist.domain.claims.transitions import validate_transition
from super_scientist.evaluation.claim_drift.deterministic import check_evidence_link
from super_scientist.evaluation.claim_drift.models import CheckOutcome


def test_claim_cannot_skip_evidence_linked() -> None:
    result = validate_transition(ClaimStatus.PROPOSED, ClaimStatus.CORROBORATED)
    assert not result.allowed
    assert result.reason == "illegal claim status transition"


def test_claim_can_be_withdrawn_from_proposed() -> None:
    assert validate_transition(ClaimStatus.PROPOSED, ClaimStatus.WITHDRAWN).allowed


def test_missing_evidence_fails_deterministically() -> None:
    link = EvidenceLink(evidence_id="missing", supporting_span="span")
    result = check_evidence_link(link, evidence_by_id={})
    assert result.outcome is CheckOutcome.FAIL_DETERMINISTIC
    assert result.code == "source_exists"
```

- [ ] **Step 2: Run tests and verify missing modules**

Run: `python -m pytest tests/unit/claims tests/unit/evaluation -v`

Expected: collection fails because claim modules do not exist.

- [ ] **Step 3: Implement claim contracts**

Create `src/super_scientist/domain/claims/models.py`:

```python
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from super_scientist.domain.primitives import UtcTimestamp


class ClaimStatus(StrEnum):
    PROPOSED = "PROPOSED"
    EVIDENCE_LINKED = "EVIDENCE_LINKED"
    TESTABLE = "TESTABLE"
    REPRODUCED = "REPRODUCED"
    CORROBORATED = "CORROBORATED"
    CONSTRAINT_VALIDATED = "CONSTRAINT_VALIDATED"
    FALSIFIED = "FALSIFIED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class EvidenceLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    supporting_span: str = Field(min_length=1)


class AtomicClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_id: str
    version: int = Field(ge=1)
    proposition: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    population_or_system: str = Field(min_length=1)
    epistemic_modality: str = Field(min_length=1)
    status: ClaimStatus
    evidence_links: tuple[EvidenceLink, ...] = ()
    assumptions: tuple[str, ...] = ()
    parent_version_id: str | None = None
    created_at: UtcTimestamp
    created_by: str
```

- [ ] **Step 4: Implement the explicit transition graph**

Create `src/super_scientist/domain/claims/transitions.py`:

```python
from pydantic import BaseModel, ConfigDict

from super_scientist.domain.claims.models import ClaimStatus

TERMINAL = {ClaimStatus.SUPERSEDED, ClaimStatus.WITHDRAWN}
ALLOWED: dict[ClaimStatus, set[ClaimStatus]] = {
    ClaimStatus.PROPOSED: {
        ClaimStatus.EVIDENCE_LINKED,
        ClaimStatus.FALSIFIED,
        ClaimStatus.WITHDRAWN,
    },
    ClaimStatus.EVIDENCE_LINKED: {
        ClaimStatus.TESTABLE,
        ClaimStatus.CORROBORATED,
        ClaimStatus.CONSTRAINT_VALIDATED,
        ClaimStatus.FALSIFIED,
        ClaimStatus.WITHDRAWN,
    },
    ClaimStatus.TESTABLE: {
        ClaimStatus.REPRODUCED,
        ClaimStatus.CORROBORATED,
        ClaimStatus.CONSTRAINT_VALIDATED,
        ClaimStatus.FALSIFIED,
        ClaimStatus.WITHDRAWN,
    },
    ClaimStatus.REPRODUCED: {
        ClaimStatus.CORROBORATED,
        ClaimStatus.FALSIFIED,
        ClaimStatus.SUPERSEDED,
        ClaimStatus.WITHDRAWN,
    },
    ClaimStatus.CORROBORATED: {
        ClaimStatus.FALSIFIED,
        ClaimStatus.SUPERSEDED,
        ClaimStatus.WITHDRAWN,
    },
    ClaimStatus.CONSTRAINT_VALIDATED: {
        ClaimStatus.CORROBORATED,
        ClaimStatus.FALSIFIED,
        ClaimStatus.SUPERSEDED,
        ClaimStatus.WITHDRAWN,
    },
    ClaimStatus.FALSIFIED: {ClaimStatus.SUPERSEDED},
    ClaimStatus.SUPERSEDED: set(),
    ClaimStatus.WITHDRAWN: set(),
}


class TransitionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    reason: str | None = None


def validate_transition(current: ClaimStatus, target: ClaimStatus) -> TransitionResult:
    if target in ALLOWED[current]:
        return TransitionResult(allowed=True)
    return TransitionResult(allowed=False, reason="illegal claim status transition")
```

- [ ] **Step 5: Implement deterministic check results and evidence validation**

Create `src/super_scientist/evaluation/claim_drift/models.py`:

```python
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CheckOutcome(StrEnum):
    PASS_DETERMINISTIC = "PASS_DETERMINISTIC"
    FAIL_DETERMINISTIC = "FAIL_DETERMINISTIC"
    REQUIRES_INDEPENDENT_REVIEW = "REQUIRES_INDEPENDENT_REVIEW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    outcome: CheckOutcome
    reason: str
    validator_version: str = "1"
```

Create `src/super_scientist/evaluation/claim_drift/deterministic.py`:

```python
from collections.abc import Mapping

from super_scientist.domain.claims.models import AtomicClaim, EvidenceLink
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.evaluation.claim_drift.models import CheckOutcome, CheckResult


def check_evidence_link(
    link: EvidenceLink,
    evidence_by_id: Mapping[str, EvidenceRecord],
) -> CheckResult:
    evidence = evidence_by_id.get(link.evidence_id)
    if evidence is None:
        return CheckResult(
            code="source_exists",
            outcome=CheckOutcome.FAIL_DETERMINISTIC,
            reason="linked evidence does not exist",
        )
    if evidence.extracted_span is None or link.supporting_span not in evidence.extracted_span.text:
        return CheckResult(
            code="evidence_span_exists",
            outcome=CheckOutcome.FAIL_DETERMINISTIC,
            reason="supporting span is unavailable in linked evidence",
        )
    return CheckResult(
        code="evidence_link",
        outcome=CheckOutcome.PASS_DETERMINISTIC,
        reason="linked evidence and exact span exist",
    )


def run_deterministic_checks(
    claim: AtomicClaim,
    evidence_by_id: Mapping[str, EvidenceRecord],
) -> tuple[CheckResult, ...]:
    checks = tuple(check_evidence_link(link, evidence_by_id) for link in claim.evidence_links)
    if not checks:
        return (
            CheckResult(
                code="source_exists",
                outcome=CheckOutcome.FAIL_DETERMINISTIC,
                reason="claim has no evidence links",
            ),
        )
    return checks
```

- [ ] **Step 6: Add a property test covering every illegal edge**

```python
from hypothesis import given, strategies as st

from super_scientist.domain.claims.models import ClaimStatus
from super_scientist.domain.claims.transitions import ALLOWED, validate_transition


@given(st.sampled_from(list(ClaimStatus)), st.sampled_from(list(ClaimStatus)))
def test_transition_policy_matches_declared_graph(
    current: ClaimStatus,
    target: ClaimStatus,
) -> None:
    assert validate_transition(current, target).allowed is (target in ALLOWED[current])
```

- [ ] **Step 7: Run claim tests and commit**

Run: `python -m pytest tests/unit/claims tests/unit/evaluation tests/property/test_claim_transitions.py -v`

Expected: all tests pass.

```bash
git add src/super_scientist/domain/claims src/super_scientist/evaluation tests/unit/claims tests/unit/evaluation tests/property/test_claim_transitions.py
git commit -m "feat: add atomic claims and deterministic drift checks"
```

---

### Task 7: Typed Proposals and Pure Admission Engine

**Files:**
- Create: `src/super_scientist/kernel/transactions/models.py`
- Create: `src/super_scientist/kernel/admission/engine.py`
- Create: `tests/unit/admission/test_engine.py`
- Create: `tests/property/test_admission_idempotency.py`

**Interfaces:**
- Consumes: actor identity, evidence, claims, transitions, claim-drift checks, policy snapshot
- Produces: discriminated `Proposal`, `TransactionDecision`, `RejectionCode`, `AdmissionContext`, and `AdmissionEngine.decide(proposal, context)`

- [ ] **Step 1: Write failing authority and evidence admission tests**

```python
from datetime import UTC, datetime

from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.admission.engine import AdmissionContext, AdmissionEngine
from super_scientist.kernel.transactions.models import (
    Approval,
    ProposeClaim,
    RejectionCode,
)


def actor(actor_id: str) -> ActorIdentity:
    return ActorIdentity(actor_id=actor_id, kind=ActorKind.HUMAN, created_at=datetime.now(UTC))


def context() -> AdmissionContext:
    return AdmissionContext(
        active_policy=PolicySnapshot(
            policy_hash="a" * 64,
            policy=GovernancePolicy(
                required_claim_checks=["source_exists", "evidence_span_exists"]
            ),
        ),
        evidence_by_id={},
        claim_by_id={},
        prior_decision_by_idempotency_key={},
    )


def test_proposer_cannot_approve_own_claim() -> None:
    proposal = ProposeClaim(
        proposal_id="p-1",
        idempotency_key="k-1",
        proposer=actor("same"),
        approval=Approval(approver=actor("same"), approved_at=datetime.now(UTC)),
        claim={
            "claim_id": "c-1",
            "version": 1,
            "proposition": "x",
            "scope": "toy",
            "population_or_system": "fixture",
            "epistemic_modality": "observed",
            "status": "PROPOSED",
            "created_at": datetime.now(UTC),
            "created_by": "same",
        },
    )
    decision = AdmissionEngine().decide(proposal, context())
    assert not decision.accepted
    assert decision.reasons[0].code is RejectionCode.SELF_APPROVAL


def test_replay_returns_prior_decision() -> None:
    prior = AdmissionEngine.rejected("p-old", RejectionCode.PERMISSION_DENIED, "denied")
    current = context().model_copy(
        update={"prior_decision_by_idempotency_key": {"k-1": prior}}
    )
    proposal = ProposeClaim.model_construct(
        proposal_id="p-1", idempotency_key="k-1", proposer=actor("a")
    )
    replay = AdmissionEngine().decide(proposal, current)
    assert replay.replayed
    assert replay.model_copy(update={"replayed": False}) == prior
```

- [ ] **Step 2: Run tests and verify missing transaction modules**

Run: `python -m pytest tests/unit/admission/test_engine.py -v`

Expected: collection fails because transaction and admission modules do not exist.

- [ ] **Step 3: Implement proposal and decision contracts**

Create `src/super_scientist/kernel/transactions/models.py`:

```python
from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.identity import ActorIdentity
from super_scientist.domain.primitives import UtcTimestamp


class RejectionCode(StrEnum):
    SELF_APPROVAL = "SELF_APPROVAL"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    EVIDENCE_HASH_MISMATCH = "EVIDENCE_HASH_MISMATCH"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    INDEPENDENT_REVIEW_REQUIRED = "INDEPENDENT_REVIEW_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    POLICY_HASH_MISMATCH = "POLICY_HASH_MISMATCH"


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True)

    approver: ActorIdentity
    approved_at: UtcTimestamp


class ProposalBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: str
    idempotency_key: str
    proposer: ActorIdentity
    approval: Approval | None = None


class AddEvidence(ProposalBase):
    proposal_type: Literal["add_evidence"] = "add_evidence"
    evidence: EvidenceRecord


class ProposeClaim(ProposalBase):
    proposal_type: Literal["propose_claim"] = "propose_claim"
    claim: AtomicClaim


class TransitionClaim(ProposalBase):
    proposal_type: Literal["transition_claim"] = "transition_claim"
    claim_id: str
    expected_version: int
    target_status: ClaimStatus


Proposal = Annotated[
    AddEvidence | ProposeClaim | TransitionClaim,
    Field(discriminator="proposal_type"),
]


class RejectionReason(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: RejectionCode
    message: str


class TransactionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal_id: str
    accepted: bool
    replayed: bool = False
    reasons: tuple[RejectionReason, ...] = ()
```

- [ ] **Step 4: Implement pure deterministic admission**

Create `src/super_scientist/kernel/admission/engine.py`:

```python
from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.claims.transitions import validate_transition
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.evaluation.claim_drift.deterministic import run_deterministic_checks
from super_scientist.evaluation.claim_drift.models import CheckOutcome
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    ProposeClaim,
    RejectionCode,
    RejectionReason,
    TransactionDecision,
    TransitionClaim,
)


class AdmissionContext(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    active_policy: PolicySnapshot
    evidence_by_id: Mapping[str, EvidenceRecord]
    claim_by_id: Mapping[str, AtomicClaim]
    prior_decision_by_idempotency_key: Mapping[str, TransactionDecision]


class AdmissionEngine:
    def decide(self, proposal: Proposal, context: AdmissionContext) -> TransactionDecision:
        prior = context.prior_decision_by_idempotency_key.get(proposal.idempotency_key)
        if prior is not None:
            return prior.model_copy(update={"replayed": True})
        if proposal.approval and proposal.approval.approver.actor_id == proposal.proposer.actor_id:
            return self.rejected(
                proposal.proposal_id,
                RejectionCode.SELF_APPROVAL,
                "proposer cannot approve its own proposal",
            )
        if isinstance(proposal, AddEvidence):
            return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
        if isinstance(proposal, ProposeClaim):
            if proposal.claim.status.value != "PROPOSED":
                return self.rejected(
                    proposal.proposal_id,
                    RejectionCode.INVALID_STATUS_TRANSITION,
                    "new claims must begin in PROPOSED",
                )
            return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
        if isinstance(proposal, TransitionClaim):
            current = context.claim_by_id.get(proposal.claim_id)
            if current is None:
                return self.rejected(
                    proposal.proposal_id,
                    RejectionCode.INVALID_STATUS_TRANSITION,
                    "claim does not exist",
                )
            if current.version != proposal.expected_version:
                return self.rejected(
                    proposal.proposal_id,
                    RejectionCode.INVALID_STATUS_TRANSITION,
                    "claim version does not match expected version",
                )
            transition = validate_transition(current.status, proposal.target_status)
            if not transition.allowed:
                return self.rejected(
                    proposal.proposal_id,
                    RejectionCode.INVALID_STATUS_TRANSITION,
                    transition.reason or "invalid transition",
                )
            checks = run_deterministic_checks(current, context.evidence_by_id)
            if any(check.outcome is CheckOutcome.FAIL_DETERMINISTIC for check in checks):
                return self.rejected(
                    proposal.proposal_id,
                    RejectionCode.MISSING_EVIDENCE,
                    "claim evidence checks failed",
                )
            return TransactionDecision(proposal_id=proposal.proposal_id, accepted=True)
        raise TypeError(f"unsupported proposal type: {type(proposal).__name__}")

    @staticmethod
    def rejected(
        proposal_id: str,
        code: RejectionCode,
        message: str,
    ) -> TransactionDecision:
        return TransactionDecision(
            proposal_id=proposal_id,
            accepted=False,
            reasons=(RejectionReason(code=code, message=message),),
        )
```

- [ ] **Step 5: Replace unsafe `model_construct` replay fixture with a valid proposal**

The replay test must construct a complete `ProposeClaim`; use a shared `claim(actor_id)` fixture returning a valid `AtomicClaim` and pass it to `ProposeClaim`. This keeps tests inside public validation boundaries rather than bypassing them.

```python
def claim(actor_id: str) -> AtomicClaim:
    return AtomicClaim(
        claim_id="c-1",
        version=1,
        proposition="fixture proposition",
        scope="toy",
        population_or_system="fixture",
        epistemic_modality="observed",
        status=ClaimStatus.PROPOSED,
        created_at=datetime.now(UTC),
        created_by=actor_id,
    )
```

- [ ] **Step 6: Run admission tests and add idempotency property coverage**

Create `tests/property/test_admission_idempotency.py`:

```python
from datetime import UTC, datetime

from hypothesis import given, strategies as st

from super_scientist.config.models import GovernancePolicy, PolicySnapshot
from super_scientist.domain.evidence.models import ArtifactRef, EvidenceRecord
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.admission.engine import AdmissionContext, AdmissionEngine
from super_scientist.kernel.transactions.models import AddEvidence, TransactionDecision


def valid_evidence_proposal(key: str) -> AddEvidence:
    proposer = ActorIdentity(
        actor_id="actor-1",
        kind=ActorKind.HUMAN,
        created_at=datetime(2026, 7, 11, tzinfo=UTC),
    )
    evidence = EvidenceRecord(
        evidence_id="ev-1",
        evidence_type="document",
        source_locator="fixture://one",
        retrieved_at=datetime(2026, 7, 11, tzinfo=UTC),
        artifact=ArtifactRef(
            sha256="a" * 64,
            size_bytes=1,
            media_type="text/plain",
            relative_path=f"sha256/aa/{'a' * 64}",
        ),
        provenance={"collector": "property-test"},
        ingestion_actor_id="actor-1",
    )
    return AddEvidence(
        proposal_id="p-1",
        idempotency_key=key,
        proposer=proposer,
        evidence=evidence,
    )


def empty_context(
    prior: dict[str, TransactionDecision] | None = None,
) -> AdmissionContext:
    return AdmissionContext(
        active_policy=PolicySnapshot(
            policy_hash="b" * 64,
            policy=GovernancePolicy(
                required_claim_checks=["source_exists", "evidence_span_exists"]
            ),
        ),
        evidence_by_id={},
        claim_by_id={},
        prior_decision_by_idempotency_key=prior or {},
    )


@given(st.text(min_size=1, max_size=32))
def test_same_idempotency_key_replays_same_decision(key: str) -> None:
    proposal = valid_evidence_proposal(key)
    engine = AdmissionEngine()
    first = engine.decide(proposal, empty_context())
    replay_context = empty_context(prior={key: first})
    replay = engine.decide(proposal, replay_context)
    assert replay.model_copy(update={"replayed": False}) == first
```

Run: `python -m pytest tests/unit/admission tests/property/test_admission_idempotency.py -v`

Expected: all tests pass.

- [ ] **Step 7: Commit proposals and admission**

```bash
git add src/super_scientist/kernel tests/unit/admission tests/property/test_admission_idempotency.py
git commit -m "feat: add typed proposal admission boundary"
```

---

### Task 8: SQLite Schema, Migration, and Repositories

**Files:**
- Create: `src/super_scientist/providers/storage/schema.py`
- Create: `src/super_scientist/providers/storage/database.py`
- Create: `src/super_scientist/providers/storage/repositories.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_epistemic_kernel.py`
- Create: `tests/integration/storage/test_migrations.py`
- Create: `tests/integration/storage/test_repositories.py`
- Create: `tests/property/test_database_append_only.py`

**Interfaces:**
- Consumes: evidence, claim, transaction, policy, and audit contracts
- Produces: `create_database_engine(url)`, `upgrade_database(url)`, `DatabaseUnitOfWork`, and repository methods used by `KernelService`

- [ ] **Step 1: Write failing migration and append-only tests**

```python
from pathlib import Path

import pytest
from sqlalchemy import text

from super_scientist.providers.storage.database import create_database_engine, upgrade_database


def test_initial_migration_creates_kernel_tables(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'kernel.db'}"
    upgrade_database(url)
    engine = create_database_engine(url)
    with engine.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
    assert {
        "governance_policies",
        "governance_state",
        "evidence_records",
        "claim_versions",
        "claim_heads",
        "transactions",
        "audit_events",
    } <= names


def test_evidence_rows_cannot_be_updated(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'kernel.db'}"
    upgrade_database(url)
    engine = create_database_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evidence_records "
                "(evidence_id, content_hash, record_json, created_at) "
                "VALUES ('ev-1', :digest, '{}', :created_at)"
            ),
            {"digest": "a" * 64, "created_at": "2026-07-11T00:00:00+00:00"},
        )
        with pytest.raises(Exception, match="append-only"):
            connection.execute(
                text("UPDATE evidence_records SET record_json = '{\"changed\":true}'")
            )
```

- [ ] **Step 2: Run tests and verify missing storage schema**

Run: `python -m pytest tests/integration/storage/test_migrations.py -v`

Expected: collection fails because database modules do not exist.

- [ ] **Step 3: Define SQLAlchemy metadata and tables**

Create `src/super_scientist/providers/storage/schema.py` with SQLAlchemy Core tables using these exact primary and uniqueness constraints:

```python
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, UniqueConstraint

metadata = MetaData()

governance_policies = Table(
    "governance_policies",
    metadata,
    Column("policy_hash", String(64), primary_key=True),
    Column("policy_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)

governance_state = Table(
    "governance_state",
    metadata,
    Column("singleton_id", Integer, primary_key=True),
    Column("active_policy_hash", String(64), nullable=False),
)

evidence_records = Table(
    "evidence_records",
    metadata,
    Column("evidence_id", String(128), primary_key=True),
    Column("content_hash", String(64), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)

claim_versions = Table(
    "claim_versions",
    metadata,
    Column("claim_version_id", String(160), primary_key=True),
    Column("claim_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("record_json", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("claim_id", "version", name="uq_claim_version"),
)

claim_heads = Table(
    "claim_heads",
    metadata,
    Column("claim_id", String(128), primary_key=True),
    Column("claim_version_id", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
)

transactions = Table(
    "transactions",
    metadata,
    Column("proposal_id", String(128), primary_key=True),
    Column("idempotency_key", String(128), nullable=False, unique=True),
    Column("proposal_hash", String(64), nullable=False),
    Column("proposal_json", Text, nullable=False),
    Column("decision_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(128), nullable=False, unique=True),
    Column("previous_hash", String(64), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("event_hash", String(64), nullable=False),
    Column("event_json", Text, nullable=False),
)
```

- [ ] **Step 4: Create the initial Alembic migration**

The migration imports no application services. It creates the six tables and these SQLite triggers after table creation:

```python
APPEND_ONLY_TABLES = (
    "governance_policies",
    "evidence_records",
    "claim_versions",
    "audit_events",
)


def create_append_only_triggers() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
        )
```

`downgrade()` drops the triggers before dropping tables in reverse dependency order. `alembic/env.py` sets `target_metadata = metadata` imported from `super_scientist.providers.storage.schema`.

After creating the `alembic/` directory and its migration files, add the following exact setting under `[tool.hatch.build.targets.wheel]` in `pyproject.toml` so packaged wheels include the migrations:

```toml
force-include = { "alembic" = "super_scientist/_migrations" }
```

- [ ] **Step 5: Implement engine setup and `BEGIN IMMEDIATE` unit of work**

Create `src/super_scientist/providers/storage/database.py`:

```python
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine

from super_scientist.providers.storage.repositories import RepositorySet


def create_database_engine(url: str) -> Engine:
    return create_engine(url, future=True)


def upgrade_database(url: str) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    packaged_migrations = Path(__file__).resolve().parents[2] / "_migrations"
    script_location = (
        packaged_migrations if packaged_migrations.exists() else repository_root / "alembic"
    )
    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


class DatabaseUnitOfWork:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.connection: Connection | None = None

    def __enter__(self) -> DatabaseUnitOfWork:
        self.connection = self._engine.connect()
        self.connection.exec_driver_sql("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.connection is None:
            return
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()

    def repositories(self) -> RepositorySet:
        if self.connection is None:
            raise RuntimeError("unit of work is not active")
        return RepositorySet(self.connection)
```

- [ ] **Step 6: Implement typed repositories**

Create `src/super_scientist/providers/storage/repositories.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import Connection, insert, select

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.claims.models import AtomicClaim
from super_scientist.domain.evidence.models import EvidenceRecord
from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes, sha256_hex
from super_scientist.kernel.audit.models import AuditEvent
from super_scientist.kernel.transactions.models import Proposal, TransactionDecision
from super_scientist.providers.storage.schema import (
    audit_events,
    claim_heads,
    claim_versions,
    evidence_records,
    governance_policies,
    governance_state,
    transactions,
)

PROPOSAL_ADAPTER = TypeAdapter(Proposal)


class StoredTransaction(BaseModel):
    model_config = ConfigDict(frozen=True)

    proposal: Proposal
    proposal_hash: str
    decision: TransactionDecision


class EvidenceRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        row = self._connection.execute(
            select(evidence_records.c.record_json).where(
                evidence_records.c.evidence_id == evidence_id
            )
        ).scalar_one_or_none()
        return None if row is None else EvidenceRecord.model_validate_json(row)

    def list_all(self) -> tuple[EvidenceRecord, ...]:
        rows = self._connection.execute(
            select(evidence_records.c.record_json).order_by(evidence_records.c.evidence_id)
        ).scalars()
        return tuple(EvidenceRecord.model_validate_json(row) for row in rows)

    def add(self, record: EvidenceRecord) -> None:
        self._connection.execute(
            insert(evidence_records).values(
                evidence_id=record.evidence_id,
                content_hash=record.content_hash,
                record_json=record.model_dump_json(),
                created_at=record.retrieved_at.isoformat(),
            )
        )


class ClaimRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_head(self, claim_id: str) -> AtomicClaim | None:
        row = self._connection.execute(
            select(claim_versions.c.record_json)
            .join(
                claim_heads,
                claim_heads.c.claim_version_id == claim_versions.c.claim_version_id,
            )
            .where(claim_heads.c.claim_id == claim_id)
        ).scalar_one_or_none()
        return None if row is None else AtomicClaim.model_validate_json(row)

    def get_head_required(self, claim_id: str) -> AtomicClaim:
        claim = self.get_head(claim_id)
        if claim is None:
            raise KeyError(f"claim does not exist: {claim_id}")
        return claim

    def list_heads(self) -> tuple[AtomicClaim, ...]:
        rows = self._connection.execute(
            select(claim_versions.c.record_json)
            .join(
                claim_heads,
                claim_heads.c.claim_version_id == claim_versions.c.claim_version_id,
            )
            .order_by(claim_heads.c.claim_id)
        ).scalars()
        return tuple(AtomicClaim.model_validate_json(row) for row in rows)

    def history(self, claim_id: str) -> tuple[AtomicClaim, ...]:
        rows = self._connection.execute(
            select(claim_versions.c.record_json)
            .where(claim_versions.c.claim_id == claim_id)
            .order_by(claim_versions.c.version)
        ).scalars()
        return tuple(AtomicClaim.model_validate_json(row) for row in rows)

    def add_version(self, claim: AtomicClaim) -> None:
        version_id = f"{claim.claim_id}:{claim.version}"
        record_json = claim.model_dump_json()
        self._connection.execute(
            insert(claim_versions).values(
                claim_version_id=version_id,
                claim_id=claim.claim_id,
                version=claim.version,
                status=claim.status.value,
                record_json=record_json,
                content_hash=sha256_hex(record_json.encode("utf-8")),
                created_at=claim.created_at.isoformat(),
            )
        )
        self._connection.execute(
            insert(claim_heads)
            .values(
                claim_id=claim.claim_id,
                claim_version_id=version_id,
                version=claim.version,
                status=claim.status.value,
            )
            .prefix_with("OR REPLACE")
        )


class TransactionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def get_by_idempotency_key(self, key: str) -> StoredTransaction | None:
        row = self._connection.execute(
            select(
                transactions.c.proposal_json,
                transactions.c.proposal_hash,
                transactions.c.decision_json,
            ).where(transactions.c.idempotency_key == key)
        ).one_or_none()
        if row is None:
            return None
        return StoredTransaction(
            proposal=PROPOSAL_ADAPTER.validate_json(row.proposal_json),
            proposal_hash=row.proposal_hash,
            decision=TransactionDecision.model_validate_json(row.decision_json),
        )

    def list_all(self) -> tuple[StoredTransaction, ...]:
        rows = self._connection.execute(
            select(
                transactions.c.proposal_json,
                transactions.c.proposal_hash,
                transactions.c.decision_json,
            ).order_by(transactions.c.created_at, transactions.c.proposal_id)
        )
        return tuple(
            StoredTransaction(
                proposal=PROPOSAL_ADAPTER.validate_json(row.proposal_json),
                proposal_hash=row.proposal_hash,
                decision=TransactionDecision.model_validate_json(row.decision_json),
            )
            for row in rows
        )

    def add(
        self,
        proposal: Proposal,
        decision: TransactionDecision,
        occurred_at: UtcTimestamp,
    ) -> None:
        proposal_json = canonical_json_bytes(proposal.model_dump(mode="json")).decode("utf-8")
        self._connection.execute(
            insert(transactions).values(
                proposal_id=proposal.proposal_id,
                idempotency_key=proposal.idempotency_key,
                proposal_hash=sha256_hex(proposal_json.encode("utf-8")),
                proposal_json=proposal_json,
                decision_json=decision.model_dump_json(),
                created_at=occurred_at.isoformat(),
            )
        )


class AuditRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def last(self) -> AuditEvent | None:
        row = self._connection.execute(
            select(audit_events.c.event_json).order_by(audit_events.c.sequence.desc()).limit(1)
        ).scalar_one_or_none()
        return None if row is None else AuditEvent.model_validate_json(row)

    def list_all(self) -> tuple[AuditEvent, ...]:
        rows = self._connection.execute(
            select(audit_events.c.event_json).order_by(audit_events.c.sequence)
        ).scalars()
        return tuple(AuditEvent.model_validate_json(row) for row in rows)

    def add(self, event: AuditEvent) -> None:
        self._connection.execute(
            insert(audit_events).values(
                sequence=event.sequence,
                event_id=event.event_id,
                previous_hash=event.previous_hash,
                payload_hash=event.payload_hash,
                event_hash=event.event_hash,
                event_json=event.model_dump_json(),
            )
        )


class PolicyRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def add_and_activate(self, snapshot: PolicySnapshot, created_at: UtcTimestamp) -> None:
        self._connection.execute(
            insert(governance_policies)
            .values(
                policy_hash=snapshot.policy_hash,
                policy_json=snapshot.policy.model_dump_json(),
                created_at=created_at.isoformat(),
            )
            .prefix_with("OR IGNORE")
        )
        self._connection.execute(
            insert(governance_state)
            .values(singleton_id=1, active_policy_hash=snapshot.policy_hash)
            .prefix_with("OR REPLACE")
        )

    def get_active(self) -> PolicySnapshot | None:
        row = self._connection.execute(
            select(
                governance_policies.c.policy_hash,
                governance_policies.c.policy_json,
            )
            .join(
                governance_state,
                governance_state.c.active_policy_hash == governance_policies.c.policy_hash,
            )
            .where(governance_state.c.singleton_id == 1)
        ).one_or_none()
        if row is None:
            return None
        return PolicySnapshot(
            policy_hash=row.policy_hash,
            policy=GovernancePolicy.model_validate_json(row.policy_json),
        )


class RepositorySet:
    def __init__(self, connection: Connection) -> None:
        self.evidence = EvidenceRepository(connection)
        self.claims = ClaimRepository(connection)
        self.transactions = TransactionRepository(connection)
        self.audit = AuditRepository(connection)
        self.policies = PolicyRepository(connection)
```

Add these named repository tests in `tests/integration/storage/test_repositories.py`, using a migrated temporary SQLite database and the concrete model builders already introduced in Tasks 4-7:

```python
def test_evidence_repository_add_get_and_list_round_trip(repository_fixture) -> None:
    record = repository_fixture.evidence_record("ev-1")
    repository_fixture.repositories.evidence.add(record)
    assert repository_fixture.repositories.evidence.get("ev-1") == record
    assert repository_fixture.repositories.evidence.list_all() == (record,)


def test_claim_repository_preserves_versions_and_moves_head(repository_fixture) -> None:
    first = repository_fixture.claim("claim-1", version=1, status="PROPOSED")
    second = first.model_copy(
        update={
            "version": 2,
            "status": "EVIDENCE_LINKED",
            "parent_version_id": "claim-1:1",
        }
    )
    repository_fixture.repositories.claims.add_version(first)
    repository_fixture.repositories.claims.add_version(second)
    assert repository_fixture.repositories.claims.get_head("claim-1") == second
    assert repository_fixture.repositories.claims.get_head_required("claim-1") == second
    assert repository_fixture.repositories.claims.list_heads() == (second,)
    assert repository_fixture.repositories.claims.history("claim-1") == (first, second)


def test_transaction_repository_round_trips_by_idempotency_key(repository_fixture) -> None:
    proposal = repository_fixture.add_evidence_proposal("proposal-1", "key-1")
    decision = TransactionDecision(proposal_id="proposal-1", accepted=True)
    repository_fixture.repositories.transactions.add(proposal, decision, repository_fixture.now)
    stored = repository_fixture.repositories.transactions.get_by_idempotency_key("key-1")
    assert stored is not None
    assert stored.proposal == proposal
    assert stored.decision == decision
    assert repository_fixture.repositories.transactions.list_all() == (stored,)


def test_policy_and_audit_repositories_round_trip(repository_fixture) -> None:
    snapshot = repository_fixture.policy_snapshot()
    repository_fixture.repositories.policies.add_and_activate(snapshot, repository_fixture.now)
    event = append_event(None, "audit-1", "test", {"accepted": True}, repository_fixture.now)
    repository_fixture.repositories.audit.add(event)
    assert repository_fixture.repositories.policies.get_active() == snapshot
    assert repository_fixture.repositories.audit.last() == event
    assert repository_fixture.repositories.audit.list_all() == (event,)
```

Define `repository_fixture` in the same test module as a small dataclass fixture. Its builders must return the exact Pydantic contracts from Tasks 4-7, and its `repositories` property must be created from one active `DatabaseUnitOfWork`; do not mock SQLAlchemy in these integration tests. After the claim test, query `claim_versions` directly and assert both `claim-1:1` and `claim-1:2` remain while `claim_heads` references only `claim-1:2`.

- [ ] **Step 7: Run migration, repository, and append-only tests**

Run: `python -m pytest tests/integration/storage tests/property/test_database_append_only.py -v`

Expected: migrations create all tables, repositories round-trip typed records, and direct update/delete attempts fail with `append-only table`.

- [ ] **Step 8: Commit persistence**

```bash
git add src/super_scientist/providers/storage alembic.ini alembic tests/integration/storage tests/property/test_database_append_only.py
git commit -m "feat: persist kernel state with append-only SQLite history"
```

---

### Task 9: Transactional Kernel Application Service

**Files:**
- Create: `src/super_scientist/application/kernel_service.py`
- Create: `tests/integration/application/test_kernel_service.py`
- Create: `tests/property/test_transaction_replay.py`

**Interfaces:**
- Consumes: artifact store, repositories, admission engine, audit chain, clock
- Produces: `Clock`, `SystemClock`, and `KernelService.submit(proposal) -> TransactionDecision`

- [ ] **Step 1: Write failing acceptance, rejection, and replay tests**

```python
def test_accepted_evidence_is_committed_with_audit(kernel: KernelFixture) -> None:
    proposal = kernel.valid_add_evidence("p-1", "k-1", b"observation")
    decision = kernel.service.submit(proposal)
    assert decision.accepted
    assert kernel.evidence.get(proposal.evidence.evidence_id) == proposal.evidence
    assert kernel.audit.list_all()[-1].payload["decision"]["accepted"] is True


def test_rejected_claim_is_audited_but_not_projected(kernel: KernelFixture) -> None:
    proposal = kernel.self_approved_claim("p-2", "k-2")
    decision = kernel.service.submit(proposal)
    assert not decision.accepted
    assert kernel.claims.get_head(proposal.claim.claim_id) is None
    assert kernel.audit.list_all()[-1].payload["decision"]["accepted"] is False


def test_duplicate_submission_returns_original_decision(kernel: KernelFixture) -> None:
    proposal = kernel.valid_add_evidence("p-3", "k-3", b"same")
    first = kernel.service.submit(proposal)
    second = kernel.service.submit(proposal)
    assert second.replayed
    assert second.model_copy(update={"replayed": False}) == first
    assert len(kernel.audit.list_all()) == 1


def test_reused_idempotency_key_with_new_content_is_rejected_and_audited(
    kernel: KernelFixture,
) -> None:
    first = kernel.valid_add_evidence("p-4", "shared-key", b"first")
    conflicting = kernel.valid_add_evidence("p-5", "shared-key", b"different")
    assert kernel.service.submit(first).accepted
    decision = kernel.service.submit(conflicting)
    assert decision.reasons[0].code is RejectionCode.IDEMPOTENCY_CONFLICT
    assert len(kernel.audit.list_all()) == 2
```

- [ ] **Step 2: Run tests and verify missing service**

Run: `python -m pytest tests/integration/application/test_kernel_service.py -v`

Expected: collection fails because `KernelService` does not exist.

- [ ] **Step 3: Implement clock and service transaction flow**

Create `src/super_scientist/application/kernel_service.py` using this exact transaction flow:

```python
from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import Callable
from typing import Protocol

from super_scientist.config.models import PolicySnapshot
from super_scientist.domain.primitives import UtcTimestamp, canonical_json_bytes, sha256_hex
from super_scientist.kernel.admission.engine import AdmissionContext, AdmissionEngine
from super_scientist.kernel.audit.chain import append_event
from super_scientist.kernel.transactions.models import (
    AddEvidence,
    Proposal,
    ProposeClaim,
    RejectionCode,
    TransactionDecision,
    TransitionClaim,
)
from super_scientist.providers.storage.database import DatabaseUnitOfWork
from super_scientist.providers.storage.repositories import RepositorySet


class Clock(Protocol):
    def now(self) -> UtcTimestamp:
        raise NotImplementedError


class SystemClock:
    def now(self) -> UtcTimestamp:
        return datetime.now(UTC)


class KernelService:
    def __init__(
        self,
        uow_factory: Callable[[], DatabaseUnitOfWork],
        active_policy: PolicySnapshot,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._active_policy = active_policy
        self._clock = clock
        self._engine = AdmissionEngine()

    def submit(self, proposal: Proposal) -> TransactionDecision:
        proposal_hash = sha256_hex(canonical_json_bytes(proposal.model_dump(mode="json")))
        with self._uow_factory() as uow:
            repositories = uow.repositories()
            prior = repositories.transactions.get_by_idempotency_key(proposal.idempotency_key)
            if prior is not None:
                if prior.proposal_hash != proposal_hash:
                    decision = AdmissionEngine.rejected(
                        proposal.proposal_id,
                        RejectionCode.IDEMPOTENCY_CONFLICT,
                        "idempotency key was reused with different proposal content",
                    )
                    self._audit(proposal, decision, repositories, conflict=True)
                    return decision
                return prior.decision.model_copy(update={"replayed": True})
            context = AdmissionContext(
                active_policy=self._active_policy,
                evidence_by_id={item.evidence_id: item for item in repositories.evidence.list_all()},
                claim_by_id={item.claim_id: item for item in repositories.claims.list_heads()},
                prior_decision_by_idempotency_key={},
            )
            decision = self._engine.decide(proposal, context)
            if decision.accepted:
                self._project(proposal, repositories)
            repositories.transactions.add(proposal, decision, self._clock.now())
            self._audit(proposal, decision, repositories)
            return decision

    def _audit(
        self,
        proposal: Proposal,
        decision: TransactionDecision,
        repositories: RepositorySet,
        conflict: bool = False,
    ) -> None:
        previous = repositories.audit.last()
        suffix = f"-{0 if previous is None else previous.sequence + 1}" if conflict else ""
        event = append_event(
            previous,
            f"audit-{proposal.proposal_id}{suffix}",
            "transaction_decision",
            {
                "proposal": proposal.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
                "policy_hash": self._active_policy.policy_hash,
            },
            self._clock.now(),
        )
        repositories.audit.add(event)

    def _project(self, proposal: Proposal, repositories: RepositorySet) -> None:
        if isinstance(proposal, AddEvidence):
            repositories.evidence.add(proposal.evidence)
        elif isinstance(proposal, ProposeClaim):
            repositories.claims.add_version(proposal.claim)
        elif isinstance(proposal, TransitionClaim):
            current = repositories.claims.get_head_required(proposal.claim_id)
            repositories.claims.add_version(
                current.model_copy(
                    update={
                        "version": current.version + 1,
                        "status": proposal.target_status,
                        "parent_version_id": f"{current.claim_id}:{current.version}",
                        "created_at": self._clock.now(),
                        "created_by": proposal.proposer.actor_id,
                    }
                )
            )
```

- [ ] **Step 4: Add failure atomicity test**

Patch the audit repository fixture so `add` raises `RuntimeError("disk failure")`. Submit valid evidence and assert that the SQLite transaction rolls back evidence, transaction, and audit rows while the already prepared content-addressed blob remains readable and unreferenced.

```python
with pytest.raises(RuntimeError, match="disk failure"):
    failing_service.submit(proposal)
assert repositories.evidence.get(proposal.evidence.evidence_id) is None
assert repositories.transactions.get_by_idempotency_key(proposal.idempotency_key) is None
assert artifact_store.read(proposal.evidence.artifact) == b"observation"
```

- [ ] **Step 5: Run service and replay property tests**

Run: `python -m pytest tests/integration/application tests/property/test_transaction_replay.py -v`

Expected: accepted and rejected decisions are audited once, accepted projections commit atomically, and retries are stable.

- [ ] **Step 6: Commit the application service**

```bash
git add src/super_scientist/application tests/integration/application tests/property/test_transaction_replay.py
git commit -m "feat: commit admitted proposals transactionally"
```

---

### Task 10: Minimal CLI and Stable JSON Envelope

**Files:**
- Create: `src/super_scientist/cli/main.py`
- Create: `src/super_scientist/cli/output.py`
- Create: `src/super_scientist/cli/kernel.py`
- Create: `tests/integration/cli/test_kernel_cli.py`

**Interfaces:**
- Consumes: `KernelService`, repositories, artifact store, settings
- Produces: `scientist-harness init`, `evidence add/show`, `claim propose/history`, `transaction list`, and `audit verify`

- [ ] **Step 1: Write failing CLI JSON and exit-code tests**

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from super_scientist.cli.main import app

runner = CliRunner()


def initialize_fixture(root: Path) -> None:
    result = runner.invoke(app, ["init", "--root", str(root), "--json"])
    assert result.exit_code == 0, result.output


def test_init_emits_versioned_json(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "init"
    assert payload["success"] is True


def test_rejected_claim_returns_nonzero(tmp_path: Path) -> None:
    initialize_fixture(tmp_path)
    result = runner.invoke(
        app,
        [
            "claim",
            "propose",
            "--root",
            str(tmp_path),
            "--proposition",
            "unsupported",
            "--scope",
            "toy",
            "--system",
            "fixture",
            "--modality",
            "observed",
            "--self-approve",
            "--json",
        ],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["decision"]["reasons"][0]["code"] == "SELF_APPROVAL"
```

- [ ] **Step 2: Run CLI tests and verify missing commands**

Run: `python -m pytest tests/integration/cli/test_kernel_cli.py -v`

Expected: collection fails because CLI modules do not exist.

- [ ] **Step 3: Implement the result envelope and root app**

Create `src/super_scientist/cli/output.py`:

```python
import json
from typing import Any

import typer

def json_envelope(
    command: str,
    success: bool,
    data: Any = None,
    decision: Any = None,
    errors: list[dict[str, str]] | None = None,
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "command": command,
            "success": success,
            "decision": decision,
            "data": data,
            "errors": errors or [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def emit(command: str, success: bool, json_output: bool, **payload: Any) -> None:
    envelope = json_envelope(command, success, **payload)
    if json_output:
        typer.echo(envelope)
        return
    parsed = json.loads(envelope)
    typer.echo(f"{command}: {'ok' if success else 'rejected'}")
    typer.echo(json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True))
```

Create `src/super_scientist/cli/main.py`:

```python
import typer

from super_scientist.cli.kernel import audit_app, claim_app, evidence_app, init_command, transaction_app

app = typer.Typer(no_args_is_help=True)
app.command("init")(init_command)
app.add_typer(evidence_app, name="evidence")
app.add_typer(claim_app, name="claim")
app.add_typer(transaction_app, name="transaction")
app.add_typer(audit_app, name="audit")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Implement kernel commands through application services**

Create `src/super_scientist/cli/kernel.py`. Commands use repositories only through a unit of work, generate identifiers at the application boundary, and send every mutation through `KernelService`:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import typer
from sqlalchemy import Engine

from super_scientist.application.kernel_service import KernelService, SystemClock
from super_scientist.cli.output import emit
from super_scientist.config.loader import load_policy
from super_scientist.config.models import GovernancePolicy
from super_scientist.domain.claims.models import AtomicClaim, ClaimStatus
from super_scientist.domain.evidence.models import EvidenceRecord, EvidenceSpan
from super_scientist.domain.identity import ActorIdentity, ActorKind
from super_scientist.kernel.audit.chain import verify_chain
from super_scientist.kernel.transactions.models import AddEvidence, Approval, ProposeClaim
from super_scientist.providers.storage.artifacts import FileArtifactStore
from super_scientist.providers.storage.database import (
    DatabaseUnitOfWork,
    create_database_engine,
    upgrade_database,
)

evidence_app = typer.Typer(no_args_is_help=True)
claim_app = typer.Typer(no_args_is_help=True)
transaction_app = typer.Typer(no_args_is_help=True)
audit_app = typer.Typer(no_args_is_help=True)


@dataclass(frozen=True)
class Runtime:
    engine: Engine
    artifacts: FileArtifactStore
    service: KernelService
    clock: SystemClock


def _database_url(root: Path) -> str:
    return f"sqlite:///{(root / 'scientist-harness.db').resolve().as_posix()}"


def _actor(clock: SystemClock) -> ActorIdentity:
    return ActorIdentity(actor_id="local-cli", kind=ActorKind.HUMAN, created_at=clock.now())


def build_runtime(root: Path) -> Runtime:
    resolved = root.resolve()
    engine = create_database_engine(_database_url(resolved))
    clock = SystemClock()
    with DatabaseUnitOfWork(engine) as uow:
        policy = uow.repositories().policies.get_active()
    if policy is None:
        raise typer.BadParameter("workspace is not initialized; run init first")
    return Runtime(
        engine=engine,
        artifacts=FileArtifactStore(resolved / "artifacts"),
        service=KernelService(lambda: DatabaseUnitOfWork(engine), policy, clock),
        clock=clock,
    )


def init_command(
    root: Path = typer.Option(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    resolved = root.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    policy_path = resolved / "governance-policy.json"
    if not policy_path.exists():
        policy = GovernancePolicy(required_claim_checks=["source_exists", "evidence_span_exists"])
        policy_path.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
    snapshot = load_policy(policy_path)
    url = _database_url(resolved)
    upgrade_database(url)
    engine = create_database_engine(url)
    clock = SystemClock()
    with DatabaseUnitOfWork(engine) as uow:
        policies = uow.repositories().policies
        active = policies.get_active()
        if active is not None and active.policy_hash != snapshot.policy_hash:
            raise typer.BadParameter(
                "changing an initialized governance policy requires the approval workflow"
            )
        policies.add_and_activate(snapshot, clock.now())
    emit(
        "init",
        True,
        json_output,
        data={
            "database": str(resolved / "scientist-harness.db"),
            "artifact_root": str(resolved / "artifacts"),
            "active_policy_hash": snapshot.policy_hash,
        },
    )


@evidence_app.command("add")
def evidence_add(
    root: Path = typer.Option(...),
    source: str = typer.Option(...),
    file: Path = typer.Option(..., exists=True, dir_okay=False, readable=True),
    media_type: str = typer.Option("text/plain"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    runtime = build_runtime(root)
    data = file.read_bytes()
    artifact = runtime.artifacts.put(data, media_type)
    actor = _actor(runtime.clock)
    evidence_id = f"ev-{uuid.uuid4()}"
    text = data.decode("utf-8") if media_type.startswith("text/") and data else None
    record = EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type="document",
        source_locator=source,
        retrieved_at=runtime.clock.now(),
        artifact=artifact,
        extracted_span=None if text is None else EvidenceSpan(start=0, end=len(text), text=text),
        provenance={"collector": "local-cli", "input_file": str(file.resolve())},
        ingestion_actor_id=actor.actor_id,
    )
    proposal = AddEvidence(
        proposal_id=f"proposal-{uuid.uuid4()}",
        idempotency_key=f"evidence:{artifact.sha256}:{source}",
        proposer=actor,
        evidence=record,
    )
    decision = runtime.service.submit(proposal)
    emit("evidence add", decision.accepted, json_output, decision=decision.model_dump(mode="json"))
    if not decision.accepted:
        raise typer.Exit(code=2)


@evidence_app.command("show")
def evidence_show(
    root: Path = typer.Option(...),
    evidence_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    runtime = build_runtime(root)
    with DatabaseUnitOfWork(runtime.engine) as uow:
        record = uow.repositories().evidence.get(evidence_id)
    if record is None:
        emit("evidence show", False, json_output, errors=[{"code": "NOT_FOUND", "message": evidence_id}])
        raise typer.Exit(code=4)
    emit("evidence show", True, json_output, data=record.model_dump(mode="json"))


@claim_app.command("propose")
def claim_propose(
    root: Path = typer.Option(...),
    proposition: str = typer.Option(...),
    scope: str = typer.Option(...),
    system: str = typer.Option(...),
    modality: str = typer.Option(...),
    self_approve: bool = typer.Option(False),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    runtime = build_runtime(root)
    actor = _actor(runtime.clock)
    claim = AtomicClaim(
        claim_id=f"claim-{uuid.uuid4()}",
        version=1,
        proposition=proposition,
        scope=scope,
        population_or_system=system,
        epistemic_modality=modality,
        status=ClaimStatus.PROPOSED,
        created_at=runtime.clock.now(),
        created_by=actor.actor_id,
    )
    approval = Approval(approver=actor, approved_at=runtime.clock.now()) if self_approve else None
    proposal = ProposeClaim(
        proposal_id=f"proposal-{uuid.uuid4()}",
        idempotency_key=f"claim:{claim.claim_id}:1",
        proposer=actor,
        approval=approval,
        claim=claim,
    )
    decision = runtime.service.submit(proposal)
    emit(
        "claim propose",
        decision.accepted,
        json_output,
        data={"claim_id": claim.claim_id},
        decision=decision.model_dump(mode="json"),
    )
    if not decision.accepted:
        raise typer.Exit(code=2)


@claim_app.command("history")
def claim_history(
    root: Path = typer.Option(...),
    claim_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    runtime = build_runtime(root)
    with DatabaseUnitOfWork(runtime.engine) as uow:
        history = uow.repositories().claims.history(claim_id)
    emit("claim history", True, json_output, data=[item.model_dump(mode="json") for item in history])


@transaction_app.command("list")
def transaction_list(
    root: Path = typer.Option(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    runtime = build_runtime(root)
    with DatabaseUnitOfWork(runtime.engine) as uow:
        stored = uow.repositories().transactions.list_all()
    data = [
        {
            "proposal": item.proposal.model_dump(mode="json"),
            "proposal_hash": item.proposal_hash,
            "decision": item.decision.model_dump(mode="json"),
        }
        for item in stored
    ]
    emit("transaction list", True, json_output, data=data)


@audit_app.command("verify")
def audit_verify(
    root: Path = typer.Option(...),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    runtime = build_runtime(root)
    with DatabaseUnitOfWork(runtime.engine) as uow:
        events = uow.repositories().audit.list_all()
    result = verify_chain(events)
    emit("audit verify", result.valid, json_output, data=result.model_dump(mode="json"))
    if not result.valid:
        raise typer.Exit(code=3)
```

- [ ] **Step 5: Run CLI tests and manual smoke commands**

Run: `python -m pytest tests/integration/cli/test_kernel_cli.py -v`

Expected: all tests pass.

Run: `scientist-harness init --root .kernel-smoke --json`

Expected: JSON with `success: true`, a created SQLite path, artifact root, and active policy hash.

Run: `scientist-harness audit verify --root .kernel-smoke --json`

Expected: JSON reports a valid zero- or one-event chain and exits zero.

Remove `.kernel-smoke` using a platform-native command after verifying its resolved path is inside the repository worktree.

- [ ] **Step 6: Commit the CLI**

```bash
git add src/super_scientist/cli tests/integration/cli pyproject.toml
git commit -m "feat: expose kernel operations through stable CLI"
```

---

### Task 11: Deterministic End-to-End Kernel Workflow

**Files:**
- Create: `tests/e2e/test_kernel_vertical_slice.py`
- Create: `examples/kernel_vertical_slice.py`
- Create: `docs/examples/kernel-vertical-slice.md`

**Interfaces:**
- Consumes: installed CLI and public application contracts
- Produces: offline demonstration of accepted evidence, rejected self-approval, accepted claim proposal, claim history, and verified audit

- [ ] **Step 1: Write the failing end-to-end test**

```python
import json
import subprocess
import sys
from pathlib import Path


def run_cli(root: Path, *args: str, expected_code: int = 0) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "super_scientist.cli.main", *args, "--root", str(root), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == expected_code, completed.stderr
    return json.loads(completed.stdout)


def test_offline_kernel_workflow(tmp_path: Path) -> None:
    assert run_cli(tmp_path, "init")["success"] is True
    evidence_file = tmp_path / "observation.txt"
    evidence_file.write_text("At x=1, y=2.", encoding="utf-8")
    added = run_cli(tmp_path, "evidence", "add", "--source", "fixture://x1", "--file", str(evidence_file))
    assert added["decision"]["accepted"] is True
    rejected = run_cli(
        tmp_path,
        "claim",
        "propose",
        "--proposition",
        "y equals 2x for the toy fixture",
        "--scope",
        "x in the observed fixture range",
        "--system",
        "deterministic toy generator",
        "--modality",
        "observed",
        "--self-approve",
        expected_code=2,
    )
    assert rejected["decision"]["reasons"][0]["code"] == "SELF_APPROVAL"
    accepted = run_cli(
        tmp_path,
        "claim",
        "propose",
        "--proposition",
        "y equals 2x for the toy fixture",
        "--scope",
        "x in the observed fixture range",
        "--system",
        "deterministic toy generator",
        "--modality",
        "observed",
    )
    assert accepted["decision"]["accepted"] is True
    claim_id = str(accepted["data"]["claim_id"])
    history = run_cli(tmp_path, "claim", "history", claim_id)
    assert len(history["data"]) == 1
    assert history["data"][0]["status"] == "PROPOSED"
    verified = run_cli(tmp_path, "audit", "verify")
    assert verified["data"]["valid"] is True
    assert verified["data"]["checked_events"] == 3
```

- [ ] **Step 2: Run the test and observe the first integration mismatch**

Run: `python -m pytest tests/e2e/test_kernel_vertical_slice.py -v`

Expected: the test fails on the first missing or mismatched CLI contract, proving the test exercises the installed public boundary.

- [ ] **Step 3: Implement the executable example with the same public contracts**

Create `examples/kernel_vertical_slice.py` as a `main()` program that creates a temporary workspace, calls the public CLI through `subprocess.run`, prints each parsed JSON envelope, verifies the expected acceptance and rejection, verifies the audit chain, and exits nonzero on any mismatch. It must not import repository internals or use network access.

Create `docs/examples/kernel-vertical-slice.md` with the exact command:

```bash
python examples/kernel_vertical_slice.py
```

Document expected accepted evidence, `SELF_APPROVAL` rejection, accepted claim history, and valid three-event audit output. State that this kernel example is not the complete scientific-run demonstration planned for a later subsystem.

- [ ] **Step 4: Pass end-to-end and offline checks**

Run: `python -m pytest tests/e2e/test_kernel_vertical_slice.py -v`

Expected: one end-to-end test passes without network access.

Run: `python examples/kernel_vertical_slice.py`

Expected: process exits zero and prints only successful assertions and versioned JSON envelopes.

- [ ] **Step 5: Commit the vertical-slice demonstration**

```bash
git add tests/e2e examples docs/examples/kernel-vertical-slice.md
git commit -m "test: demonstrate offline epistemic kernel workflow"
```

---

### Task 12: Kernel Quality Gate, Documentation, and Verification

**Files:**
- Create: `src/super_scientist/quality/runner.py`
- Create: `tests/unit/quality/test_runner.py`
- Create: `README.md`
- Create: `ARCHITECTURE.md`
- Create: `GOVERNANCE.md`
- Create: `CLAIM_LEDGER.md`
- Create: `SECURITY.md`
- Create: `.github/workflows/quality.yml`
- Modify: `src/super_scientist/cli/main.py`
- Test: entire repository

**Interfaces:**
- Consumes: all kernel vertical-slice commands and tests
- Produces: fixed `scientist-harness quality-gate` command and accurate kernel documentation

- [ ] **Step 1: Write a failing fixed-registry quality test**

```python
from super_scientist.quality.runner import CHECKS


def test_quality_registry_is_fixed_and_complete_for_kernel_slice() -> None:
    assert tuple(check.name for check in CHECKS) == (
        "format",
        "lint",
        "types",
        "tests",
        "security",
        "dependencies",
        "build",
        "package",
    )
    assert all(isinstance(check.argv, tuple) for check in CHECKS)
```

- [ ] **Step 2: Run the test and verify missing quality module**

Run: `python -m pytest tests/unit/quality/test_runner.py -v`

Expected: collection fails because the quality runner does not exist.

- [ ] **Step 3: Implement a fixed, non-user-extensible runner**

Create `src/super_scientist/quality/runner.py`:

```python
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from glob import glob


@dataclass(frozen=True)
class QualityCheck:
    name: str
    argv: tuple[str, ...]


PYTHON = sys.executable
CHECKS = (
    QualityCheck("format", (PYTHON, "-m", "ruff", "format", "--check", ".")),
    QualityCheck("lint", (PYTHON, "-m", "ruff", "check", ".")),
    QualityCheck("types", (PYTHON, "-m", "mypy", "src")),
    QualityCheck(
        "tests",
        (PYTHON, "-m", "pytest", "--cov=super_scientist", "--cov-branch", "--cov-fail-under=90"),
    ),
    QualityCheck("security", (PYTHON, "-m", "bandit", "-q", "-r", "src")),
    QualityCheck("dependencies", (PYTHON, "-m", "pip_audit")),
    QualityCheck("build", (PYTHON, "-m", "build")),
    QualityCheck("package", (PYTHON, "-m", "twine", "check", "dist/*")),
)


def run_quality_gate() -> int:
    for check in CHECKS:
        argv = check.argv
        if check.name == "package":
            distributions = tuple(sorted(glob("dist/*")))
            if not distributions:
                return 1
            argv = (*check.argv[:-1], *distributions)
        completed = subprocess.run(argv, check=False)
        if completed.returncode != 0:
            return completed.returncode
    return 0
```

The only accepted input to this runner is the decision to run the predefined registry. Do not add an arbitrary command, path, skip, or threshold option.

- [ ] **Step 4: Expose `quality-gate` through the CLI**

Register a `quality-gate` command in `cli/main.py` that calls `run_quality_gate()` and exits with its exact nonzero return code. JSON mode records each fixed check and result but does not permit check selection.

- [ ] **Step 5: Write accurate kernel-slice documentation**

Create the five listed documents with these required statements:

- `README.md`: current maturity is an epistemic-kernel vertical slice; install, minimal CLI example, security boundaries, quality command, and roadmap to later subsystems.
- `ARCHITECTURE.md`: inward dependency rule, SQLite and artifact boundaries, proposal/admission flow, audit chain, and citations `[S07]` and `[S12]` where provenance matters.
- `GOVERNANCE.md`: active policy hash, self-approval prohibition, human approval boundary, quality-policy protection, and local RepoQualityGate provenance `[S20]`.
- `CLAIM_LEDGER.md`: claim fields, legal transition graph, evidence-span requirements, deterministic versus review-required outcomes, and SciConBench inspiration `[S02]` without reproduction claims.
- `SECURITY.md`: no shell/network/model authority, untrusted retrieved content, path containment, secret limitations, audit fail-closed behavior, disclosure process, and residual local-filesystem risks.

Do not describe unimplemented hypotheses, experiments, orchestration, memory, QLoRA, or complete scientific runs as available features.

After creating `README.md`, add `readme = "README.md"` under `[project]` in `pyproject.toml` and include `pyproject.toml` in this task's commit.

- [ ] **Step 6: Add CI using the same fixed commands**

Create `.github/workflows/quality.yml` for pushes and pull requests. Use Python 3.12, install `.[dev]`, run the fixed quality command, and upload built distributions and coverage output as artifacts. Grant only `contents: read` permission. Do not place secrets in the workflow.

- [ ] **Step 7: Run full verification**

Run each command separately so failures are attributable:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest --cov=super_scientist --cov-branch --cov-fail-under=90
python -m bandit -q -r src
python -m pip_audit
python -m build
python -m twine check dist/*
scientist-harness quality-gate
```

Expected: every command exits zero. If dependency auditing requires network and the environment is offline, capture the failure exactly; do not report the quality gate as passing and do not weaken the command.

- [ ] **Step 8: Perform clean wheel installation verification**

Create a temporary virtual environment outside the repository, install the built wheel without `dev` extras, run `scientist-harness --help`, initialize a temporary kernel workspace, and verify an empty audit chain. Confirm `pip list` contains no model SDK, PEFT, Transformers, CUDA, or training package introduced by this project.

Expected: wheel installation and smoke commands pass using core dependencies only.

- [ ] **Step 9: Request independent code review**

Invoke `superpowers:requesting-code-review` against the complete branch diff. Resolve correctness, authority, persistence, and missing-test findings through the required review-feedback workflow. Re-run Step 7 after any change.

- [ ] **Step 10: Commit quality and documentation**

```bash
git add src/super_scientist/quality src/super_scientist/cli/main.py tests/unit/quality pyproject.toml README.md ARCHITECTURE.md GOVERNANCE.md CLAIM_LEDGER.md SECURITY.md .github/workflows/quality.yml
git commit -m "chore: enforce kernel quality and document boundaries"
```

---

## Plan Completion Gate

Before declaring this plan implemented:

1. Confirm every task has its own passing tests and commit.
2. Confirm `git diff --check` is clean.
3. Confirm the full quality command actually passed or report its exact blocker.
4. Confirm audit tampering, evidence replacement, illegal transitions, self-approval,
   idempotency conflict, path traversal, migration, rollback-on-failure, and clean-wheel
   installation tests ran.
5. Confirm documentation describes only the implemented kernel slice.
6. Invoke `superpowers:verification-before-completion` and capture command output.
7. Do not merge into `main`; prepare the branch for later draft-PR integration after
   the remaining subsystem plans are implemented.
