import errno
import os
from datetime import date
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from claimgraph import (
    arms,
    batch,
    classify,
    compare,
    contradiction_review,
    contradictions,
    evaluation,
    export_build,
    extract,
    gold,
    kind_review,
    label_set,
    labels,
    models,
    normalization_eval,
    normalize,
    outcome_kinds,
    outcome_pairs,
    pair_sample,
    paths,
    publish,
    relabel,
    review,
    vendors,
)
from claimgraph.config import QuestionConfig, load_config, resolve_question
from claimgraph.llm import ReplayMiss, request_key
from claimgraph.models import NormalizedOutcome
from claimgraph.prices import PRICES_FILE, cost, load_prices
from claimgraph.retrieve import Abstract, EutilsError, connect_eutils, retrieve, write_atomic

# Read once at startup: CLAIMGRAPH_DATA can move the gitignored data out of the checkout.
RUNS_DIR = paths.runs_dir()
PAGE_DIR = review.page_dir()

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
    NORMALIZE = "normalize"
    COMPARE = "compare"


Model = Annotated[extract.ModelChoice, typer.Option(help="The extraction model.")]
DryRun = Annotated[
    bool, typer.Option(help="Estimate what the unrecorded requests would cost; send nothing.")
]
Live = Annotated[
    bool,
    typer.Option(
        "--live/--replay",
        help="Send the requests that have no recording; extraction goes through the batch API. "
        "Costs money.",
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
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are written.")] = RUNS_DIR,
    dev: Annotated[
        bool, typer.Option(help="Extract the dev sample instead of every kept abstract.")
    ] = False,
    model: Model = extract.DEFAULT_MODEL,
    limit: Annotated[
        int | None,
        typer.Option(
            min=1,
            help="Extract only the first N kept abstracts, a first stage the full run replays.",
        ),
    ] = None,
    dry_run: DryRun = False,
    live: Live = False,
    prices_file: PricesFile = PRICES_FILE,
    replay_dir: Annotated[
        Path, typer.Option(help="Where the committed outcome proposal recordings live.")
    ] = Path("replay"),
    mappings_dir: Annotated[
        Path, typer.Option(help="Where the outcome mappings are written.")
    ] = Path("mappings"),
) -> None:
    """Run the pipeline for one question: retrieve, extract, propose the outcome mapping, compare.

    Model calls replay recorded responses by default and exit with status 1 if any is missing.
    """
    config = load_config(resolve_question(question))
    run_dir = runs_dir / config.id
    if stage in (None, Stage.RETRIEVE):
        _retrieve(config, run_dir, refresh)
    if stage in (None, Stage.EXTRACT):
        _extract(config, run_dir, dev, model, dry_run, live, prices_file, limit)
    if stage in (None, Stage.NORMALIZE):
        _normalize(config, run_dir, model, dry_run, live, prices_file, replay_dir, mappings_dir)
    # A dry run has extracted and mapped nothing yet to compare.
    if stage is Stage.COMPARE or (stage is None and not dry_run):
        _compare(config, run_dir, model, mappings_dir)


def _compare(
    config: QuestionConfig, run_dir: Path, model: extract.ModelChoice, mappings_dir: Path
) -> None:
    results = _corpus_results(run_dir, model)
    try:
        abstracts = gold.read_abstracts(run_dir / "abstracts.jsonl")
    except FileNotFoundError as error:
        typer.echo(f"no abstracts in {run_dir}; run --stage retrieve first", err=True)
        raise typer.Exit(1) from error
    mapping = _load_mapping(mappings_dir / f"{config.id}.toml")
    try:
        found = compare.candidates(
            results,
            {a.pmid: a.trial_ids for a in abstracts},
            mapping,
            config.exclude_designs,
        )
    except ValueError as error:
        typer.echo(f"{error}; run mapping check", err=True)
        raise typer.Exit(1) from error
    out = run_dir / "compare" / "candidates.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, (found.model_dump_json(indent=1) + "\n").encode())
    studies = len({h.study for h in found.headlines})
    typer.echo(
        f"{len(found.pairs)} candidate pairs from {studies} studies on {len(found.blocks)} "
        f"outcomes; {found.out_of_domain} findings dropped as out of domain"
    )
    if not all(o.reviewed for o in mapping.outcomes):
        typer.echo("from an unreviewed mapping, which the hosted build refuses")
    for block in found.blocks:
        alone = f", {block.subgroup_only} with only subgroup results" if block.subgroup_only else ""
        typer.echo(f"{block.pairs:>6} pairs {block.studies:>3} studies  {block.outcome}{alone}")
    typer.echo(f"wrote {out}")


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
    limit: int | None = None,
) -> None:
    sample = "dev" if dev else "corpus"
    source = run_dir / "dev" / "abstracts.jsonl" if dev else run_dir / "abstracts.jsonl"
    try:
        abstracts = gold.read_abstracts(source)
    except (FileNotFoundError, ValidationError) as error:
        typer.echo(f"no current abstracts in {source}; run --stage retrieve first", err=True)
        raise typer.Exit(1) from error
    if limit is not None:
        # A first stage, so its measured cost can be checked before the rest is sent. Its
        # recordings are shared, so the full run replays them.
        abstracts = abstracts[:limit]
        sample = f"{sample}-first{limit}"
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


