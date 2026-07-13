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
