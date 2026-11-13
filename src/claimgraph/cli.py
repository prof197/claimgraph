import errno
import os
from datetime import date
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from claimgraph import batch, evaluation, extract, gold, labels, relabel, review
from claimgraph.config import QuestionConfig, load_config, resolve_question
from claimgraph.prices import PRICES_FILE, load_prices
from claimgraph.retrieve import Abstract, EutilsError, connect_eutils, retrieve, write_atomic

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
    EXTRACT = "extract"


Model = Annotated[extract.ModelChoice, typer.Option(help="The extraction model.")]
DryRun = Annotated[
    bool, typer.Option(help="Estimate what the unrecorded requests would cost; send nothing.")
]
Live = Annotated[
    bool,
    typer.Option(
        "--live/--replay",
        help="Send the requests that have no recording, through the batch API. Costs money.",
    ),
]
PricesFile = Annotated[Path, typer.Option(help="The dated price file.")]


@app.command()
def run(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    stage: Annotated[Stage | None, typer.Option(help="Run only this stage.")] = None,
    refresh: Annotated[
        bool, typer.Option(help="Search and fetch again instead of reusing cached records.")
    ] = False,
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are written.")] = Path("runs"),
    dev: Annotated[
        bool, typer.Option(help="Extract the dev sample instead of every kept abstract.")
    ] = False,
    model: Model = extract.ModelChoice.HAIKU,
    dry_run: DryRun = False,
    live: Live = False,
    prices_file: PricesFile = PRICES_FILE,
) -> None:
    """Run the pipeline for one question: retrieve, then extract.

    Extraction replays recorded responses by default and exits with status 1 if any is missing.
    """
    config = load_config(resolve_question(question))
    run_dir = runs_dir / config.id
    if stage in (None, Stage.RETRIEVE):
        _retrieve(config, run_dir, refresh)
    if stage in (None, Stage.EXTRACT):
        _extract(config, run_dir, dev, model, dry_run, live, prices_file)


def _retrieve(config: QuestionConfig, run_dir: Path, refresh: bool) -> None:
    try:
        with connect_eutils(os.environ) as client:
            manifest = retrieve(config, run_dir, client, refresh=refresh)
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


def _extract(
    config: QuestionConfig,
    run_dir: Path,
    dev: bool,
    model: extract.ModelChoice,
    dry_run: bool,
    live: bool,
    prices_file: Path,
) -> None:
    sample = "dev" if dev else "corpus"
    source = run_dir / "dev" / "abstracts.jsonl" if dev else run_dir / "abstracts.jsonl"
    try:
        abstracts = gold.read_abstracts(source)
    except (FileNotFoundError, ValidationError) as error:
        typer.echo(f"no current abstracts in {source}; run --stage retrieve first", err=True)
        raise typer.Exit(1) from error
    # Dev and corpus share recordings: a dev abstract is also in the corpus, with the same request.
    replay_dir = run_dir / "replay" / "extraction" / model.value
    sent = _send_batches(abstracts, config, replay_dir, model, dry_run, live, prices_file, sample)
    if sent is None:
        return
    outcome, summary = sent
    # The extract stage's own manifest: retrieve rewrites manifest.json whole.
    batch.write_manifest(run_dir / "extract" / "manifest.json", f"{sample}-{model.value}", summary)
    out = run_dir / "extract" / f"{sample}-{model.value}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, "".join(r.model_dump_json() + "\n" for r in outcome.results).encode())
    typer.echo(f"wrote {out}")
    _report_missing(outcome)


def _send_batches(
    abstracts: list[Abstract],
    config: QuestionConfig,
    replay_dir: Path,
    model: extract.ModelChoice,
    dry_run: bool,
    live: bool,
    prices_file: Path,
    sample: str,
) -> tuple[batch.BatchRun, batch.ExtractManifest] | None:
    """The shared part of run --stage extract and gold record: estimate, or replay and send."""
    prices = load_prices(prices_file)
    if dry_run:
        try:
            result = batch.estimate(abstracts, config, replay_dir, model, prices)
        except ValueError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error
        typer.echo(batch.describe_estimate(result, prices))
        return None
    try:
        send = batch.anthropic_batches(os.environ, on_poll=typer.echo) if live else None
    except RuntimeError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    outcome = batch.extract_batched(abstracts, config, replay_dir, model, send)
    summary = batch.summarize(outcome, extract.SETTINGS[model].model, prices)
    typer.echo(f"{sample}: {len(abstracts)} abstracts on {summary.model}")
    for line in batch.describe(outcome, summary):
        typer.echo(line)
    return outcome, summary


def _report_missing(outcome: batch.BatchRun) -> None:
    if outcome.missing:
        shown = ", ".join(f"{p} ({why})" for p, why in list(outcome.missing.items())[:10])
        more = len(outcome.missing) - 10
        typer.echo(
            f"missing: {shown}{f' and {more} more' if more > 0 else ''}; "
            "record them with --live after a dry run",
            err=True,
        )
        raise typer.Exit(1)


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


