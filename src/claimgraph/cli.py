import os
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer

from claimgraph.config import load_config, resolve_question
from claimgraph.retrieve import EutilsError, connect_eutils, retrieve

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


class Stage(StrEnum):
    RETRIEVE = "retrieve"


@app.command()
def run(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    stage: Annotated[Stage | None, typer.Option(help="Run only this stage.")] = None,
    refresh: Annotated[
        bool, typer.Option(help="Search and fetch again instead of reusing cached records.")
    ] = False,
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are written.")] = Path("runs"),
) -> None:
    """Run the pipeline for one question; only the retrieve stage exists so far."""
    config = load_config(resolve_question(question))
    try:
        with connect_eutils(os.environ) as client:
            manifest = retrieve(config, runs_dir / config.id, client, refresh=refresh)
    except EutilsError as error:
        typer.echo(f"retrieve failed: {error}", err=True)
        raise typer.Exit(1) from error

    stage_report = manifest.retrieve
    dropped = ", ".join(f"{n} {reason}" for reason, n in stage_report.dropped.items()) or "none"
    counts = ", ".join(f"{n} by {m}" for m, n in stage_report.scope_counts.items())
    typer.echo(f"scope {stage_report.scope} ({counts})")
    typer.echo(
        f"{stage_report.found} found ({stage_report.date_range}); "
        f"{stage_report.fetched} fetched, {stage_report.cached} cached, "
        f"{len(stage_report.unavailable)} unavailable; {stage_report.requests} requests"
    )
    typer.echo(f"{stage_report.kept} kept; dropped: {dropped}")
