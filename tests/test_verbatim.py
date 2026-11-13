"""The verbatim check: a quote counts only if the abstract holds it, spacing and marks aside."""

import pytest
from test_labels import synthetic_abstract

from claimgraph.retrieve import Abstract, AbstractSection
from claimgraph.verbatim import normalize_quote, quote_in

OPEN, CLOSE = "\N{LEFT DOUBLE QUOTATION MARK}", "\N{RIGHT DOUBLE QUOTATION MARK}"
OPEN_1, CLOSE_1 = "\N{LEFT SINGLE QUOTATION MARK}", "\N{RIGHT SINGLE QUOTATION MARK}"
NBSP, THIN = "\N{NO-BREAK SPACE}", "\N{THIN SPACE}"


def abstract(*texts: str) -> Abstract:
    sections = [AbstractSection(label=None, category=None, text=t) for t in texts]
    return synthetic_abstract().model_copy(update={"sections": sections})


def sentence(marks: tuple[str, str] = ('"', '"'), *, gap: str = " ", p: str = "p = 0.02") -> str:
    return (
        f"Participants rated the {marks[0]}synthetic mood{marks[1]} item{gap}higher after 8 "
        f"weeks ({p})."
    )


# As PubMed might give it: curly marks, a line break and odd spaces.
SOURCE = abstract(
    "Synthetic adults took a synthetic supplement.",
    sentence((OPEN, CLOSE), gap="\n", p=f"p{THIN}={THIN}0.02").replace("8 ", f"8{NBSP}"),
)


@pytest.mark.parametrize(
    "quote",
    [
        sentence(),
        sentence((OPEN, CLOSE)),
        sentence((OPEN, CLOSE), gap="\n", p=f"p{THIN}={THIN}0.02").replace("8 ", f"8{NBSP}"),
        f"  {sentence(gap='   ')}\n\n ",
    ],
    ids=["straight-marks", "curly-marks", "line-break-and-odd-spaces", "extra-spacing"],
)
def test_spacing_and_quote_marks_do_not_count(quote: str) -> None:
    assert quote_in(quote, SOURCE)


@pytest.mark.parametrize(
    "quote",
    [
        sentence(("", "")),
        sentence().replace("8 weeks", "eight weeks"),
        sentence(p="p < 0.05"),
        "Synthetic mood improved.",
        sentence(("'", "'")),
        "",
    ],
    ids=["dropped-marks", "reworded", "changed-number", "paraphrase", "single-for-double", "empty"],
)
def test_any_other_difference_is_a_miss(quote: str) -> None:
    assert not quote_in(quote, SOURCE)


def test_the_title_is_not_a_source() -> None:
    assert not quote_in(SOURCE.title, SOURCE)


def test_normalization_is_shared_with_the_labels() -> None:
    from claimgraph import labels

    assert labels.normalize_quote is normalize_quote
    assert normalize_quote(f"{OPEN}A{CLOSE}\n  {OPEN_1}b{CLOSE_1}") == "\"A\" 'b'"
