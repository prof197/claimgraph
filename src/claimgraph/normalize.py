"""Normalize: group the outcome strings findings report into named outcomes.

Findings name their outcomes in the abstract's words, so one construct arrives under many names:
a general term, a score on a named scale, a scale with its cut-off. One model call proposes the
groups over every unique string; the result is written to the question's mapping file unreviewed,
and a person reviews it before anything is published. Comparisons happen only within one
outcome, so a wrong merge or split here changes which studies are compared.
"""

import json
import os
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from claimgraph.batch import CHARS_PER_TOKEN, RETRY_NOTE_TOKENS
from claimgraph.config import OUTSIDE_DOMAIN, QuestionConfig
from claimgraph.extract import DEFAULT_MODEL, SETTINGS, ExtractionResult, ModelChoice
from claimgraph.llm import (
    Mode,
    ModelClient,
    Reply,
    Usage,
    anthropic_transport,
    request_key,
    retry_request,
)
from claimgraph.models import NormalizedOutcome, Record
from claimgraph.prices import Prices, model_prices

# Room for one assignment per string, about 24 tokens each, for over 600 strings. The SDK
# refuses a non-streamed request whose max_tokens could run past ten minutes.
MAX_TOKENS = 16_000

# Typical output per string: its assignment, a number and an outcome name. Measured on the
# vitamin D proposal: 12,900 output tokens for 547 strings.
TYPICAL_TOKENS_PER_STRING = 24


def outcome_strings(results: Iterable[ExtractionResult]) -> list[str]:
    """Each kept finding's outcome string once, sorted, so the request is the same every run."""
    return sorted(
        {f.outcome_reported for r in results if r.study is not None for f in r.study.findings}
    )


# The request


def system_prompt(config: QuestionConfig) -> str:
    domain = "; ".join(config.outcome_domain)
    return f"""\
You group the outcome names that findings on one question report, so that findings on the same \
outcome can be compared with each other.

Question: {config.title}
The outcome domain, the outcomes the question is about: {domain}.

The user message lists outcome names, one per line, each with a number. First define the \
outcomes, then assign every name to one of them.

outcomes: each outcome once, with
- name: a short name for what it measures, in plain words, given to no other outcome;
- domain: the one entry of the outcome domain it measures, or "{OUTSIDE_DOMAIN}";
- higher_is_better: true when a higher value, or more of the outcome, is good for the people \
studied, as with remission; false when it is bad, as with symptoms of a disorder.

assignments: one entry for every name on the list, in the list's order, from the first number \
to the last with none skipped, each with
- number: the name's number;
- outcome: the name of the outcome it belongs to, exactly as defined.

Group by what is measured, not by how. Names for the same construct go together whatever \
instrument, scale, cut-off, timing or population they name: those differences are weighed \
later, within one outcome. A general term and a score on a named scale for the same construct \
go together.

Keep apart outcomes that answer different questions. Whether a condition occurs (its diagnosis, \
incidence or case status) is one outcome and how severe its symptoms are is another. Each \
outcome measures one entry of the outcome domain, so names on two entries go to two outcomes.

A name that measures nothing in the outcome domain goes to an outcome of its own, named for \
what it measures, with the domain "{OUTSIDE_DOMAIN}", so a reviewer can see it and remove it.

Return one JSON object that follows the schema.
"""


def user_message(strings: Sequence[str], existing: Sequence[NormalizedOutcome] = ()) -> str:
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(strings, start=1))
    if not existing:
        return numbered
    # A rerun: the reviewed mapping's outcomes come first, so new names can join them. Said
    # here, not in the instructions, so a first proposal's request stays the same.
    listed = "\n".join(
        f"- {o.name} ({'higher' if o.higher_is_better else 'lower'} is better)" for o in existing
    )
    return (
        "These outcomes are already in the mapping. Assign a name to one of them, by its name "
        "exactly, when it measures the same thing. Define a new outcome only for a name none of "
        "them fits, and give it a name none of them has.\n"
        f"{listed}\n\n"
        f"Names to assign:\n{numbered}"
    )


def _domains(config: QuestionConfig) -> list[str]:
    return [*config.outcome_domain, OUTSIDE_DOMAIN]


