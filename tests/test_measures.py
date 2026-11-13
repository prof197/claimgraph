"""Scoring the outcome field: do two outcome strings name the same measure?"""

from pathlib import Path

import pytest

from claimgraph.labels import read_gold
from claimgraph.measures import abbreviations, abstract_text, same_measure

GOLD = Path(__file__).parents[1] / "evals" / "gold" / "vitamin-d-depression"


def gold_text(pmid: str) -> str:
    (entry,) = [g for g in read_gold(GOLD) if g.abstract.pmid == pmid]
    return abstract_text(entry.abstract)


# The three examples agreed before this was built, on gold strings and invented model strings.
@pytest.mark.parametrize(
    ("pmid", "gold", "model", "same"),
    [
        pytest.param("25467385", "PHQ-2", "depression risk (PHQ-2)", True, id="shared instrument"),
        pytest.param(
            "36368945",
            "depression severity (BDI-II)",
            "Beck Depression Inventory-II score",
            True,
            id="full name the abstract defines",
        ),
        pytest.param("32945627", "HAMD-17", "anhedonia (RSAS)", False, id="other instrument"),
    ],
)
def test_the_agreed_examples(pmid: str, gold: str, model: str, same: bool) -> None:
    assert same_measure(gold, model, gold_text(pmid)) is same


def test_abbreviations_come_from_the_abstract() -> None:
    found = abbreviations(gold_text("32945627"))

    assert found["HAMD-17"] == "Hamilton Depression Rating Scale-17"
    assert found["RSAS"] == "Revised Social Anhedonia Scale"


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        pytest.param("PHQ-2", "PHQ-9", False, id="versions count"),
        pytest.param("BDI", "BDI-II", False, id="editions count"),
        pytest.param("CES-D score", "CESD scores", True, id="hyphens and filler ignored"),
        pytest.param("MDD (MINI)", "MDD", True, id="one shared name is enough"),
        pytest.param("depression risk", "PHQ-2", False, id="instrument on one side only"),
        pytest.param("incident depression", "depression incidence", True, id="order and endings"),
        pytest.param("depressive symptoms", "symptoms of depression", True, id="filler words"),
        pytest.param("depression severity", "depressive symptoms", False, id="other words"),
        pytest.param(
            "Edinburgh Postnatal Depression scores",
            "Edinburgh Postnatal Depression scale",
            True,
            id="score or scale, one measure",
        ),
    ],
)
def test_synthetic_pairs(a: str, b: str, same: bool) -> None:
    assert same_measure(a, b) is same


def test_a_full_name_counts_only_where_the_abstract_defines_it() -> None:
    assert not same_measure("BDI-II", "Beck Depression Inventory-II score")
    assert same_measure(
        "BDI-II",
        "Beck Depression Inventory-II score",
        "Symptoms were rated on the Beck Depression Inventory-II (BDI-II) at baseline.",
    )
