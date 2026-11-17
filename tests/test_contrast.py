"""The README contrast between questions: the table from run outputs and dry-run estimates.

The runs are the synthetic creatine fixtures, through compare; every number in a cell comes
from them or from the estimate code classify's dry run uses.
"""

import json
from pathlib import Path

import pytest
from test_creatine_demo import CONFIG, QUESTION, ROOT, Demo, make_demo, response, through

from claimgraph import arms, classify, contrast, pair_sample
from claimgraph.compare import Candidates
from claimgraph.contrast import DepthCost, QuestionRun
from claimgraph.extract import ExtractionResult
from claimgraph.llm import ModelClient
from claimgraph.models import Arm
from claimgraph.normalize import OutcomeMapping, load_mapping
from claimgraph.pair_sample import PairSample
from claimgraph.prices import Prices, load_prices

GUIDE = (ROOT / "evals" / "gold" / "LABELING.md").read_text()
SMALL = Arm.SINGLE_SMALL


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Demo:
    found = make_demo(tmp_path, monkeypatch)
    through(found, "compare")
    return found


def mapping(demo: Demo) -> OutcomeMapping:
    return load_mapping(demo.mappings / f"{QUESTION}.toml")


def prices() -> Prices:
    return load_prices(ROOT / "prices.toml")


def items(demo: Demo) -> list[classify.PairInput]:
    """The candidate pairs as classify's corpus split builds them."""
    run_dir = demo.run_dir
    lines = (run_dir / "extract" / "corpus-sonnet.jsonl").read_text().splitlines()
    results = [ExtractionResult.model_validate_json(line) for line in lines]
    found = Candidates.model_validate_json((run_dir / "compare" / "candidates.json").read_text())
    findings = {f.finding_id: f for r in results if r.study is not None for f in r.study.findings}
    studies = {
        r.pmid: (r.study.design, r.study.sample_size) for r in results if r.study is not None
    }
    pairs = pair_sample.drawn_pairs(found, {p: d for p, (d, _) in studies.items()})
    return classify.pair_inputs(pairs, findings, studies, mapping(demo))


ANSWER = classify.ClassifierOutput(
    rationale="Synthetic: the populations differ.",
    verdict=classify.Verdict.GENUINE,
    reason=classify.Reason.POPULATION,
    also_differs=[],
    detail="synthetic: older adults vs young adults",
).model_dump_json()


def classify_run(demo: Demo, split: str, guide: str = GUIDE) -> list[classify.ClassifyResult]:
    """Record an answer for every candidate pair under `guide` and classify them from it, as a
    run of the small arm on `split` would leave its file."""
    replay = demo.run_dir / "replay" / "classification" / SMALL.value
    client = ModelClient(replay)
    run: list[classify.ClassifyResult] = []
    for item in items(demo):
        client.record(classify.request(item, CONFIG, guide, SMALL), response(ANSWER))
        run.append(classify.classify_pair(item, CONFIG, guide, client, SMALL))
    out = demo.run_dir / "classify" / f"{split}-{SMALL.value}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(r.model_dump_json() + "\n" for r in run))
    return run


def dev_sample(demo: Demo) -> PairSample:
    return PairSample(question=QUESTION, seed=1, eval=[], dev=[item.pair for item in items(demo)])


def column(demo: Demo, sample: PairSample | None = None, depths: tuple[Arm, ...] = (SMALL,)):
    return contrast.question_run(
        CONFIG, demo.run_dir, mapping(demo), GUIDE, prices(), depths=depths, sample=sample
    )


# One question's column


def test_counts_come_from_the_run(demo: Demo) -> None:
    found = column(demo)

    assert (found.abstracts, found.studies, found.findings) == (5, 4, 7)
    assert (found.outcomes, found.pairs) == (3, 3)
    assert not found.mapping_reviewed
    manifest = json.loads((demo.run_dir / "extract" / "manifest.json").read_text())
    assert found.extraction_usd == manifest["runs"]["corpus-sonnet"]["cost_usd"]


def test_each_depth_is_the_dry_run_estimate_for_every_pair(demo: Demo, tmp_path: Path) -> None:
    found = column(demo, depths=(SMALL, Arm.COUNCIL_RANKED))

    small = classify.estimate(items(demo), CONFIG, GUIDE, SMALL, tmp_path / "none", prices())
    assert (found.depths[0].typical_usd, found.depths[0].ceiling_usd) == (
        small.typical_usd,
        small.ceiling_usd,
    )
    ranked = arms.estimate_arm(
        items(demo), Arm.COUNCIL_RANKED, CONFIG, GUIDE, tmp_path / "none", prices(), None
    )
    assert found.depths[1].typical_usd == ranked.typical_usd
    assert found.depths[1].ceiling_usd == ranked.ceiling_usd


def test_recorded_pairs_still_count_in_the_estimate(demo: Demo) -> None:
    before = column(demo).depths[0]
    classify_run(demo, "corpus")

    after = column(demo).depths[0]

    assert (after.typical_usd, after.ceiling_usd) == (before.typical_usd, before.ceiling_usd)