def response_schema(config: QuestionConfig) -> dict[str, Any]:
    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    outcome = obj(
        {
            "name": {"type": "string"},
            "domain": {"type": "string", "enum": _domains(config)},
            "higher_is_better": {"type": "boolean"},
        }
    )
    assignment = obj({"number": {"type": "integer"}, "outcome": {"type": "string"}})
    return obj(
        {
            "outcomes": {"type": "array", "items": outcome},
            "assignments": {"type": "array", "items": assignment},
        }
    )


def request(
    strings: Sequence[str],
    config: QuestionConfig,
    choice: ModelChoice = DEFAULT_MODEL,
    existing: Sequence[NormalizedOutcome] = (),
) -> dict[str, Any]:
    """The one Messages API request for the proposal, on the extraction model's settings."""
    settings = SETTINGS[choice]
    return {
        "model": settings.model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": system_prompt(config)}],
        "messages": [{"role": "user", "content": user_message(strings, existing)}],
        "output_config": {
            "effort": settings.effort,
            "format": {"type": "json_schema", "schema": response_schema(config)},
        },
        "thinking": settings.thinking,
    }


# Reading the proposal


class InvalidProposal(ValueError):
    pass


class _Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str
    domain: str
    higher_is_better: bool


class _Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    number: int
    outcome: str


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcomes: list[_Outcome]
    assignments: list[_Assignment]


def _problems(
    proposal: _Proposal, count: int, domains: Sequence[str], existing: set[str]
) -> list[str]:
    problems: list[str] = []
    names: set[str] = set()
    for i, outcome in enumerate(proposal.outcomes):
        name = outcome.name.casefold()
        if not name:
            problems.append(f"outcomes.{i}.name is blank")
        elif name in existing:
            problems.append(f"outcome name {name!r} is already in the mapping")
        elif name in names:
            problems.append(f"outcome name {name!r} is used twice")
        names.add(name)
        if outcome.domain not in domains:
            problems.append(f"outcome {outcome.name!r} has the domain {outcome.domain!r}")
    seen: dict[int, int] = {}
    for a in proposal.assignments:
        seen[a.number] = seen.get(a.number, 0) + 1
        if a.outcome.casefold() not in names | existing:
            problems.append(f"string {a.number} is assigned to {a.outcome!r}, not an outcome")
    problems += [f"string {n} is not on the list" for n in sorted(seen) if not 1 <= n <= count]
    problems += [f"string {n} is assigned more than once" for n in sorted(seen) if seen[n] > 1]
    problems += [f"string {n} is not assigned" for n in range(1, count + 1) if n not in seen]
    return problems


def read_proposal(
    reply: Reply,
    strings: Sequence[str],
    config: QuestionConfig,
    existing: Sequence[NormalizedOutcome] = (),
) -> list[NormalizedOutcome]:
    """The proposed outcomes, unreviewed, largest first; every string must be in exactly one.

    On a rerun, strings that join an existing outcome come back as an outcome of that name
    holding only them, unreviewed, for the mapping file to add as a table of its own.
    """
    if reply.stop_reason in ("refusal", "max_tokens"):
        raise InvalidProposal(f"stopped: {reply.stop_reason}")
    try:
        proposal = _Proposal.model_validate_json(reply.text)
    except ValidationError as error:
        if any(e["type"] == "json_invalid" for e in error.errors()):
            raise InvalidProposal("the output is not JSON") from error
        raise InvalidProposal(str(error)) from error
    known = {o.name.casefold() for o in existing}
    problems = _problems(proposal, len(strings), _domains(config), known)
    if problems:
        raise InvalidProposal("; ".join(problems))
    targets = [(o.name, o.higher_is_better) for o in proposal.outcomes]
    targets += [(o.name, o.higher_is_better) for o in existing]
    members: dict[str, list[str]] = {name.casefold(): [] for name, _ in targets}
    for a in proposal.assignments:
        members[a.outcome.casefold()].append(strings[a.number - 1])
    outcomes = [
        NormalizedOutcome(
            name=name,
            members=sorted(members[name.casefold()], key=str.casefold),
            higher_is_better=higher_is_better,
            reviewed=False,
        )
        # An outcome nothing is assigned to holds no findings.
        for name, higher_is_better in targets
        if members[name.casefold()]
    ]
    return sorted(outcomes, key=lambda o: -len(o.members))


