"""The README's contrast between questions: what each run found, and what classifying every
candidate pair would cost at each depth.

Every cell comes from a run's outputs or from the classify dry run's own estimates, so nothing
is typed by hand and a rerun on the same outputs writes the same table. Estimating the deeper
arms walks every pair through each step of the arm, so on a corpus of tens of thousands of
pairs the command takes minutes.
"""

import json
from collections.abc import Sequence
from math import floor, log10
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated

import typer
from pydantic import ValidationError

from claimgraph import arms, classify, extract, gold, pair_sample, paths, relabel
from claimgraph.compare import Candidates
from claimgraph.config import QuestionConfig, load_config, resolve_question
from claimgraph.extract import ExtractionResult, ModelChoice
from claimgraph.models import Arm, Design, Finding, Record
from claimgraph.normalize import OutcomeMapping, load_mapping
from claimgraph.pair_sample import PairSample
from claimgraph.prices import PRICES_FILE, Prices, load_prices

README_BLOCK = "question-contrast"


class DepthCost(Record):
    arm: Arm
    # The classify dry run's estimate for every candidate pair, as if none were recorded:
    # typical output and no cache reads, and the most it could cost, at batch prices.
    typical_usd: float
    ceiling_usd: float
    # A current run's measured spend per pair, scaled to every candidate pair; None without one.
    measured_usd: float | None = None
    # The pairs that run measured, and which split they came from.
    measured_pairs: int = 0
    measured_split: str = ""


class QuestionRun(Record):
    question: str
    title: str
    abstracts: int
    # Studies with a headline finding on some outcome: the ones the pairs are made from.
    studies: int
    findings: int
    outcomes: int
    pairs: int
    mapping_reviewed: bool
    # The corpus extraction's measured spend at batch prices, from its manifest entry.
    extraction_usd: float | None
    # The self-consistency arm's samples per pair, when it is estimated.
    k: int | None
    depths: list[DepthCost]


def _results(run_dir: Path, model: ModelChoice) -> list[ExtractionResult]:
    source = run_dir / "extract" / f"corpus-{model.value}.jsonl"
    return [ExtractionResult.model_validate_json(line) for line in source.read_text().splitlines()]


def _extraction_usd(run_dir: Path, model: ModelChoice) -> float | None:
    path = run_dir / "extract" / "manifest.json"
    if not path.exists():
        return None
    entry = json.loads(path.read_text())["runs"].get(f"corpus-{model.value}")
    return None if entry is None else float(entry["cost_usd"])


def _classified(path: Path) -> list[classify.ClassifyResult]:
    return [
        classify.ClassifyResult.model_validate_json(line) for line in path.read_text().splitlines()
    ]


def _estimate(
    items: Sequence[classify.PairInput],
    arm: Arm,
    config: QuestionConfig,
    guide: str,
    replay_dir: Path,
    prices: Prices,
    k: int | None,
) -> tuple[int, float, float]:
    """Unrecorded pairs, typical and ceiling dollars: the dry run's own estimate for the arm."""
    if arm in classify.SINGLE_ARMS:
        single = classify.estimate(items, config, guide, arm, replay_dir, prices)
        return single.pairs, single.typical_usd, single.ceiling_usd
    multi = arms.estimate_arm(items, arm, config, guide, replay_dir, prices, k)
    return multi.pairs, multi.typical_usd, multi.ceiling_usd


