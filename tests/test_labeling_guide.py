"""The contradiction guide's examples are invented: none may echo a gold abstract, since the
guide's rules will be shown to the classifier and gold abstracts never appear as prompt
examples."""

import json
import re
from pathlib import Path

from claimgraph.kind_review import guide_section
from claimgraph.models import Reason

ROOT = Path(__file__).parents[1]
GUIDE = ROOT / "evals" / "gold" / "LABELING.md"
GOLD = ROOT / "evals" / "gold" / "vitamin-d-depression" / "gold.jsonl"
# Five words in a row is long enough to be a borrowed phrase, short of everyday wording.
RUN = 5


def section() -> str:
    return guide_section(GUIDE.read_text(), "Contradiction pairs")


def examples() -> dict[str, str]:
    found = re.findall(
        r"^- `(\w+)`, (?:genuine|apparent)\. \*Example: (.+?)\*", section(), flags=re.M | re.S
    )
    return {reason: " ".join(text.split()) for reason, text in found}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def runs(text: str) -> set[tuple[str, ...]]:
    w = words(text)
    return {tuple(w[i : i + RUN]) for i in range(len(w) - RUN + 1)}


def test_every_reason_has_one_invented_example() -> None:
    assert sorted(examples()) == sorted(r.value for r in Reason)


def test_no_example_borrows_a_phrase_from_a_gold_abstract() -> None:
    gold: set[tuple[str, ...]] = set()
    for line in GOLD.read_text().splitlines():
        record = json.loads(line)
        gold |= runs(" ".join(s["text"] for s in record["abstract"]["sections"]))

    borrowed = {r: sorted(runs(t) & gold) for r, t in examples().items()}

    assert {r: b for r, b in borrowed.items() if b} == {}


def test_only_the_verdict_and_the_primary_reason_are_required() -> None:
    assert "Two fields are required" in section()
    assert "`also_differs`, `detail` and `note` are optional and never scored" in section()