def _corpus_results(run_dir: Path, model: extract.ModelChoice) -> list[extract.ExtractionResult]:
    source = run_dir / "extract" / f"corpus-{model.value}.jsonl"
    try:
        lines = source.read_text().splitlines()
        return [extract.ExtractionResult.model_validate_json(line) for line in lines]
    except (FileNotFoundError, ValidationError) as error:
        typer.echo(f"no corpus extraction in {source}; run --stage extract first", err=True)
        raise typer.Exit(1) from error


def _corpus_strings(run_dir: Path, model: extract.ModelChoice) -> list[str]:
    """The outcome strings of the corpus extraction's findings."""
    return normalize.outcome_strings(_corpus_results(run_dir, model))


def _normalize(
    config: QuestionConfig,
    run_dir: Path,
    model: extract.ModelChoice,
    dry_run: bool,
    live: bool,
    prices_file: Path,
    replay_root: Path,
    mappings_dir: Path,
) -> None:
    strings = _corpus_strings(run_dir, model)
    out = mappings_dir / f"{config.id}.toml"
    existing: list[NormalizedOutcome] = []
    if out.exists():
        # A rerun proposes only the strings the mapping has not seen, and appends them, so the
        # reviewer's edits are never rewritten and review effort does not repeat.
        mapping = _load_mapping(out)
        existing = mapping.outcomes
        strings = normalize.unmapped(mapping, strings)
        if not strings:
            typer.echo(f"every outcome string is mapped; nothing to propose for {out}")
            return
    replay_dir = replay_root / config.id / "normalization" / model.value
    prices = load_prices(prices_file)
    if dry_run:
        try:
            result = normalize.estimate(strings, config, replay_dir, model, prices, existing)
        except ValueError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error
        state = "recorded, so nothing is sent" if result.recorded else "not recorded yet"
        typer.echo(
            f"dry run: {result.strings} outcome strings to send to {result.model} in one "
            f"request, about {result.input_tokens} input tokens ({state}); typical "
            f"${result.typical_usd:.4f}, at most ${result.ceiling_usd:.4f} at standard prices "
            f"(prices checked {prices.checked})"
        )
        return
    try:
        client = normalize.model_client(replay_dir, live)
        proposal = normalize.propose(strings, config, client, model, existing)
    except (RuntimeError, ReplayMiss) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    spent = sum(cost(u, proposal.model, prices, batch=False) for u in proposal.usage)
    tokens = sum(u.input_tokens + u.cache_read_input_tokens for u in proposal.usage)
    output = sum(u.output_tokens for u in proposal.usage)
    typer.echo(
        f"{len(proposal.usage)} requests to {proposal.model}; tokens: {tokens} input, "
        f"{output} output; ${spent:.4f} at standard prices"
    )
    if proposal.outcomes is None:
        typer.echo(f"invalid proposal: {'; '.join(proposal.errors)}", err=True)
        raise typer.Exit(1)
    if existing:
        known = {o.name.casefold() for o in existing}
        joined = sum(len(o.members) for o in proposal.outcomes if o.name.casefold() in known)
        added = normalize.mapping_toml(proposal.outcomes, normalize.rerun_header(proposal))
        write_atomic(out, (out.read_text() + "\n" + added).encode())
        typer.echo(
            f"{proposal.strings} new outcome strings: {joined} into existing outcomes, "
            f"{proposal.strings - joined} into new ones"
        )
    else:
        header = normalize.mapping_header(config, proposal)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(out, normalize.mapping_toml(proposal.outcomes, header).encode())
        typer.echo(f"{proposal.strings} outcome strings in {len(proposal.outcomes)} outcomes")
    for outcome in proposal.outcomes:
        better = "higher is better" if outcome.higher_is_better else "lower is better"
        typer.echo(f"  {len(outcome.members):>4}  {outcome.name} ({better})")
    typer.echo(f"wrote {out}, unreviewed" if not existing else f"appended to {out}, unreviewed")


def _load_mapping(path: Path) -> normalize.OutcomeMapping:
    try:
        return normalize.load_mapping(path)
    except FileNotFoundError as error:
        typer.echo(f"no mapping at {path}; run --stage normalize first", err=True)
        raise typer.Exit(1) from error
    except ValueError as error:
        # Malformed TOML, a table that fails validation, or tables that disagree.
        typer.echo(f"{path} is not a valid mapping: {error}", err=True)
        raise typer.Exit(1) from error


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