def question_run(
    config: QuestionConfig,
    run_dir: Path,
    mapping: OutcomeMapping,
    guide: str,
    prices: Prices,
    model: ModelChoice = extract.DEFAULT_MODEL,
    depths: Sequence[Arm] = tuple(Arm),
    sample: PairSample | None = None,
) -> QuestionRun:
    """One question's column, from its run directory and mapping; sends nothing.

    `sample` is the question's drawn contradiction pairs, whose dev pairs a current dev run
    measured; a question without one has only a corpus run to measure from.
    """
    abstracts = gold.read_abstracts(run_dir / "abstracts.jsonl")
    results = _results(run_dir, model)
    found = Candidates.model_validate_json((run_dir / "compare" / "candidates.json").read_text())
    findings: dict[str, Finding] = {
        f.finding_id: f for r in results if r.study is not None for f in r.study.findings
    }
    studies: dict[str, tuple[Design, int | None]] = {
        r.pmid: (r.study.design, r.study.sample_size) for r in results if r.study is not None
    }
    designs = {pmid: design for pmid, (design, _) in studies.items()}
    pairs = pair_sample.drawn_pairs(found, designs)
    items = classify.pair_inputs(pairs, findings, studies, mapping)
    k = arms.SELF_CONSISTENCY_K
    if k is None and Arm.SELF_CONSISTENCY in depths:
        k = arms.provisional_k(items, config, guide)
    costs: list[DepthCost] = []
    with TemporaryDirectory() as empty:
        for arm in depths:
            _, typical, ceiling = _estimate(items, arm, config, guide, Path(empty), prices, k)
            cost = DepthCost(arm=arm, typical_usd=typical, ceiling_usd=ceiling)
            measured = _measured(
                arm, items, sample, findings, studies, mapping, run_dir, config, guide, prices, k
            )
            if measured is not None:
                split, run = measured
                per_pair = classify.measured_per_pair(run, prices)
                cost = cost.model_copy(
                    update={
                        "measured_usd": per_pair * len(items),
                        "measured_pairs": len(run),
                        "measured_split": split,
                    }
                )
            costs.append(cost)
    return QuestionRun(
        question=config.id,
        title=config.title,
        abstracts=len(abstracts),
        studies=len({h.study for h in found.headlines}),
        findings=len(findings),
        outcomes=len(found.blocks),
        pairs=len(found.pairs),
        mapping_reviewed=mapping.reviewed,
        extraction_usd=_extraction_usd(run_dir, model),
        k=k if Arm.SELF_CONSISTENCY in depths else None,
        depths=costs,
    )


def _measured(
    arm: Arm,
    corpus_items: Sequence[classify.PairInput],
    sample: PairSample | None,
    findings: dict[str, Finding],
    studies: dict[str, tuple[Design, int | None]],
    mapping: OutcomeMapping,
    run_dir: Path,
    config: QuestionConfig,
    guide: str,
    prices: Prices,
    k: int | None,
) -> tuple[str, list[classify.ClassifyResult]] | None:
    """The arm's run to measure from: the corpus run, else the dev run, and only one whose every
    request is recorded under the current prompt, as the dry run measures."""
    # Dev and corpus recordings share the run's replay directory, as classify keeps them.
    replay_dir = run_dir / "replay" / "classification" / arm.value
    candidates: list[tuple[str, Sequence[classify.PairInput]]] = [("corpus", corpus_items)]
    if sample is not None:
        # Classified as drawn, before the mapping had polarity overrides.
        dev = classify.pair_inputs(sample.dev, findings, studies, mapping, overrides=False)
        candidates.append(("dev", dev))
    for split, items in candidates:
        path = run_dir / "classify" / f"{split}-{arm.value}.jsonl"
        if not path.exists():
            continue
        run = _classified(path)
        ran = {r.pair_id for r in run}
        covered = [i for i in items if i.pair.pair_id in ran]
        # A run of pairs the split no longer holds measures nothing current.
        current = len(covered) == len(run) > 0
        if current and _estimate(covered, arm, config, guide, replay_dir, prices, k)[0] == 0:
            return split, run
    return None


# The table


def _count(n: int) -> str:
    return f"{n:,}"


def usd(value: float) -> str:
    """Cents for a dollar or more; two significant figures below, so small runs still show."""
    if value == 0:
        return "$0"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.{max(2, 1 - floor(log10(value)))}f}"


def _arm_name(arm: Arm, k: int | None) -> str:
    return f"`{arm.value}` (k = {k})" if arm is Arm.SELF_CONSISTENCY and k else f"`{arm.value}`"


