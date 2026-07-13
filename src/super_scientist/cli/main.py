import typer

from super_scientist.cli.kernel import (
    audit_app,
    claim_app,
    evidence_app,
    init_command,
    transaction_app,
)

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