@gold_app.command("trim")
def gold_trim(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
) -> None:
    """Keep only the labeled gold abstracts; drops the rest of the draw and its blank templates."""
    config = load_config(resolve_question(question))
    try:
        trimmed = labels.trim_gold(gold_dir / config.id, config)
    except ValueError as error:
        typer.echo(f"not trimmed: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"kept {len(trimmed.kept)} labeled gold abstracts; dropped {len(trimmed.dropped)}")


@gold_app.command("relabel")
def gold_relabel(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
) -> None:
    """Draw records for a blind second labeling pass and write their blank templates."""
    config = load_config(resolve_question(question))
    try:
        picks = relabel.write_templates(gold_dir / config.id, config)
    except ValueError as error:
        typer.echo(f"not drawn: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(
        f"wrote {len(picks)} blank templates to {gold_dir / config.id / relabel.RELABEL_DIR}; "
        "label them with gold review --relabel"
    )


@gold_app.command("agreement")
def gold_agreement(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
    readme: Annotated[
        Path | None, typer.Option(help="Also write the table into this README's block.")
    ] = None,
) -> None:
    """Field-level agreement between the first labeling pass and the second, as a table."""
    config = load_config(resolve_question(question))
    try:
        first, second = relabel.read_passes(gold_dir / config.id, config)
        result = relabel.agreement(first, second, relabel.abstract_texts(gold_dir / config.id))
        table = relabel.agreement_table(result)
        if readme is not None:
            text = relabel.replace_block(readme.read_text(), relabel.AGREEMENT_BLOCK, table)
            readme.write_text(text)
    except ValueError as error:
        typer.echo(f"no agreement yet: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(table, nl=False)


@gold_app.command("record")
def gold_record(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
    replay_dir: Annotated[
        Path, typer.Option(help="Where the committed model recordings live.")
    ] = Path("replay"),
    model: Model = extract.ModelChoice.HAIKU,
    dry_run: DryRun = False,
    live: Live = False,
    prices_file: PricesFile = PRICES_FILE,
) -> None:
    """Run extraction on every gold abstract through the recordings the eval replays.

    In replay, the default, this checks that every current request is recorded and exits with
    status 1 if any is missing, as it is after a change to the prompt, schema or model settings.
    With --live it sends what is missing and removes the recordings no request uses.
    """
    config = load_config(resolve_question(question))
    abstracts = [g.abstract for g in labels.read_gold(gold_dir / config.id)]
    recordings = extract.gold_replay_dir(replay_dir, config, model)
    sent = _send_batches(abstracts, config, recordings, model, dry_run, live, prices_file, "gold")
    if sent is None:
        return
    outcome, _ = sent
    unused = batch.stale(recordings, outcome.used)
    if unused and live and not outcome.missing:
        for name in unused:
            (recordings / name).unlink()
        typer.echo(f"removed {len(unused)} stale recordings from {recordings}")
    elif unused:
        typer.echo(
            f"{len(unused)} stale recordings in {recordings}; a complete --live run removes them"
        )
    _report_missing(outcome)


@gold_app.command("review")
def gold_review(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
    page_dir: Annotated[
        Path, typer.Option(help="Where the local page and its notes are kept; never committed.")
    ] = review.PAGE_DIR,
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1; 0 picks a free one.")] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open a browser.")] = True,
    second_pass: Annotated[
        bool, typer.Option("--relabel", help="Label the blind second pass from gold relabel.")
    ] = False,
) -> None:
    """Serve a local page for labeling the gold abstracts; each edit is saved and checked."""
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    session = review.ReviewSession(
        out_dir, config, gold_dir / "LABELING.md", page_dir, relabel=second_pass
    )
    if second_pass and not session.gold:
        typer.echo("no second pass yet; run gold relabel first", err=True)
        raise typer.Exit(1)
    try:
        review.serve(session, review.write_page(page_dir), port, open_browser)
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        typer.echo(f"port {port} is in use; pass --port 0 to pick a free one", err=True)
        raise typer.Exit(1) from error


eval_app = typer.Typer(no_args_is_help=True, help="Score the pipeline against the gold labels.")
app.add_typer(eval_app, name="eval")


@eval_app.command("extraction")
def eval_extraction(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
    replay_dir: Annotated[
        Path, typer.Option(help="Where the committed model recordings live.")
    ] = Path("replay"),
    second_run: Annotated[
        Path | None,
        typer.Option(help="Recordings of a second live run, to report run-to-run agreement."),
    ] = None,
    model: Model = extract.ModelChoice.HAIKU,
    readme: Annotated[
        Path | None, typer.Option(help="Also write the tables into this README's blocks.")
    ] = None,
) -> None:
    """Field-level scores for extraction on the gold set, replayed from the recordings.

    Exits with status 1 if any gold abstract has no recording, so CI never calls out.
    """
    config = load_config(resolve_question(question))
    try:
        scores = evaluation.gold_scores(gold_dir, replay_dir, config, model)
        table = evaluation.extraction_table(scores)
        rerun = None
        if second_run is not None:
            result = evaluation.rerun_agreement(gold_dir, replay_dir, second_run, config, model)
            rerun = relabel.agreement_table(result, passes="Second live run", unit="run")
        if readme is not None:
            text = relabel.replace_block(readme.read_text(), evaluation.EXTRACTION_BLOCK, table)
            if rerun is not None:
                text = relabel.replace_block(text, evaluation.RERUN_BLOCK, rerun)
            readme.write_text(text)
    except ValueError as error:
        typer.echo(f"no scores: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(table, nl=False)
    if scores.wrong_designs:
        typer.echo("\nWrong designs:")
        for line in evaluation.wrong_design_lines(scores):
            typer.echo(f"- {line}")
    if rerun is not None:
        typer.echo(f"\n{rerun}", nl=False)