mapping_app = typer.Typer(no_args_is_help=True, help="Inspect the outcome mappings.")
app.add_typer(mapping_app, name="mapping")


@mapping_app.command("check")
def mapping_check(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    mappings_dir: Annotated[
        Path, typer.Option(help="Where the outcome mappings are read from.")
    ] = Path("mappings"),
    model: Model = extract.DEFAULT_MODEL,
) -> None:
    """Check the outcome mapping can be published: every outcome reviewed, every string mapped.

    Exits with status 1 if not, naming what is left.
    """
    config = load_config(resolve_question(question))
    results = _corpus_results(runs_dir / config.id, model)
    strings = normalize.outcome_strings(results)
    mapping = _load_mapping(mappings_dir / f"{config.id}.toml")
    reviewed = sum(o.reviewed for o in mapping.outcomes)
    missing = normalize.unmapped(mapping, strings)
    typer.echo(
        f"{len(mapping.outcomes)} outcomes, {reviewed} reviewed; "
        f"{len(strings)} strings, {len(missing)} unmapped"
    )
    if mapping.out_of_domain:
        typer.echo(
            f"{len(mapping.out_of_domain)} strings out of domain, dropping "
            f"{normalize.dropped_findings(results, mapping)} findings"
        )
    for override in mapping.polarity_overrides:
        better = "higher is better" if override.higher_is_better else "lower is better"
        typer.echo(f"polarity override: {override.string!r}, {better}")
    unused = normalize.unused_members(mapping, strings)
    if unused:
        typer.echo(f"{len(unused)} members no finding reports, kept")
    problems = normalize.check_mapping(mapping, strings)
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        raise typer.Exit(1)


gold_app = typer.Typer(no_args_is_help=True, help="Draw and inspect the eval samples.")
app.add_typer(gold_app, name="gold")


@gold_app.command("sample")
def gold_sample(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    seed: Annotated[int, typer.Option(help="Seed for the sampling order.")] = gold.SEED,
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
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
    kinds = gold_dir / config.id / outcome_kinds.LABEL_FILE
    kind_problems = outcome_kinds.check(kinds.parent).problems if kinds.exists() else []
    for problem in kind_problems:
        typer.echo(f"{kinds.name}: {problem}", err=True)
    if report.problems or kind_problems:
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
    model: Model = extract.DEFAULT_MODEL,
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
    ] = PAGE_DIR,
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
        page = review.write_page(page_dir)
        review.serve(
            lambda method, path, body, kind: review.handle(session, page, method, path, body, kind),
            port,
            open_browser,
        )
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        typer.echo(f"port {port} is in use; pass --port 0 to pick a free one", err=True)
        raise typer.Exit(1) from error


pairs_app = typer.Typer(
    no_args_is_help=True, help="Draw, template and check the outcome-pair labels."
)
gold_app.add_typer(pairs_app, name="outcome-pairs")


@pairs_app.command("sample")
def pairs_sample(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    seed: Annotated[int, typer.Option(help="Seed for the draw.")] = outcome_pairs.SEED,
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    replay_dir: Annotated[
        Path, typer.Option(help="Where the recorded proposal is read from.")
    ] = Path("replay"),
    gold_dir: GoldDir = Path("evals/gold"),
    model: Model = extract.DEFAULT_MODEL,
) -> None:
    """Draw the outcome pairs to label from the recorded proposal; never redraws over a sample.

    The strata come from the proposal as the model made it, not from the reviewed mapping.
    """
    config = load_config(resolve_question(question))
    out = gold_dir / config.id / outcome_pairs.SAMPLE_FILE
    if out.exists():
        typer.echo(f"{out} is already drawn; delete it to draw again", err=True)
        raise typer.Exit(1)
    strings = _corpus_strings(runs_dir / config.id, model)
    client = normalize.model_client(replay_dir / config.id / "normalization" / model.value, False)
    try:
        proposal = normalize.propose(strings, config, client, model)
    except ReplayMiss as error:
        typer.echo(
            f"no recorded outcome proposal for these strings in {replay_dir}; "
            "run --stage normalize first",
            err=True,
        )
        raise typer.Exit(1) from error
    if proposal.outcomes is None:
        typer.echo(f"the recorded proposal is invalid: {'; '.join(proposal.errors)}", err=True)
        raise typer.Exit(1)
    groups = {s: o.name for o in proposal.outcomes for s in o.members}
    result = outcome_pairs.draw(strings, groups, config.outcome_hard_cases, seed=seed)
    key = request_key(normalize.request(strings, config, model))
    drawn = outcome_pairs.sample(config.id, seed, proposal.model, key, len(strings), result)
    outcome_pairs.write_sample(out, drawn)
    typer.echo(f"{len(drawn.pairs)} pairs drawn from {len(strings)} outcome strings")
    for s in outcome_pairs.Stratum:
        typer.echo(f"  {drawn.drawn[s]:>4} of {drawn.pools[s]:>6}  {s}")
    typer.echo(f"wrote {out}; open it only after labeling, as it shows the strata")


