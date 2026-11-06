import os
from datetime import date
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from claimgraph import gold, labels
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


gold_app = typer.Typer(no_args_is_help=True, help="Draw and inspect the eval samples.")
app.add_typer(gold_app, name="gold")


@gold_app.command("sample")
def gold_sample(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    seed: Annotated[int, typer.Option(help="Seed for the sampling order.")] = gold.SEED,
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = Path("runs"),
    gold_dir: Annotated[Path, typer.Option(help="Where the samples are written.")] = Path(
        "evals/gold"
    ),
) -> None:
    """Draw the gold and dev samples from openly licensed retrieved abstracts.

    Records the walk reaches must be screened in the question's screening.toml first;
    unscreened ones are written to the run directory and the command exits with status 2.
    """
    config = load_config(resolve_question(question))
    run_dir = runs_dir / config.id
    out_dir = gold_dir / config.id
    try:
        abstracts = gold.read_abstracts(run_dir / "abstracts.jsonl")
    except (FileNotFoundError, ValidationError) as error:
        typer.echo(f"no current abstracts in {run_dir}; run --stage retrieve first", err=True)
        raise typer.Exit(1) from error
    screening = gold.load_screening(out_dir / "screening.toml")
    try:
        with (
            connect_eutils(os.environ) as eutils,
            gold.connect_bucket(run_dir / "pmc") as bucket,
        ):
            result, manifest = gold.sample(
                config.id,
                abstracts,
                screening,
                eutils,
                bucket,
                cache_dir=run_dir / "pmc",
                seed=seed,
                today=date.today(),
            )
            requests = eutils.requests + bucket.requests
    except EutilsError as error:
        typer.echo(f"sampling failed: {error}", err=True)
        raise typer.Exit(1) from error

    pool = manifest.open_pool.records
    typer.echo(f"{pool} of {len(abstracts)} kept abstracts are CC BY or CC0; {requests} requests")
    if result.pending:
        by_pmid = {a.pmid: a for a in abstracts}
        path = run_dir / "to_screen.jsonl"
        path.write_text("".join(by_pmid[p].model_dump_json() + "\n" for p in result.pending))
        typer.echo(f"{len(result.pending)} records need screening first; see {path}", err=True)
        raise typer.Exit(2)
    assert result.dev is not None
    short = {
        split: draw.shortfall
        for split, draw in ((gold.Split.GOLD, result.gold), (gold.Split.DEV, result.dev))
        if draw.shortfall
    }
    if short:
        typer.echo(f"the pool cannot fill the quotas: {short}", err=True)
        raise typer.Exit(1)
    gold.write_sample(out_dir, run_dir, result, manifest)
    skipped = ", ".join(f"{n} {reason}" for reason, n in manifest.skipped.items()) or "none"
    typer.echo(
        f"wrote {out_dir}: {len(result.gold.picks)} gold, {len(result.dev.picks)} dev; "
        f"dev abstracts in {run_dir / 'dev'}"
    )
    typer.echo(f"gold candidates skipped by the licence check: {skipped}")


GoldDir = Annotated[Path, typer.Option(help="Where the samples and labels live.")]


@gold_app.command("template")
def gold_template(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
) -> None:
    """Write a label template for each gold abstract that has none; never overwrites."""
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    written = labels.write_templates(out_dir, config)
    typer.echo(f"wrote {len(written)} templates to {out_dir / 'labels'}")


@gold_app.command("check")
def gold_check(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
) -> None:
    """Validate the question's label files; exits with status 1 if any has a problem."""
    config = load_config(resolve_question(question))
    report = labels.check_labels(gold_dir / config.id, config)
    total = len(report.labeled) + len(report.missing) + len(report.problems)
    for name, problems in report.problems.items():
        for problem in problems:
            typer.echo(f"{name}: {problem}", err=True)
    typer.echo(
        f"{len(report.labeled)} labeled, {len(report.problems)} with problems, "
        f"{len(report.missing)} not started, of {total}"
    )
    if report.problems:
        raise typer.Exit(1)
