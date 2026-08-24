from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from super_scientist.domain.harness_eval.guidance import GuidanceCondition
from super_scientist.kernel.transactions.models import (
    AppendGuidanceEvaluationCell,
    RecordGuidanceEvaluationProtocol,
)
from super_scientist.providers.storage.database import upgrade_database
from super_scientist.providers.storage.evaluation_records import (
    GuidanceCellRepository,
    GuidanceEvaluationProtocolRepository,
)
from tests.unit.collaboration.conftest import actor
from tests.unit.harness_eval.test_guidance import _cell, _protocol

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


@pytest.mark.integration
def test_guidance_protocol_repository_round_trips_real_proposal(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'evaluation.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            record = _protocol()
            proposal = RecordGuidanceEvaluationProtocol(
                proposal_id="proposal-guidance",
                idempotency_key="proposal-guidance",
                proposer=actor("coordinator"),
                protocol=record,
            )
            repository = GuidanceEvaluationProtocolRepository(connection)
            repository.add_from_proposal(
                proposal,
                created_at=NOW,
                transaction_id=proposal.proposal_id,
                governing_policy_hash="f" * 64,
            )

            assert repository.get(record.protocol_id) == record
    finally:
        engine.dispose()


@pytest.mark.integration
def test_guidance_cells_are_returned_in_canonical_identity_order(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'guidance-order.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection, connection.begin():
            protocol = _protocol()
            protocol_proposal = RecordGuidanceEvaluationProtocol(
                proposal_id="proposal-guidance",
                idempotency_key="proposal-guidance",
                proposer=actor("coordinator"),
                protocol=protocol,
            )
            GuidanceEvaluationProtocolRepository(connection).add_from_proposal(
                protocol_proposal,
                created_at=NOW,
                transaction_id=protocol_proposal.proposal_id,
                governing_policy_hash="f" * 64,
            )
            repository = GuidanceCellRepository(connection)
            for condition in (
                GuidanceCondition.OBJECTIVE_AND_DATA_ONLY,
                GuidanceCondition.FULL_PROCEDURE_GUIDANCE,
            ):
                cell = _cell(protocol=protocol, condition=condition)
                proposal = AppendGuidanceEvaluationCell(
                    proposal_id=f"proposal-{cell.cell_id}",
                    idempotency_key=f"proposal-{cell.cell_id}",
                    proposer=actor("coordinator"),
                    cell=cell,
                )
                repository.add_from_proposal(
                    proposal,
                    created_at=NOW,
                    transaction_id=proposal.proposal_id,
                    governing_policy_hash="f" * 64,
                )

            assert tuple(
                item.cell_id for item in repository.list_for_protocol(protocol.protocol_id)
            ) == tuple(
                sorted(
                    (
                        f"cell-full_procedure_guidance-{protocol.protocol_id}",
                        f"cell-objective_and_data_only-{protocol.protocol_id}",
                    )
                )
            )
    finally:
        engine.dispose()