class Proposal(Record):
    model: str
    strings: int
    # None when both attempts were invalid.
    outcomes: list[NormalizedOutcome] | None
    # The outcomes the model marked outside the domain, for review to delete.
    outside: list[str] = Field(default_factory=list[str])
    # What was wrong with each invalid attempt, in order.
    errors: list[str]
    # One entry per request sent, for the cost report.
    usage: list[Usage]


def _invalid_note(error: str) -> str:
    return (
        f"That output was invalid: {error}. Return the complete JSON object again, corrected, "
        "with every number assigned once, in order."
    )


def model_client(replay_dir: Path, live: bool) -> ModelClient:
    """A replay client, or a live one that sends what has no recording."""
    if not live:
        return ModelClient(replay_dir, Mode.REPLAY)
    return ModelClient(replay_dir, Mode.LIVE, transport=anthropic_transport(os.environ))


def propose(
    strings: Sequence[str],
    config: QuestionConfig,
    client: ModelClient,
    choice: ModelChoice = DEFAULT_MODEL,
    existing: Sequence[NormalizedOutcome] = (),
) -> Proposal:
    """The proposal over the strings given. Invalid output gets one retry naming what was wrong."""
    params = request(strings, config, choice, existing)
    errors: list[str] = []
    usage: list[Usage] = []
    outcomes: list[NormalizedOutcome] | None = None
    outside: list[str] = []
    for attempt in range(2):
        reply = client.create(params)
        usage.append(reply.usage)
        try:
            outcomes = read_proposal(reply, strings, config, existing)
            outside = [
                o.name
                for o in _Proposal.model_validate_json(reply.text).outcomes
                if o.domain == OUTSIDE_DOMAIN
            ]
            break
        except InvalidProposal as error:
            errors.append(str(error))
            if attempt == 0:
                params = retry_request(params, reply, _invalid_note(str(error)))
    return Proposal(
        model=SETTINGS[choice].model,
        strings=len(strings),
        outcomes=outcomes,
        outside=outside,
        errors=errors,
        usage=usage,
    )


# The estimate


class ProposalEstimate(Record):
    model: str
    strings: int
    input_tokens: int
    # Whether the first request is already recorded, so a rerun sends nothing.
    recorded: bool
    # The first request with typical output, at standard prices: the proposal is one direct call.
    typical_usd: float
    # The first request and its retry, each with max_tokens of output. Real spend cannot exceed it.
    ceiling_usd: float


def _tokens(params: Mapping[str, Any]) -> int:
    sent = {k: params[k] for k in ("system", "messages", "output_config")}
    return round(len(json.dumps(sent, ensure_ascii=False)) / CHARS_PER_TOKEN)


def estimate(
    strings: Sequence[str],
    config: QuestionConfig,
    replay_dir: Path,
    choice: ModelChoice,
    prices: Prices,
    existing: Sequence[NormalizedOutcome] = (),
) -> ProposalEstimate:
    params = request(strings, config, choice, existing)
    model = SETTINGS[choice].model
    tokens = _tokens(params)
    recorded = (replay_dir / f"{request_key(params)}.json").exists()
    p = model_prices(model, prices)
    retry = tokens + MAX_TOKENS + RETRY_NOTE_TOKENS
    if retry > prices.max_prompt_tokens:
        raise ValueError(
            f"the proposal's retry could pass {prices.max_prompt_tokens} prompt tokens, where the "
            "price file no longer applies"
        )
    typical = (tokens * p.input + TYPICAL_TOKENS_PER_STRING * len(strings) * p.output) / 1e6
    ceiling = ((tokens + retry) * p.input + 2 * MAX_TOKENS * p.output) / 1e6
    return ProposalEstimate(
        model=model,
        strings=len(strings),
        input_tokens=tokens,
        recorded=recorded,
        typical_usd=0 if recorded else typical,
        ceiling_usd=0 if recorded else ceiling,
    )


# The mapping file


class OutcomeMapping(Record):
    outcomes: list[NormalizedOutcome]

    @model_validator(mode="after")
    def _each_string_in_one_outcome(self) -> Self:
        seen: set[str] = set()
        for outcome in self.outcomes:
            for member in outcome.members:
                if member in seen:
                    raise ValueError(f"{member!r} is in more than one outcome")
                seen.add(member)
        return self


