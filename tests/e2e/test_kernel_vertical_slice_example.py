import subprocess
from pathlib import Path

import pytest

from examples import kernel_vertical_slice as example


def test_example_rejects_non_object_json_envelope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        example.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "[]", ""),
    )

    with pytest.raises(example.WorkflowMismatch, match="JSON object"):
        example.run_cli(tmp_path, "init")


def test_example_rejects_non_object_self_approval_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_cli(root: Path, *args: str, expected_code: int = 0) -> dict[str, object]:
        del root, expected_code
        if args == ("init",):
            return {"success": True}
        if args[:2] == ("evidence", "add"):
            return {"decision": {"accepted": True}}
        if "--self-approve" in args:
            return {"decision": {"reasons": ["SELF_APPROVAL"]}}
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(example, "run_cli", run_cli)

    with pytest.raises(example.WorkflowMismatch, match="SELF_APPROVAL"):
        example.main()
