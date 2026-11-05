from importlib.metadata import version
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, help="Map where studies on one health question disagree.")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"claimgraph {version('claimgraph')}")
        raise typer.Exit


@app.callback()
def main(
    show_version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Map where studies on one health question disagree."""
