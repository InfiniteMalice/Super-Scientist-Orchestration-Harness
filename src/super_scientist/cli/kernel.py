from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

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

Root = Annotated[Path, typer.Option()]
JsonOutput = Annotated[bool, typer.Option("--json")]
Source = Annotated[str, typer.Option()]
InputFile = Annotated[Path, typer.Option(exists=True, dir_okay=False, readable=True)]
MediaType = Annotated[str, typer.Option()]
Proposition = Annotated[str, typer.Option()]
Scope = Annotated[str, typer.Option()]
System = Annotated[str, typer.Option()]
Modality = Annotated[str, typer.Option()]
SelfApprove = Annotated[bool, typer.Option()]
EvidenceId = Annotated[str, typer.Argument()]
ClaimId = Annotated[str, typer.Argument()]


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
    root: Root,
    json_output: JsonOutput = False,
) -> None:
    resolved = root.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    policy_path = resolved / "governance-policy.json"
    if not policy_path.exists():
        policy = GovernancePolicy(required_claim_checks=("source_exists", "evidence_span_exists"))
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
    root: Root,
    source: Source,
    file: InputFile,
    media_type: MediaType = "text/plain",
    json_output: JsonOutput = False,
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
    root: Root,
    evidence_id: EvidenceId,
    json_output: JsonOutput = False,
) -> None:
    runtime = build_runtime(root)
    with DatabaseUnitOfWork(runtime.engine) as uow:
        record = uow.repositories().evidence.get(evidence_id)
    if record is None:
        emit(
            "evidence show",
            False,
            json_output,
            errors=[{"code": "NOT_FOUND", "message": evidence_id}],
        )
        raise typer.Exit(code=4)
    emit("evidence show", True, json_output, data=record.model_dump(mode="json"))


@claim_app.command("propose")
def claim_propose(
    root: Root,
    proposition: Proposition,
    scope: Scope,
    system: System,
    modality: Modality,
    self_approve: SelfApprove = False,
    json_output: JsonOutput = False,
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
    root: Root,
    claim_id: ClaimId,
    json_output: JsonOutput = False,
) -> None:
    runtime = build_runtime(root)
    with DatabaseUnitOfWork(runtime.engine) as uow:
        history = uow.repositories().claims.history(claim_id)
    emit(
        "claim history",
        True,
        json_output,
        data=[item.model_dump(mode="json") for item in history],
    )


@transaction_app.command("list")
def transaction_list(
    root: Root,
    json_output: JsonOutput = False,
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
    root: Root,
    json_output: JsonOutput = False,
) -> None:
    runtime = build_runtime(root)
    with DatabaseUnitOfWork(runtime.engine) as uow:
        events = uow.repositories().audit.list_all()
    result = verify_chain(events)
    emit("audit verify", result.valid, json_output, data=result.model_dump(mode="json"))
    if not result.valid:
        raise typer.Exit(code=3)