def table(runs: Sequence[QuestionRun], prices: Prices) -> str:
    """The markdown block: one column per question, the run's counts and each depth's cost."""
    if not runs:
        raise ValueError("no questions to contrast")
    depths = [d.arm for d in runs[0].depths]
    if any([d.arm for d in r.depths] != depths for r in runs):
        raise ValueError("every question must be estimated at the same depths")
    k = next((r.k for r in runs if r.k is not None), None)

    def row(name: str, cells: Sequence[str]) -> str:
        return f"| {name} | " + " | ".join(cells) + " |"

    lines = [
        row("", [f"`{r.question}`" for r in runs]),
        "| --- |" + " ---: |" * len(runs),
        row("Question", [r.title for r in runs]),
        row("Abstracts kept", [_count(r.abstracts) for r in runs]),
        row("Studies with a headline finding", [_count(r.studies) for r in runs]),
        row("Findings extracted", [_count(r.findings) for r in runs]),
        row("Outcomes", [_count(r.outcomes) for r in runs]),
        row("Candidate pairs", [_count(r.pairs) for r in runs]),
        row("Outcome mapping", ["reviewed" if r.mapping_reviewed else "unreviewed" for r in runs]),
        row(
            "Extraction, measured",
            ["n/a" if r.extraction_usd is None else usd(r.extraction_usd) for r in runs],
        ),
    ]
    for i, arm in enumerate(depths):
        costs = [r.depths[i] for r in runs]
        lines.append(
            row(
                f"{_arm_name(arm, k)}, estimated",
                [f"{usd(c.typical_usd)} (at most {usd(c.ceiling_usd)})" for c in costs],
            )
        )
        if any(c.measured_usd is not None for c in costs):
            lines.append(
                row(
                    f"{_arm_name(arm, k)}, measured",
                    [
                        "n/a"
                        if c.measured_usd is None
                        else f"{usd(c.measured_usd)} (from {_count(c.measured_pairs)} "
                        f"{c.measured_split} pairs)"
                        for c in costs
                    ],
                )
            )
    unreviewed = [f"`{r.question}`" for r in runs if not r.mapping_reviewed]
    notes = [
        "",
        "Each depth's estimate is the classify dry run's for every candidate pair, as if none "
        "were recorded: typical output and no cache reads at batch prices, with the most it "
        "could cost in brackets. A measured row scales a current run's spend per pair, cache "
        f"reads included, to every candidate pair. Prices checked {prices.checked}.",
    ]
    if unreviewed:
        notes += [
            "",
            f"Unreviewed outcome mapping: {', '.join(unreviewed)}. Some of its outcomes, "
            "polarity overrides or exclusions are the model's or the reviewer's proposal, not "
            "yet confirmed, so its counts can change; the CLI demo runs on it, and the hosted "
            "build refuses it.",
        ]
    return "\n".join(lines + notes) + "\n"


# The command

RUNS_DIR = paths.runs_dir()


def contrast_command(
    questions: Annotated[
        list[str], typer.Argument(help="Question ids or config paths, one column each.")
    ],
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    mappings_dir: Annotated[
        Path, typer.Option(help="Where the outcome mappings are read from.")
    ] = Path("mappings"),
    gold_dir: Annotated[Path, typer.Option(help="Where the samples and labels live.")] = Path(
        "evals/gold"
    ),
    prices_file: Annotated[Path, typer.Option(help="The dated price file.")] = PRICES_FILE,
    model: Annotated[
        ModelChoice, typer.Option(help="The extraction model.")
    ] = extract.DEFAULT_MODEL,
    depth: Annotated[
        list[Arm] | None,
        typer.Option(help="Estimate only these depths; by default every arm."),
    ] = None,
    readme: Annotated[
        Path | None, typer.Option(help="Also write the table into this README's block.")
    ] = None,
) -> None:
    """Contrast questions' runs: counts from compare, and each depth's dry-run cost.

    Reads each run's extraction, candidate pairs and mapping, and sends nothing. Exits with
    status 1 if a question has no compare output yet.
    """
    prices = load_prices(prices_file)
    guide = (gold_dir / "LABELING.md").read_text()
    depths = tuple(depth) if depth else tuple(Arm)
    runs: list[QuestionRun] = []
    for question in questions:
        config = load_config(resolve_question(question))
        run_dir = runs_dir / config.id
        sample_path = gold_dir / config.id / pair_sample.SAMPLE_FILE
        try:
            mapping = load_mapping(mappings_dir / f"{config.id}.toml")
            sample = pair_sample.read_sample(sample_path) if sample_path.exists() else None
            runs.append(
                question_run(config, run_dir, mapping, guide, prices, model, depths, sample)
            )
        except (FileNotFoundError, ValidationError) as error:
            typer.echo(
                f"{config.id}: no complete run in {run_dir} ({error}); run every stage through "
                "compare first",
                err=True,
            )
            raise typer.Exit(1) from error
    block = table(runs, prices)
    if readme is not None:
        try:
            readme.write_text(relabel.replace_block(readme.read_text(), README_BLOCK, block))
        except ValueError as error:
            typer.echo(f"{readme}: {error}", err=True)
            raise typer.Exit(1) from error
    typer.echo(block, nl=False)
