import sys
from collections.abc import Sequence
from typing import Annotated, Any

import typer
from click import ClickException

# Typer's documented custom-cls hook has no top-level TyperGroup export in 0.19.2.
from typer.core import TyperGroup

from super_scientist.cli.adaptation import (
    governance_app,
    hypothesis_app,
    improvement_app,
    model_app,
    primitive_app,
    progress_app,
    research_run_app,
    rule_app,
    trail_app,
    verifier_app,
)
from super_scientist.cli.handbook import handbook_app
from super_scientist.cli.harness_eval import harness_eval_app
from super_scientist.cli.kernel import (
    audit_app,
    claim_app,
    evidence_app,
    init_command,
    transaction_app,
)
from super_scientist.cli.output import emit
from super_scientist.quality.runner import QualityCheckResult, run_quality_gate

_COMMAND_PATHS = (
    ("rule", "review", "import"),
    ("research-run", "create"),
    ("governance", "propose"),
    ("governance", "show"),
    ("improvement", "classify"),
    ("improvement", "report"),
    ("progress", "add"),
    ("progress", "validate"),
    ("progress", "status"),
    ("trail", "create"),
    ("trail", "add-node"),
    ("trail", "add-relation"),
    ("trail", "validate"),
    ("rule", "propose"),
    ("rule", "consolidate"),
    ("rule", "history"),
    ("primitive", "propose"),
    ("primitive", "evaluate"),
    ("hypothesis", "propose"),
    ("hypothesis", "revise"),
    ("model", "register"),
    ("verifier", "record"),
    ("handbook", "build"),
    ("handbook", "verify"),
    ("harness-eval", "create"),
    ("harness-eval", "record"),
    ("harness-eval", "report"),
    ("evidence", "add"),
    ("evidence", "show"),
    ("claim", "propose"),
    ("claim", "history"),
    ("transaction", "list"),
    ("audit", "verify"),
    ("quality-gate",),
    ("init",),
)


def _command_name(args: Sequence[str]) -> str:
    for path in _COMMAND_PATHS:
        if tuple(args[: len(path)]) == path:
            return " ".join(path)
    return "scientist-harness"


class JsonEnvelopeGroup(TyperGroup):
    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        raw_args = list(sys.argv[1:] if args is None else args)
        if "--json" not in raw_args:
            return super().main(
                args=raw_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        try:
            result = super().main(
                args=raw_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except ClickException as error:
            emit(
                _command_name(raw_args),
                False,
                True,
                errors=[{"code": "INVALID_ARGUMENT", "message": error.format_message()}],
            )
            raise SystemExit(error.exit_code) from None
        if isinstance(result, int) and result != 0:
            raise SystemExit(result)
        return result


app = typer.Typer(cls=JsonEnvelopeGroup, no_args_is_help=True)
app.command("init")(init_command)
app.add_typer(evidence_app, name="evidence")
app.add_typer(claim_app, name="claim")
app.add_typer(transaction_app, name="transaction")
app.add_typer(audit_app, name="audit")
app.add_typer(research_run_app, name="research-run")
app.add_typer(governance_app, name="governance")
app.add_typer(improvement_app, name="improvement")
app.add_typer(progress_app, name="progress")
app.add_typer(trail_app, name="trail")
app.add_typer(rule_app, name="rule")
app.add_typer(primitive_app, name="primitive")
app.add_typer(hypothesis_app, name="hypothesis")
app.add_typer(model_app, name="model")
app.add_typer(verifier_app, name="verifier")
app.add_typer(handbook_app, name="handbook")
app.add_typer(harness_eval_app, name="harness-eval")


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
