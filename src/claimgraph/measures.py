"""Whether two outcome strings name the same measure, for scoring the outcome field.

The extraction eval and the second labeling pass score `outcome_reported` with this rather than
by exact string, so wording alone is never counted wrong. Two strings name the same measure when
they share an instrument, or, when neither names one, when they have the same content words.
"""

import re

from claimgraph.retrieve import Abstract

# Words that say how a measure is reported rather than which measure it is.
_FILLER = frozenset(
    {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}
    | {"score", "scores", "level", "levels", "value", "values"}
)
# Word endings dropped so that "depression" and "depressive", or "incident" and "incidence",
# compare equal. Longest first, and only while four letters remain.
_ENDINGS = ("ence", "ions", "ives", "ent", "ion", "ive", "ing", "ed", "es", "s")
_TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
_PARENTHESIS = re.compile(r"\(([^()]{1,20})\)")


def abstract_text(abstract: Abstract) -> str:
    return " ".join(s.text for s in abstract.sections)


def _is_abbreviation(token: str) -> bool:
    return sum(c.isupper() for c in token) >= 2


def _key(abbreviation: str) -> str:
    # Case and hyphens aside, a version or edition is part of the name: PHQ-2 is not PHQ-9.
    return abbreviation.replace("-", "").upper()


def _plain(text: str) -> str:
    return " ".join(text.replace("-", " ").casefold().split())


def _long_form(short: str, before: str) -> str | None:
    """The words before an abbreviation that spell it, by Schwartz and Hearst's algorithm."""
    letters = [c.casefold() for c in short if c.isalnum()]
    words = before.split()
    candidate = " ".join(words[-min(len(letters) + 5, len(letters) * 2) :])
    i = len(candidate) - 1
    for n in range(len(letters) - 1, -1, -1):
        # The first letter must start a word; the others may fall anywhere in one.
        while i >= 0 and (
            candidate[i].casefold() != letters[n]
            or (n == 0 and i > 0 and candidate[i - 1].isalnum())
        ):
            i -= 1
        if i < 0:
            return None
        if n > 0:
            i -= 1
    found = candidate[i:].strip()
    return found if " " in found else None


def abbreviations(text: str) -> dict[str, str]:
    """Abbreviations the text defines, as "Full Name (ABBR)", with their full names."""
    found: dict[str, str] = {}
    for match in _PARENTHESIS.finditer(text):
        short = match.group(1).strip()
        if not _is_abbreviation(short) or " " in short:
            continue
        # "the scale (Full Name) (ABBR)" spells the name inside the earlier brackets.
        before = text[: match.start()].rstrip().rstrip(")").replace("(", " ")
        long = _long_form(short, before)
        if long is not None:
            found.setdefault(short, long)
    return found


def _instruments(outcome: str, defined: dict[str, str]) -> set[str]:
    named = {_key(t) for t in _TOKEN.findall(outcome) if _is_abbreviation(t)}
    plain = _plain(outcome)
    return named | {_key(short) for short, long in defined.items() if _plain(long) in plain}


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def _content(outcome: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", outcome.casefold())
    return {_stem(w) for w in words if w not in _FILLER}


def same_measure(a: str, b: str, abstract: str = "") -> bool:
    """Whether two outcome strings name the same measure, reading abbreviations the abstract
    defines. Strings that name an instrument match when they share one; strings that name none
    match when their content words do, ignoring order, endings and filler such as "score"."""
    defined = abbreviations(abstract)
    named_a, named_b = _instruments(a, defined), _instruments(b, defined)
    if named_a or named_b:
        return bool(named_a & named_b)
    words_a, words_b = _content(a), _content(b)
    return words_a == words_b if words_a or words_b else _plain(a) == _plain(b)