def test_a_corpus_run_is_measured(demo: Demo) -> None:
    run = classify_run(demo, "corpus")

    cost = column(demo).depths[0]

    per_pair = classify.measured_per_pair(run, prices())
    assert per_pair > 0
    assert cost.measured_usd == per_pair * 3
    assert (cost.measured_pairs, cost.measured_split) == (3, "corpus")


def test_a_current_dev_run_is_scaled_to_every_pair(demo: Demo) -> None:
    run = classify_run(demo, "dev")

    cost = column(demo, dev_sample(demo)).depths[0]

    assert cost.measured_usd == classify.measured_per_pair(run, prices()) * 3
    assert (cost.measured_pairs, cost.measured_split) == (3, "dev")


def test_a_dev_run_under_another_prompt_is_not_measured(demo: Demo) -> None:
    classify_run(demo, "dev", guide=GUIDE + "\nSynthetic change to the rules.\n")

    assert column(demo, dev_sample(demo)).depths[0].measured_usd is None


def test_without_a_sample_a_dev_run_is_not_measured(demo: Demo) -> None:
    classify_run(demo, "dev")

    assert column(demo).depths[0].measured_usd is None


# The table


def run_column(
    question: str, reviewed: bool, measured: float | None, pairs: int = 27_297
) -> QuestionRun:
    return QuestionRun(
        question=question,
        title=f"Synthetic question {question}?",
        abstracts=1_901,
        studies=443,
        findings=815,
        outcomes=4,
        pairs=pairs,
        mapping_reviewed=reviewed,
        extraction_usd=5.26323115 if reviewed else None,
        k=6,
        depths=[
            DepthCost(
                arm=SMALL,
                typical_usd=5.7324,
                ceiling_usd=38.7649,
                measured_usd=measured,
                measured_pairs=40 if measured is not None else 0,
                measured_split="dev" if measured is not None else "",
            ),
            DepthCost(arm=Arm.SELF_CONSISTENCY, typical_usd=0.000591, ceiling_usd=0.0042),
        ],
    )


def test_the_table_has_one_column_per_question() -> None:
    runs = [run_column("synthetic-a", True, 2.6137), run_column("synthetic-b", False, None, 3)]

    text = contrast.table(runs, prices())

    assert text == (
        "|  | `synthetic-a` | `synthetic-b` |\n"
        "| --- | ---: | ---: |\n"
        "| Question | Synthetic question synthetic-a? | Synthetic question synthetic-b? |\n"
        "| Abstracts kept | 1,901 | 1,901 |\n"
        "| Studies with a headline finding | 443 | 443 |\n"
        "| Findings extracted | 815 | 815 |\n"
        "| Outcomes | 4 | 4 |\n"
        "| Candidate pairs | 27,297 | 3 |\n"
        "| Outcome mapping | reviewed | unreviewed |\n"
        "| Extraction, measured | $5.26 | n/a |\n"
        "| `single-small`, estimated | $5.73 (at most $38.76) | $5.73 (at most $38.76) |\n"
        "| `single-small`, measured | $2.61 (from 40 dev pairs) | n/a |\n"
        "| `self-consistency` (k = 6), estimated | $0.00059 (at most $0.0042) "
        "| $0.00059 (at most $0.0042) |\n"
        "\n"
        "Each depth's estimate is the classify dry run's for every candidate pair, as if none "
        "were recorded: typical output and no cache reads at batch prices, with the most it "
        "could cost in brackets. A measured row scales a current run's spend per pair, cache "
        f"reads included, to every candidate pair. Prices checked {prices().checked}.\n"
        "\n"
        "Unreviewed outcome mapping: `synthetic-b`. Some of its outcomes, polarity overrides or "
        "exclusions are the model's or the reviewer's proposal, not yet confirmed, so its "
        "counts can change; the CLI demo runs on it, and the hosted build refuses it.\n"
    )


def test_a_measured_row_shows_only_when_a_question_has_one() -> None:
    text = contrast.table([run_column("synthetic-a", True, None)], prices())

    assert "measured |" not in text.replace("Extraction, measured |", "")
    assert "Unreviewed" not in text


@pytest.mark.parametrize(
    ("value", "shown"),
    [(0, "$0"), (5.26323, "$5.26"), (1234.5, "$1,234.50"), (0.31, "$0.31"), (0.000591, "$0.00059")],
)
def test_dollars_keep_two_significant_figures_below_a_dollar(value: float, shown: str) -> None:
    assert contrast.usd(value) == shown


def test_the_table_needs_the_same_depths_for_every_question() -> None:
    other = run_column("synthetic-b", True, None)
    other = other.model_copy(update={"depths": other.depths[:1]})

    with pytest.raises(ValueError, match="same depths"):
        contrast.table([run_column("synthetic-a", True, None), other], prices())
    with pytest.raises(ValueError, match="no questions"):
        contrast.table([], prices())
