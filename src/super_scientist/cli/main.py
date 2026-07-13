from typing import Annotated

import typer

from super_scientist.cli.kernel import (
    audit_app,
    claim_app,
    evidence_app,
    init_command,
    transaction_app,
)
from super_scientist.cli.output import emit
from super_scientist.quality.runner import QualityCheckResult, run_quality_gate

app = typer.Typer(no_args_is_help=True)
app.command("init")(init_command)
app.add_typer(evidence_app, name="evidence")
app.add_typer(claim_app, name="claim")
app.add_typer(transaction_app, name="transaction")
app.add_typer(audit_app, name="audit")


def _quality_result_payload(result: QualityCheckResult) -> dict[str, object]:
    return {
        "name": result.name,
        "argv": result.argv,
        "status": result.status,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@app.command("quality-gate")
def quality_gate_command(
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    results: list[QualityCheckResult] = []

    def report(result: QualityCheckResult) -> None:
        results.append(result)
        if json_output:
            return
        if result.stdout:
            typer.echo(result.stdout, nl=False)
        if result.stderr:
            typer.echo(result.stderr, err=True, nl=False)

    returncode = run_quality_gate(reporter=report)
    failed = next((result for result in results if result.status == "failed"), None)
    errors = (
        []
        if failed is None
        else [
            {
                "code": "QUALITY_CHECK_FAILED",
                "message": f"{failed.name} exited with status {failed.returncode}",
            }
        ]
    )
    emit(
        "quality-gate",
        returncode == 0,
        json_output,
        data={"checks": [_quality_result_payload(result) for result in results]},
        errors=errors,
    )
    if returncode != 0:
        raise typer.Exit(code=returncode)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