@pairs_app.command("template")
def pairs_template(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
) -> None:
    """Write the outcome-pair label file, shuffled and without strata; never overwrites."""
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    source = out_dir / outcome_pairs.SAMPLE_FILE
    if not source.exists():
        typer.echo(f"no draw in {source}; run gold outcome-pairs sample first", err=True)
        raise typer.Exit(1)
    try:
        path = outcome_pairs.write_label_template(out_dir, outcome_pairs.load_sample(source))
    except FileExistsError as error:
        typer.echo(f"{error} already exists and may hold labels; not overwritten", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"wrote {path}")


@pairs_app.command("check")
def pairs_check(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
) -> None:
    """Check the outcome-pair labels against the draw; exits 1 until every pair is labeled."""
    config = load_config(resolve_question(question))
    report = labels.check_outcome_pairs(gold_dir / config.id)
    labeled = sum(report.verdicts.values())
    total = report.drawn if report.drawn is not None else labeled + report.unlabeled
    counts = ", ".join(f"{n} {v}" for v, n in report.verdicts.items())
    typer.echo(f"{labeled} of {total} pairs labeled: {counts}")
    for problem in report.problems:
        typer.echo(problem, err=True)
    if report.problems:
        raise typer.Exit(1)


kinds_app = typer.Typer(
    no_args_is_help=True, help="Label each outcome string by what it measures, and check it."
)
gold_app.add_typer(kinds_app, name="outcome-kinds")


@kinds_app.command("template")
def kinds_template(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    gold_dir: GoldDir = Path("evals/gold"),
    model: Model = extract.DEFAULT_MODEL,
) -> None:
    """Write the outcome-kind label file: every string of the recorded proposal, no kinds.

    The strings must be the ones the outcome pairs were drawn from, so the eval scores the
    proposal the pairs came from. Never overwrites a label file.
    """
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    source = out_dir / outcome_pairs.SAMPLE_FILE
    if not source.exists():
        typer.echo(f"no draw in {source}; run gold outcome-pairs sample first", err=True)
        raise typer.Exit(1)
    strings = _corpus_strings(runs_dir / config.id, model)
    if request_key(normalize.request(strings, config, model)) != (
        outcome_pairs.load_sample(source).proposal
    ):
        typer.echo(
            f"the corpus strings in {runs_dir} are not the ones the recorded proposal in "
            f"{source} was made on",
            err=True,
        )
        raise typer.Exit(1)
    try:
        path = outcome_kinds.write_template(out_dir, config.id, strings)
    except FileExistsError as error:
        typer.echo(f"{error} already exists and may hold labels; not overwritten", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"wrote {path}: {len(strings)} strings")


@kinds_app.command("check")
def kinds_check(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
) -> None:
    """Check the outcome kinds against the draw; exits 1 until every string has a kind."""
    config = load_config(resolve_question(question))
    report = outcome_kinds.check(gold_dir / config.id)
    labeled = [label for label in report.labels if label.kind]
    counts = ", ".join(f"{report.count(k)} {k}" for k in outcome_kinds.Kind)
    unsure = sum(label.unsure for label in report.labels)
    typer.echo(f"{len(labeled)} of {len(report.labels)} strings labeled: {counts}; {unsure} unsure")
    for problem in report.problems:
        typer.echo(problem, err=True)
    if report.problems:
        raise typer.Exit(1)


@kinds_app.command("compare")
def kinds_compare(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
    pairs: Annotated[
        Path | None,
        typer.Option(help="Pair verdicts entered by hand; the question's outcome_pairs.toml."),
    ] = None,
) -> None:
    """List the pair verdicts entered by hand that the outcome kinds do not give."""
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    pairs_path = pairs or out_dir / outcome_pairs.LABEL_FILE
    try:
        labels_found = outcome_kinds.load(out_dir / outcome_kinds.LABEL_FILE)
        entered = outcome_kinds.read_entered_pairs(pairs_path)
    except (OSError, ValueError) as error:
        typer.echo(f"not compared: {error}", err=True)
        raise typer.Exit(1) from error
    for problem in entered.problems:
        typer.echo(problem, err=True)
    result = outcome_kinds.compare(entered.pairs, labels_found)
    typer.echo(
        f"{len(entered.pairs)} pair verdicts entered and {entered.blank} left blank. "
        f"Against the kinds: {result.agree} agree, {len(result.disagree)} disagree, "
        f"{result.waiting} wait on a string with no kind."
    )
    for d in result.disagree:
        unsure = "; a kind is marked unsure" if d.unsure else ""
        comment = f"; your comment: {d.comment}" if d.comment else ""
        typer.echo(
            f"- {d.a} / {d.b}: entered {d.entered}, kinds give {d.derived} "
            f"({d.kind_a} / {d.kind_b}){unsure}{comment}"
        )


