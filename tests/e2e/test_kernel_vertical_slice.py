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
    added = run_cli(
        tmp_path, "evidence", "add", "--source", "fixture://x1", "--file", str(evidence_file)
    )
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
