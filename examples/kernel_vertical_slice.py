"""Run the deterministic, offline epistemic-kernel vertical slice."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn, cast


class WorkflowMismatch(Exception):
    """Raised when a public CLI response differs from the expected workflow."""


def fail(message: str) -> NoReturn:
    raise WorkflowMismatch(message)


def run_cli(root: Path, *args: str, expected_code: int = 0) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "super_scientist.cli.main", *args, "--root", str(root), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != expected_code:
        fail(
            f"{' '.join(args)} exited {completed.returncode}, expected {expected_code}: "
            f"{completed.stderr.strip()}"
        )
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        fail(f"{' '.join(args)} did not emit JSON: {error}")
    if not isinstance(parsed, dict):
        fail(f"{' '.join(args)} did not emit a JSON object")
    envelope = cast(dict[str, object], parsed)
    if envelope.get("schema_version") != 1:
        fail(f"{' '.join(args)} did not emit schema-version-1 JSON")
    print(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
    return envelope


def data(envelope: dict[str, object]) -> dict[str, Any]:
    value = envelope.get("data")
    if not isinstance(value, dict):
        fail("expected an object data field")
    return value


def decision(envelope: dict[str, object]) -> dict[str, Any]:
    value = envelope.get("decision")
    if not isinstance(value, dict):
        fail("expected an object decision field")
    return value


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="super-scientist-kernel-") as workspace:
        root = Path(workspace)
        initialized = run_cli(root, "init")
        if initialized.get("success") is not True:
            fail("init was not accepted")

        evidence_file = root / "observation.txt"
        evidence_file.write_text("At x=1, y=2.", encoding="utf-8")
        added = run_cli(
            root,
            "evidence",
            "add",
            "--source",
            "fixture://x1",
            "--file",
            str(evidence_file),
        )
        if decision(added).get("accepted") is not True:
            fail("evidence was not accepted")

        claim_arguments = (
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
        rejected = run_cli(root, *claim_arguments, "--self-approve", expected_code=2)
        reasons = decision(rejected).get("reasons")
        if (
            not isinstance(reasons, list)
            or not reasons
            or not isinstance(reasons[0], dict)
            or reasons[0].get("code") != "SELF_APPROVAL"
        ):
            fail("self-approved claim was not rejected with SELF_APPROVAL")

        accepted = run_cli(root, *claim_arguments)
        if decision(accepted).get("accepted") is not True:
            fail("claim was not accepted")
        claim_id = data(accepted).get("claim_id")
        if not isinstance(claim_id, str):
            fail("accepted claim did not include a claim_id")

        history = run_cli(root, "claim", "history", claim_id)
        history_data = history.get("data")
        if (
            not isinstance(history_data, list)
            or len(history_data) != 1
            or not isinstance(history_data[0], dict)
            or history_data[0].get("status") != "PROPOSED"
        ):
            fail("claim history did not contain one proposed claim")

        verified = run_cli(root, "audit", "verify")
        audit = data(verified)
        if audit.get("valid") is not True or audit.get("checked_events") != 3:
            fail("audit verification did not report a valid three-event chain")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkflowMismatch as error:
        print(f"kernel vertical slice failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