@kinds_app.command("review")
def kinds_review(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1; 0 picks a free one.")] = (
        kind_review.PORT
    ),
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open a browser.")] = True,
) -> None:
    """Serve a local page for labeling each outcome string by kind; every click is saved.

    The page shows the strings alone, shuffled, beside the guide's rules, and never the
    model's grouping.
    """
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    session = kind_review.KindSession(out_dir, config.id, config.title, gold_dir / "LABELING.md")
    try:
        session.state()
    except FileNotFoundError as error:
        missing = f"no kind file in {out_dir}; run gold outcome-kinds template first"
        typer.echo(missing if not session.path.exists() else str(error), err=True)
        raise typer.Exit(1) from error
    except ValueError as error:
        typer.echo(f"{session.path} does not read: {error}", err=True)
        raise typer.Exit(1) from error
    try:
        review.serve(
            lambda method, path, body, kind: kind_review.handle(session, method, path, body, kind),
            port,
            open_browser,
        )
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
    model: Model = extract.DEFAULT_MODEL,
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


@eval_app.command("normalization")
def eval_normalization(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
    replay_dir: Annotated[
        Path, typer.Option(help="Where the committed model recordings live.")
    ] = Path("replay"),
    model: Model = extract.DEFAULT_MODEL,
    baseline: Annotated[
        bool,
        typer.Option(
            "--baseline", help="Score the proposal the pairs were drawn from, not the current one."
        ),
    ] = False,
    readme: Annotated[
        Path | None,
        typer.Option(help="Write both proposals, before and after corrections, into this README."),
    ] = None,
) -> None:
    """Merge precision and recall of the recorded outcome proposal against the outcome kinds.

    The current prompt's proposal is replayed from the strings in the kind file, so nothing is
    sent; exits with status 1 if its recording is missing. The strata are always the draw's.
    """
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    replay = replay_dir / config.id / "normalization" / model.value
    try:
        if readme is not None:
            block = normalization_eval.readme(out_dir, replay, config, model)
            text = readme.read_text()
            readme.write_text(relabel.replace_block(text, normalization_eval.README_BLOCK, block))
            typer.echo(block, nl=False)
            return
        found = normalization_eval.labeled(out_dir)
        drawn = outcome_pairs.load_sample(out_dir / outcome_pairs.SAMPLE_FILE)
        proposal = normalization_eval.replayed(found, config, replay, model, drawn, baseline)
        assert proposal.outcomes is not None
        scores = normalization_eval.score(found, proposal.outcomes, proposal.outside, drawn)
    except (OSError, ValueError) as error:
        typer.echo(f"no scores: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(normalization_eval.table(scores), nl=False)


@eval_app.command("models")
def eval_models(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    gold_dir: GoldDir = Path("evals/gold"),
    replay_dir: Annotated[
        Path, typer.Option(help="Where the committed model recordings live.")
    ] = Path("replay"),
    second_run: Annotated[
        Path | None,
        typer.Option(help="Recordings of a second live run, shown as rows of their own."),
    ] = None,
    prices_file: PricesFile = PRICES_FILE,
    readme: Annotated[
        Path | None, typer.Option(help="Also write the table into this README's block.")
    ] = None,
) -> None:
    """Every model with gold recordings side by side, with what its responses cost."""
    config = load_config(resolve_question(question))
    prices = load_prices(prices_file)
    try:
        rows = evaluation.recorded_models(gold_dir, replay_dir, config, prices, second_run)
    except ValueError as error:
        typer.echo(f"no scores: {error}", err=True)
        raise typer.Exit(1) from error
    table = evaluation.models_table(rows)
    if readme is not None:
        text = relabel.replace_block(readme.read_text(), evaluation.MODELS_BLOCK, table)
        readme.write_text(text)
    typer.echo(table, nl=False)


export_app = typer.Typer(no_args_is_help=True, help="Build the page's export for a question.")
app.add_typer(export_app, name="export")


def _candidates(run_dir: Path) -> compare.Candidates:
    source = run_dir / "compare" / "candidates.json"
    try:
        return compare.Candidates.model_validate_json(source.read_text())
    except (FileNotFoundError, ValidationError) as error:
        typer.echo(f"no candidate pairs in {source}; run --stage compare first", err=True)
        raise typer.Exit(1) from error


@export_app.command("stub")
def export_stub(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
) -> None:
    """Classify a fixed share of the candidate pairs without a model, to build the page against.

    The verdicts are arbitrary and marked as stubs; the hosted build refuses them.
    """
    config = load_config(resolve_question(question))
    run_dir = runs_dir / config.id
    stubbed = contradictions.stub(_candidates(run_dir))
    out = run_dir / "classify" / contradictions.STUB_FILE
    contradictions.write(out, stubbed)
    typer.echo(f"{len(stubbed.classifications)} of {stubbed.candidates} pairs stubbed; wrote {out}")


def _build_export(
    question: str, runs_dir: Path, mappings_dir: Path, gold_dir: Path, stub: bool
) -> tuple[Path, str]:
    """The run's directory and its export as written to disk."""
    config = load_config(resolve_question(question))
    run_dir = runs_dir / config.id
    source = run_dir / "classify" / (contradictions.STUB_FILE if stub else contradictions.FILE)
    try:
        classified = contradictions.read(source)
    except (FileNotFoundError, ValidationError) as error:
        typer.echo(f"no classified pairs in {source}", err=True)
        raise typer.Exit(1) from error
    mapping = _load_mapping(mappings_dir / f"{config.id}.toml")
    built = export_build.build_export(config, run_dir, mapping, classified, gold_dir / config.id)
    return run_dir, built.model_dump_json(indent=2) + "\n"


@export_app.command("build")
def export_build_command(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    mappings_dir: Annotated[
        Path, typer.Option(help="Where the outcome mappings are read from.")
    ] = Path("mappings"),
    gold_dir: GoldDir = Path("evals/gold"),
    stub: Annotated[
        bool, typer.Option(help="Read the stub contradictions instead of the classified pairs.")
    ] = False,
    out: Annotated[
        Path | None, typer.Option(help="Where to write the export; by default in the run.")
    ] = None,
) -> None:
    """Write the export the page reads: summaries, the graph, contradictions and the method."""
    run_dir, text = _build_export(question, runs_dir, mappings_dir, gold_dir, stub)
    target = out or run_dir / "export" / f"{run_dir.name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(target, text.encode())
    typer.echo(f"wrote {target}")


LockFile = Annotated[Path, typer.Option(help="The committed file pinning the hosted export.")]


@export_app.command("publish")
def export_publish(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    mappings_dir: Annotated[
        Path, typer.Option(help="Where the outcome mappings are read from.")
    ] = Path("mappings"),
    gold_dir: GoldDir = Path("evals/gold"),
    lock: LockFile = publish.LOCK_FILE,
) -> None:
    """Build the hosted export, upload it to the GitHub release with gh, and pin its hash.

    The release is public. Refuses an unreviewed mapping or stub contradictions. Commit the
    updated lock file afterwards; the deploy builds from the export it pins.
    """
    run_dir, text = _build_export(question, runs_dir, mappings_dir, gold_dir, stub=False)
    try:
        pinned = publish.publish(text, run_dir / "export", lock)
    except publish.PublishError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"published {pinned.asset} to release {pinned.tag}; commit {lock}")


