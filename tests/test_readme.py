"""The README's generated blocks: each one's markers appear exactly once, start before end."""

import re
from pathlib import Path

from claimgraph import contradiction_eval, costs, normalization_eval
from claimgraph.evaluation import EXTRACTION_BLOCK, MODELS_BLOCK, RERUN_BLOCK
from claimgraph.relabel import AGREEMENT_BLOCK, replace_block

ROOT = Path(__file__).parents[1]
README = (ROOT / "README.md").read_text()

# Every block a command writes with --readme. The question contrast is written by its own
# report; its markers are placed before it lands.
BLOCKS = {
    AGREEMENT_BLOCK,
    EXTRACTION_BLOCK,
    RERUN_BLOCK,
    MODELS_BLOCK,
    normalization_eval.README_BLOCK,
    contradiction_eval.README_BLOCK,
    "question-contrast",
    costs.README_BLOCK,
}
MARKER = re.compile(r"<!-- ([\w-]+):(start|end) -->")


def test_every_generated_block_has_its_markers() -> None:
    named = {name for name, _ in MARKER.findall(README)}

    assert named == BLOCKS


def test_each_marker_appears_exactly_once_and_in_order() -> None:
    for name in BLOCKS:
        start, end = f"<!-- {name}:start -->\n", f"<!-- {name}:end -->"

        assert README.count(f"<!-- {name}:start -->") == 1, name
        assert README.count(end) == 1, name
        assert README.count(start) == 1, f"{name}: the start marker ends its own line"
        assert README.index(start) < README.index(end), name


def test_every_block_can_be_rewritten_in_place() -> None:
    # The --readme commands replace a block whole; replacing one with itself changes nothing.
    for name in BLOCKS:
        start, end = f"<!-- {name}:start -->\n", f"<!-- {name}:end -->"
        content = README.split(start, 1)[1].split(end, 1)[0]

        assert replace_block(README, name, content) == README