def _toml_string(value: str) -> str:
    # A JSON string is a TOML basic string: the same quotes, and escapes TOML accepts.
    return json.dumps(value, ensure_ascii=False)


def mapping_toml(outcomes: Sequence[NormalizedOutcome], header: Sequence[str]) -> str:
    """The mapping file's text: a header comment, then one table per outcome, edited by hand."""
    lines = [f"# {line}" if line else "#" for line in header]
    for outcome in outcomes:
        lines += [
            "",
            "[[outcomes]]",
            f"name = {_toml_string(outcome.name)}",
            f"higher_is_better = {str(outcome.higher_is_better).lower()}",
            f"reviewed = {str(outcome.reviewed).lower()}",
            "members = [",
            *(f"  {_toml_string(m)}," for m in outcome.members),
            "]",
        ]
    return "\n".join(lines) + "\n"


class _MappingFile(Record):
    outcomes: list[NormalizedOutcome]


def read_mapping(text: str) -> OutcomeMapping:
    """The mapping, with tables that share a name read as one outcome.

    A rerun appends new strings as tables of their own, so the reviewer's text is never
    rewritten; an outcome is reviewed only when every table of it is.
    """
    merged: dict[str, NormalizedOutcome] = {}
    for table in _MappingFile.model_validate(tomllib.loads(text)).outcomes:
        key = table.name.casefold()
        first = merged.get(key)
        if first is None:
            merged[key] = table
            continue
        if first.higher_is_better != table.higher_is_better:
            raise ValueError(f"the tables of outcome {first.name!r} disagree on higher_is_better")
        merged[key] = NormalizedOutcome(
            name=first.name,
            members=[*first.members, *table.members],
            higher_is_better=first.higher_is_better,
            reviewed=first.reviewed and table.reviewed,
        )
    return OutcomeMapping(outcomes=list(merged.values()))


def load_mapping(path: Path) -> OutcomeMapping:
    return read_mapping(path.read_text())


def _shown(items: Sequence[str], limit: int = 10) -> str:
    more = len(items) - limit
    return ", ".join(items[:limit]) + (f" and {more} more" if more > 0 else "")


def check_mapping(mapping: OutcomeMapping, strings: Sequence[str]) -> list[str]:
    """Why the mapping cannot be published yet: unreviewed outcomes, and strings it lacks."""
    problems: list[str] = []
    unreviewed = [o.name for o in mapping.outcomes if not o.reviewed]
    if unreviewed:
        problems.append(
            f"{len(unreviewed)} of {len(mapping.outcomes)} outcomes unreviewed: "
            f"{_shown(unreviewed)}"
        )
    missing = unmapped(mapping, strings)
    if missing:
        problems.append(f"{len(missing)} of {len(strings)} strings unmapped: {_shown(missing)}")
    return problems


def unmapped(mapping: OutcomeMapping, strings: Iterable[str]) -> list[str]:
    members = {m for o in mapping.outcomes for m in o.members}
    return sorted(s for s in set(strings) if s not in members)


def unused_members(mapping: OutcomeMapping, strings: Iterable[str]) -> list[str]:
    """Members no finding reports: harmless, and kept, since a later run may report them."""
    reported = set(strings)
    return sorted(m for o in mapping.outcomes for m in o.members if m not in reported)


def rerun_header(proposal: Proposal) -> list[str]:
    return [
        f"Added by {proposal.model} for {proposal.strings} strings the mapping had not seen. A",
        "table that shares an outcome's name adds to that outcome; review it, then merge it or",
        "set reviewed = true.",
    ]


def mapping_header(config: QuestionConfig, proposal: Proposal) -> list[str]:
    return [
        f"Outcome mapping for {config.id}: each outcome lists the outcome strings findings report",
        "that measure it. Findings are compared only within one outcome.",
        "",
        f"Proposed by {proposal.model} over {proposal.strings} strings. Review each outcome: move",
        "strings that measure something else, fix the name and higher_is_better, then set",
        "reviewed = true. An outcome outside the question's domain can be deleted whole.",
    ]