@export_app.command("fetch")
def export_fetch(
    out: Annotated[Path, typer.Option(help="Where the page's build reads the export.")] = Path(
        "site/public/export.json"
    ),
    lock: LockFile = publish.LOCK_FILE,
) -> None:
    """Download the pinned export for the page's build, refusing it unless its hash matches."""
    try:
        pinned = publish.read_lock(lock)
        publish.fetch(pinned, out)
    except (FileNotFoundError, ValidationError, publish.PublishError) as error:
        typer.echo(f"no verified export: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"fetched {pinned.asset} to {out}; SHA-256 matches")


contradiction_app = typer.Typer(
    no_args_is_help=True, help="Draw and label the contradiction eval's pairs."
)
gold_app.add_typer(contradiction_app, name="contradiction-pairs")
# The labeling page's commands, beside the draw they label.
contradiction_app.command("template")(contradiction_review.template_command)
contradiction_app.command("check")(contradiction_review.check_command)
contradiction_app.command("review")(contradiction_review.review_command)


@contradiction_app.command("sample")
def contradiction_sample(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    seed: Annotated[int, typer.Option(help="Seed for the draw.")] = pair_sample.SEED,
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    gold_dir: GoldDir = Path("evals/gold"),
    model: Model = extract.DEFAULT_MODEL,
) -> None:
    """Draw the eval's pairs from openly licensed studies, and the dev pairs from the rest.

    Checks licences with PMC's licence filters and the PMC bucket's metadata, both cached.
    """
    config = load_config(resolve_question(question))
    run_dir = runs_dir / config.id
    found = _candidates(run_dir)
    results = _corpus_results(run_dir, model)
    designs = {r.pmid: r.study.design for r in results if r.study is not None}
    abstracts = gold.read_abstracts(run_dir / "abstracts.jsonl")
    pairs = pair_sample.drawn_pairs(found, designs)
    try:
        with (
            connect_eutils(os.environ) as eutils,
            gold.connect_bucket(run_dir / "pmc") as bucket,
        ):
            is_open = gold.openly_licensed(abstracts, eutils, bucket, run_dir / "pmc", date.today())
            sample = pair_sample.draw(config.id, pairs, is_open, seed)
            by_pmid = {a.pmid: a for a in abstracts}
            papers = [by_pmid[p] for p in sorted(pair_sample.eval_papers(sample))]
            licences: dict[str, gold.Licence] = {}
            for paper in papers:
                assert paper.pmcid is not None
                found = gold.licence(bucket.versions(paper.pmcid))
                assert isinstance(found, gold.Licence)
                licences[paper.pmid] = found
            checked_on = gold.open_pool(abstracts, eutils, run_dir / "pmc", date.today()).checked_on
    except EutilsError as error:
        typer.echo(f"licence check failed: {error}", err=True)
        raise typer.Exit(1) from error
    out = gold_dir / config.id / pair_sample.SAMPLE_FILE
    write_atomic(out, (sample.model_dump_json(indent=2) + "\n").encode())
    credits = out.parent / pair_sample.ATTRIBUTION_FILE
    write_atomic(credits, gold.pair_attribution(papers, licences, checked_on).encode())
    typer.echo(
        pair_sample.strata_table(pairs, pair_sample.licensed(pairs, is_open), sample), nl=False
    )
    typer.echo(f"wrote {out} and {credits}")


class Split(StrEnum):
    EVAL = "eval"
    DEV = "dev"
    # Every candidate pair of the run: what the hosted page shows.
    CORPUS = "corpus"


def classification_replay(split: Split, arm: models.Arm, committed: Path, run_dir: Path) -> Path:
    """Eval recordings are committed for CI; dev and corpus pairs can carry any licence, so
    theirs stay in the gitignored run directory."""
    root = committed if split is Split.EVAL else run_dir / "replay"
    return root / "classification" / arm.value


@app.command("classify")
def classify_command(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    split: Annotated[
        Split, typer.Option(help="The drawn eval or dev pairs, or every candidate pair.")
    ] = Split.DEV,
    arm: Annotated[
        models.Arm, typer.Option(help="The classification arm.")
    ] = models.Arm.SINGLE_SMALL,
    dry_run: Annotated[bool, typer.Option(help="Estimate the cost; send nothing.")] = False,
    live: Annotated[
        bool, typer.Option(help="Send unrecorded requests through batches (costs money).")
    ] = False,
    k: Annotated[
        int | None,
        typer.Option(
            help="Self-consistency's samples per pair; by default the k matched to "
            "council-ranked's measured tokens.",
            min=1,
        ),
    ] = None,
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    mappings_dir: Annotated[
        Path, typer.Option(help="Where the outcome mappings are read from.")
    ] = Path("mappings"),
    gold_dir: GoldDir = Path("evals/gold"),
    replay_root: Annotated[
        Path, typer.Option(help="Where the committed eval recordings live.")
    ] = Path("replay"),
    prices_file: PricesFile = PRICES_FILE,
    model: Model = extract.DEFAULT_MODEL,
) -> None:
    """Classify the drawn eval or dev pairs, or the whole corpus's candidate pairs, with one
    arm, replaying recorded responses.

    Dev and corpus recordings stay in the run directory; eval recordings are committed for CI
    to replay. A dry run also scales the current dev run's measured cost to the pairs it counts.
    """
    config = load_config(resolve_question(question))
    run_dir = runs_dir / config.id
    sample = pair_sample.read_sample(gold_dir / config.id / pair_sample.SAMPLE_FILE)
    results = _corpus_results(run_dir, model)
    findings = {f.finding_id: f for r in results if r.study is not None for f in r.study.findings}
    studies = {
        r.pmid: (r.study.design, r.study.sample_size) for r in results if r.study is not None
    }
    if split is Split.CORPUS:
        designs = {pmid: design for pmid, (design, _) in studies.items()}
        pairs = pair_sample.drawn_pairs(_candidates(run_dir), designs)
    else:
        pairs = sample.eval if split is Split.EVAL else sample.dev
    mapping = _load_mapping(mappings_dir / f"{config.id}.toml")
    # The drawn eval and dev pairs were recorded before the mapping had polarity overrides, so
    # they are classified as drawn: their recordings and the label set stand.
    items = classify.pair_inputs(pairs, findings, studies, mapping, overrides=split is Split.CORPUS)
    guide = (gold_dir / "LABELING.md").read_text()
    replay_dir = classification_replay(split, arm, replay_root / config.id, run_dir)
    prices = load_prices(prices_file)
    k = k if k is not None else arms.SELF_CONSISTENCY_K
    if dry_run and arm in arms.MULTI_ARMS:
        if arm is models.Arm.SELF_CONSISTENCY and k is None:
            k = arms.provisional_k(items, config, guide)
            typer.echo(f"k = {k}, provisional: council-ranked's typical tokens, not yet measured")
        typer.echo(
            arms.describe(arms.estimate_arm(items, arm, config, guide, replay_dir, prices, k))
        )
        return
    if dry_run:
        estimate = classify.estimate(items, config, guide, arm, replay_dir, prices)
        typer.echo(classify.describe_estimate(estimate))
        dev_items = classify.pair_inputs(sample.dev, findings, studies, mapping, overrides=False)
        dev_replay = classification_replay(Split.DEV, arm, replay_root / config.id, run_dir)
        dev_out = run_dir / "classify" / f"{Split.DEV.value}-{arm.value}.jsonl"
        # The dev run measures the current prompt only when every dev request is recorded.
        current = classify.estimate(dev_items, config, guide, arm, dev_replay, prices).pairs == 0
        if current and dev_out.exists():
            dev_run = [
                classify.ClassifyResult.model_validate_json(line)
                for line in dev_out.read_text().splitlines()
            ]
            typer.echo(classify.describe_measured(dev_run, estimate.pairs, prices))
        else:
            typer.echo(f"no dev run of {arm.value} under the current prompt to measure against")
        return
    try:
        run_one = arms.attempt(arm, config, guide, k)
    except ValueError as error:
        typer.echo(f"{error}; pass --k", err=True)
        raise typer.Exit(1) from error
    try:
        send = (
            vendors.vendor_batches(os.environ, arms.vendors(arm), on_poll=typer.echo)
            if live
            else None
        )
    except RuntimeError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    run = batch.run_batched(
        items,
        lambda item: item.pair.pair_id,
        run_one,
        replay_dir,
        send,
        max_rounds=arms.ROUNDS.get(arm, batch.MAX_ROUNDS),
    )
    out = run_dir / "classify" / f"{split.value}-{arm.value}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(out, "".join(r.model_dump_json() + "\n" for r in run.results).encode())
    typer.echo(f"{split.value}: {len(items)} pairs, {arm.value}")
    for line in classify.summary_lines(run.results, prices):
        typer.echo(line)
    typer.echo(f"wrote {out}")
    if run.missing:
        typer.echo(
            f"{len(run.missing)} pairs not recorded; record them with --live after a dry run",
            err=True,
        )
        raise typer.Exit(1)


@contradiction_app.command("label-set")
def contradiction_label_set(
    question: Annotated[str, typer.Argument(help="A question id, or the path to its config.")],
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    mappings_dir: Annotated[
        Path, typer.Option(help="Where the outcome mappings are read from.")
    ] = Path("mappings"),
    gold_dir: GoldDir = Path("evals/gold"),
    replay_root: Annotated[
        Path, typer.Option(help="Where the committed eval recordings live.")
    ] = Path("replay"),
    model: Model = extract.DEFAULT_MODEL,
) -> None:
    """Choose the eval pairs to label from every arm's recorded answers, as pre-registered.

    Replays each arm on the eval pairs, sends nothing, and writes the label set beside the
    draw: the disagreement pairs drawn by strata up to the cap, and the agreement pairs in a
    seeded order. The labeling page then shows only those pairs.
    """
    config = load_config(resolve_question(question))
    run_dir = runs_dir / config.id
    out_dir = gold_dir / config.id
    sample = pair_sample.read_sample(out_dir / pair_sample.SAMPLE_FILE)
    results = _corpus_results(run_dir, model)
    findings = {f.finding_id: f for r in results if r.study is not None for f in r.study.findings}
    studies = {
        r.pmid: (r.study.design, r.study.sample_size) for r in results if r.study is not None
    }
    mapping = _load_mapping(mappings_dir / f"{config.id}.toml")
    items = classify.pair_inputs(sample.eval, findings, studies, mapping, overrides=False)
    guide = (gold_dir / "LABELING.md").read_text()
    answers: dict[models.Arm, dict[str, models.PairClassification]] = {}
    for arm in models.Arm:
        replay_dir = classification_replay(Split.EVAL, arm, replay_root / config.id, run_dir)
        run = batch.run_batched(
            items,
            lambda item: item.pair.pair_id,
            arms.attempt(arm, config, guide, arms.SELF_CONSISTENCY_K),
            replay_dir,
            None,
            max_rounds=arms.ROUNDS.get(arm, batch.MAX_ROUNDS),
        )
        if run.missing:
            typer.echo(f"{arm.value}: {len(run.missing)} eval pairs not recorded", err=True)
            raise typer.Exit(1)
        unanswered = [r.pair_id for r in run.results if r.classification is None]
        if unanswered:
            typer.echo(f"{arm.value}: no valid answer for {unanswered[0]}", err=True)
            raise typer.Exit(1)
        answers[arm] = {r.pair_id: r.classification for r in run.results if r.classification}
    found = label_set.build(sample, answers, sample.seed)
    path = out_dir / label_set.LABEL_SET_FILE
    label_set.write(path, found)
    typer.echo(
        f"{sum(found.disagreement_strata.values())} disagreement pairs in "
        f"{len(found.disagreement_strata)} strata, {len(found.disagreement)} drawn; "
        f"{len(found.agreement_order)} agreement pairs, the first {found.agreement_labeled} "
        f"to label: {len(found.pair_ids)} pairs"
    )
    typer.echo(f"wrote {path}")
