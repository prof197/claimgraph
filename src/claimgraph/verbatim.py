"""The verbatim check: a quote counts only if its abstract holds it word for word.

Only whitespace and curly versus straight quote marks are forgiven, since PubMed text and model
output differ in those without changing a word. The page never shows a quote that failed it.
"""

from claimgraph.retrieve import Abstract

_DOUBLE = (
    "\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}\N{DOUBLE LOW-9 QUOTATION MARK}"
)
_SINGLE = "\N{LEFT SINGLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}"
_QUOTE_MARKS = str.maketrans(dict.fromkeys(_DOUBLE, '"') | dict.fromkeys(_SINGLE, "'"))


def normalize_quote(text: str) -> str:
    """Whitespace collapsed and curly quote marks straightened, as the verbatim check does."""
    return " ".join(text.translate(_QUOTE_MARKS).split())


def abstract_source(abstract: Abstract) -> str:
    """The abstract's sections, normalized: what a quote must be found in. Not the title."""
    return normalize_quote(" ".join(s.text for s in abstract.sections))


def quote_in(quote: str, abstract: Abstract) -> bool:
    quote = normalize_quote(quote)
    return bool(quote) and quote in abstract_source(abstract)
