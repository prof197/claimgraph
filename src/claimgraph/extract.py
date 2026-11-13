"""Extraction: one structured-output request per abstract, validated, with one retry.

The prompt is shared by every question: it states the labeling rules in general terms and is
filled in from the question config, so a new question needs no prompt edits. The response
schema is generated from the extraction model, and every response is validated against that
model, whatever the API enforced.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from pydantic import PositiveInt, ValidationError

from claimgraph.config import QuestionConfig
from claimgraph.design_tree import QUESTIONS, READINGS, follow
from claimgraph.dimensions import NOT_REACHED, build_extraction_model
from claimgraph.llm import ModelClient, ReplayMiss, Reply, Usage
from claimgraph.models import REVIEW_DESIGNS, Design, Finding, FindingFields, Record
from claimgraph.retrieve import Abstract

# Room for a long abstract's findings; a response cut off here is invalid and retried.
MAX_TOKENS = 8192


class ModelChoice(StrEnum):
    HAIKU = "haiku"
    SONNET = "sonnet"


class ModelSettings(Record):
    model: str
    # Fixed per model: a change to effort or thinking invalidates the cached prefix.
    effort: str
    thinking: dict[str, str]


# Thinking is off for extraction. Haiku 5.5 takes "disabled"; Sonnet 5.5 rejects it, and
# "between_tools", its lowest setting, returns text only when there are no tools. Both run at
# the same effort, so a comparison between them is a comparison of models.
SETTINGS: dict[ModelChoice, ModelSettings] = {
    ModelChoice.HAIKU: ModelSettings(
        model="claude-haiku-5-5", effort="medium", thinking={"type": "disabled"}
    ),
    ModelChoice.SONNET: ModelSettings(
        model="claude-sonnet-5-5", effort="medium", thinking={"type": "between_tools"}
    ),
}

# The labeling rules in general terms. DESIGN.md lists them word for word, and a test keeps the
# two the same.
RULES: tuple[str, ...] = (
    "The effect is read from the side with more of the exposure.",
    "Measures that combine the outcome domain with something else don't count, and neither do "
    "scales for a neighbouring construct.",
    "The quote is the Results sentence that reports the finding, a Conclusion sentence only when "
    "no Results sentence does, and without headings the sentence that reports the measured "
    "comparison.",
    "The outcome as reported is the measure as the abstract names it, including the instrument "
    "when the abstract names one; an instrument's name alone is complete.",
    "The sample size is the number enrolled or randomized: for a secondary analysis, the number "
    "entering it; for several samples, their total; the only count, when there is just one; "
    "never a planned size; for Mendelian randomization, cases plus controls in the main outcome "
    "dataset.",
    "Time points of one measure that agree are one finding, and ones that disagree are one each.",
    "With a comparison group, only comparisons between groups are findings, not a change over "
    "time pooled across them.",
    "Interaction or moderation results are subgroup findings, one per subgroup comparison, null "
    "ones included. When only the interaction's significance is reported, the abstract's words "
    "set each subgroup's direction.",
    "When the exposure is among the factors tested and a sentence says the rest were not "
    "significant, that sentence backs a no-change finding.",
    "Results from a genetic proxy for the exposure are findings only in a study whose design is "
    "other, such as Mendelian randomization, and only from exposure to outcome. A study that "
    "also measures the exposure keeps only its measured results, so proxy results are not "
    "counted under its design.",
    "A comparison group got none of the exposure or a different amount; a group that differs "
    "only by outcome status is not one. A review that says it followed PRISMA has systematic "
    "methods.",
)

_FINDING_GUIDE = """\
- Report one finding per outcome, arm or subgroup the abstract reports a result for, on the \
outcomes in the domain only. Null results are findings. An abstract with no result in the \
domain has no findings.
- List results on outcomes outside the domain in other_outcomes, by the abstract's names for \
them, and never as findings. A subscale of a mixed instrument that measures the outcome on its \
own counts as the outcome.
- quote: copied exactly, character for character. If the result spans two sentences, take the \
one with the result. One sentence can back several findings, such as two subgroups: give each \
finding its own entry with the same quote.
- effect: what happened to the measure on the side with more of the exposure, compared with \
the side with less: increased, decreased, or no_change when the abstract reports no significant \
difference, a non-significant trend included. More of the exposure means a higher measured \
level, a higher intake or dose, or getting it rather than placebo or nothing. In a trial it is \
the arm that got more: the exposure against placebo, or the higher dose against the lower. In a \
single-arm study it is after against before. Turn a result stated the other way round, as too \
little of the exposure against enough, so that more of the exposure is the exposure side. When \
the abstract groups people by the outcome, as in "people with the outcome had lower levels of \
the exposure", read it with the exposure as the cause: that is decreased, and outcome_reported \
names the outcome measure, not the exposure level.
- is_primary: true only when the abstract calls this the primary outcome or endpoint; \
co-primary outcomes are all true. Otherwise false, even when it is the only outcome reported. A \
subgroup result is never primary.
- is_subgroup: true when the result covers only part of the analyzed sample, split by a \
characteristic such as sex, age or baseline status. A study that enrolled only one group \
reports that group as its population, so its results are false.
- subgroup: for a subgroup result, which part of the sample it covers, as the abstract names \
it; null for a result on the whole sample. Two findings on one sentence and one outcome need \
different subgroup names."""

_STUDY_GUIDE = """\
- sample_size: participants enrolled or randomized, not analyzed or completed; null if the \
abstract gives no count, and null for a review, whose pooled total is neither.
- review_summary: for a systematic review, meta-analysis or narrative review only, one \
sentence stating its conclusion; a review protocol's summary says what the review will \
assess. Null for any other design.
- A review has no findings: it reports others' results, not its own. A protocol reports no \
results, so it has no findings, and its design is the planned design."""


def _design_questions() -> str:
    lines: list[str] = []
    for number, question in enumerate(QUESTIONS, start=1):
        options = "; ".join(
            o.value
            + ("" if o.label.lower() == o.value.replace("_", " ") else f" ({o.label.lower()})")
            + " -> "
            + (o.design.value if o.design is not None else f"go to {o.next}")
            for o in question.options
        )
        lines.append(f"{number}. {question.key}: {question.text} Answers: {options}.")
        if question.key in READINGS:
            lines.append(f"   {READINGS[question.key]}")
    return "\n".join(lines)


def _dimensions(config: QuestionConfig) -> str:
    if not config.dimensions:
        return "This question has no custom dimensions."
    lines = ["Each finding also takes one value for each of these dimensions:"]
    for d in config.dimensions:
        unit = "" if d.unit is None else f" Give it in {d.unit}, converting if needed."
        lines.append(f"- {d.name}: {d.definition}{unit}")
    return "\n".join(lines)


def system_prompt(config: QuestionConfig) -> str:
    domain = "; ".join(config.outcome_domain)
    rules = "\n".join(f"- {rule}" for rule in RULES)
    return f"""\
You extract findings from one PubMed abstract for a map of the evidence on one question.

Question: {config.title}
The exposure is what the question asks about. The outcome domain, the outcomes the question \
is about: {domain}.

Answer from the abstract alone, not from the full paper or your own knowledge. Return one JSON \
object that follows the schema.

## Design

Answer the design questions in order, starting at question 1 and following each answer to the \
next question, until an answer settles the design. Answer every question the path does not \
reach with {NOT_REACHED}. Answer for what the abstract describes, not for its publication type.

{_design_questions()}

## Study fields

{_STUDY_GUIDE}

## Rules

{rules}

## Findings

{_FINDING_GUIDE}

## Dimensions

{_dimensions(config)}
"""


def user_message(abstract: Abstract) -> str:
    return f"Title: {abstract.title}\n\n{abstract.text}"


# The response schema

# Keywords the API does not enforce in a structured-output schema. They are left out of what is
# sent and enforced by validation instead; titles are dropped to save tokens.
_DROPPED = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "default",
        "title",
    }
)
# The API's limits on one request's schema; a nullable field counts as a union.
MAX_OPTIONAL = 24
MAX_UNIONS = 16


def _strip(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip(n) for n in cast(list[Any], node)]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in cast(dict[str, Any], node).items():
        if key in ("properties", "$defs"):
            # Keys here are names, not keywords, so a property called "title" would survive.
            out[key] = {name: _strip(v) for name, v in cast(dict[str, Any], value).items()}
        elif key not in _DROPPED:
            out[key] = _strip(value)
    return out


def response_schema(config: QuestionConfig) -> dict[str, Any]:
    schema: dict[str, Any] = _strip(build_extraction_model(config).model_json_schema())
    return schema


def _objects(node: Any, path: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(node, list):
        for n, item in enumerate(cast(list[Any], node)):
            yield from _objects(item, f"{path}[{n}]")
        return
    if not isinstance(node, dict):
        return
    node = cast(dict[str, Any], node)
    yield path, node
    for key, value in node.items():
        if key in ("properties", "$defs"):
            for name, child in cast(dict[str, Any], value).items():
                yield from _objects(child, f"{path}.{name}")
        else:
            yield from _objects(value, f"{path}.{key}")


def _refs(node: Any) -> set[str]:
    return {cast(str, n["$ref"]).rsplit("/", 1)[-1] for _, n in _objects(node) if "$ref" in n}


def _is_union(prop: Mapping[str, Any]) -> bool:
    return "anyOf" in prop or isinstance(prop.get("type"), list)


def check_schema(schema: Mapping[str, Any]) -> list[str]:
    """What in a response schema the structured-output API would reject; empty if nothing."""
    problems: list[str] = []
    optional = unions = 0
    for path, node in _objects(schema):
        if node.get("type") == "object" or "properties" in node:
            if node.get("additionalProperties") is not False:
                problems.append(f"{path} needs additionalProperties: false")
            properties = cast(dict[str, Any], node.get("properties", {}))
            required = set(cast(list[str], node.get("required", [])))
            optional += len(properties.keys() - required)
            unions += sum(_is_union(p) for p in properties.values())
        problems += [
            f"{path} uses {key}, which the API does not support"
            for key in _DROPPED & {k for k in node if k != "title"}
        ]
    if optional > MAX_OPTIONAL:
        problems.append(f"{optional} optional parameters; the API allows {MAX_OPTIONAL}")
    if unions > MAX_UNIONS:
        problems.append(f"{unions} union-type parameters; the API allows {MAX_UNIONS}")
    definitions = cast(dict[str, Any], schema.get("$defs", {}))
    for name in definitions:
        seen: set[str] = set()
        todo = _refs(definitions[name])
        while todo:
            ref = todo.pop()
            if ref == name:
                problems.append(f"{name} is recursive")
                break
            if ref not in seen and ref in definitions:
                seen.add(ref)
                todo |= _refs(definitions[ref])
    return problems


def request(
    abstract: Abstract, config: QuestionConfig, choice: ModelChoice = ModelChoice.HAIKU
) -> dict[str, Any]:
    """The Messages API request for one abstract. No sampling parameters: the API rejects them."""
    schema = response_schema(config)
    problems = check_schema(schema)
    if problems:
        raise ValueError(f"the response schema breaks the API's limits: {'; '.join(problems)}")
    settings = SETTINGS[choice]
    return {
        "model": settings.model,
        "max_tokens": MAX_TOKENS,
        # The instructions and schema are the same for every abstract: the cached prefix.
        "system": [
            {"type": "text", "text": system_prompt(config), "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": user_message(abstract)}],
        "output_config": {
            "effort": settings.effort,
            "format": {"type": "json_schema", "schema": schema},
        },
        "thinking": settings.thinking,
    }


# Reading a response


class InvalidOutput(ValueError):
    pass


class DesignSource(StrEnum):
    PUBLICATION_TYPES = "publication_types"
    ANSWERS = "answers"


# Publication types that name a design, most specific first. Review alone names none, and a
# scoping review has systematic methods without pooling.
TAGGED_DESIGNS: tuple[tuple[str, Design], ...] = (
    ("Meta-Analysis", Design.META_ANALYSIS),
    ("Systematic Review", Design.SYSTEMATIC_REVIEW),
    ("Scoping Review", Design.SYSTEMATIC_REVIEW),
    ("Randomized Controlled Trial", Design.RCT),
)


def tagged_design(publication_types: Iterable[str]) -> Design | None:
    types = set(publication_types)
    return next((design for tag, design in TAGGED_DESIGNS if tag in types), None)


class ExtractedStudy(Record):
    pmid: str
    design: Design
    design_from: DesignSource
    # The answers on the model's path, kept even when a publication type set the design.
    design_answers: dict[str, str]
    sample_size: PositiveInt | None
    review_summary: str | None
    findings: list[Finding]
    other_outcomes: list[str]
    # Findings marked both primary and subgroup, kept as subgroup results.
    primary_subgroups: int
    # Findings returned for a review, which reports others' results and keeps none.
    review_findings_dropped: int


def match_enums(value: Any, node: Mapping[str, Any], defs: Mapping[str, Any]) -> Any:
    """The value with every closed-set string spelled as the schema spells it, whatever its case."""
    if "$ref" in node:
        return match_enums(value, defs[cast(str, node["$ref"]).rsplit("/", 1)[-1]], defs)
    for branch in cast(list[Mapping[str, Any]], node.get("anyOf", [])):
        value = match_enums(value, branch, defs)
    if isinstance(value, str) and "enum" in node:
        spelled = {str(option).casefold(): option for option in cast(list[Any], node["enum"])}
        return spelled.get(value.casefold(), value)
    if isinstance(value, dict) and "properties" in node:
        properties = cast(dict[str, Any], node["properties"])
        return {
            k: match_enums(v, properties.get(k, {}), defs)
            for k, v in cast(dict[str, Any], value).items()
        }
    if isinstance(value, list) and "items" in node:
        items = cast(Mapping[str, Any], node["items"])
        return [match_enums(v, items, defs) for v in cast(list[Any], value)]
    return cast(Any, value)


def _brief(error: ValidationError) -> str:
    lines = [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in error.errors()[:10]]
    return "; ".join(lines)


def read_extraction(reply: Reply, abstract: Abstract, config: QuestionConfig) -> ExtractedStudy:
    """The study a response describes. Raises InvalidOutput saying what is wrong."""
    if reply.stop_reason in ("refusal", "max_tokens"):
        raise InvalidOutput(f"the response stopped early ({reply.stop_reason})")
    try:
        data = json.loads(reply.text)
    except json.JSONDecodeError as error:
        raise InvalidOutput(f"the response is not valid JSON: {error}") from error
    model = build_extraction_model(config)
    schema = model.model_json_schema()
    data = match_enums(data, schema, schema.get("$defs", {}))
    try:
        parsed = model.model_validate(data)
    except ValidationError as error:
        raise InvalidOutput(_brief(error)) from error
    fields = parsed.model_dump()
    answers = {k: v for k, v in fields["design_answers"].items() if v != NOT_REACHED}
    try:
        answered = follow(answers)
    except ValueError as error:
        raise InvalidOutput(f"design_answers: {error}") from error
    tagged = tagged_design(abstract.publication_types)
    design = tagged or answered
    fixed = FindingFields.model_fields.keys()
    findings: list[Finding] = []
    primary_subgroups = 0
    for n, finding in enumerate(fields["findings"], start=1):
        if finding["subgroup"] is not None and not finding["is_subgroup"]:
            raise InvalidOutput(f"findings.{n - 1}.subgroup: named, but is_subgroup is false")
        if finding["is_primary"] and finding["is_subgroup"]:
            # A subgroup result never stands for its study, so it is kept as a subgroup.
            finding["is_primary"] = False
            primary_subgroups += 1
        findings.append(
            Finding(
                finding_id=f"{abstract.pmid}-{n}",
                dimensions={k: v for k, v in finding.items() if k not in fixed},
                **{k: v for k, v in finding.items() if k in fixed},
            )
        )
    is_review = design in REVIEW_DESIGNS
    return ExtractedStudy(
        pmid=abstract.pmid,
        design=design,
        design_from=DesignSource.ANSWERS if tagged is None else DesignSource.PUBLICATION_TYPES,
        design_answers=answers,
        sample_size=fields["sample_size"],
        review_summary=fields["review_summary"] if is_review else None,
        findings=[] if is_review else findings,
        other_outcomes=fields["other_outcomes"],
        primary_subgroups=0 if is_review else primary_subgroups,
        review_findings_dropped=len(findings) if is_review else 0,
    )


class ExtractionResult(Record):
    pmid: str
    model: str
    # None when both attempts were invalid; the run counts these.
    study: ExtractedStudy | None
    # What was wrong with each invalid attempt, in order.
    errors: list[str]
    # One entry per request sent, for the cost report.
    usage: list[Usage]


def _retry(params: Mapping[str, Any], reply: Reply, error: str) -> dict[str, Any]:
    note = (
        f"That output was invalid: {error}. Return the complete JSON object again, corrected, "
        "following the same instructions."
    )
    (first,) = params["messages"]
    if reply.text:
        messages = [
            first,
            {"role": "assistant", "content": reply.text},
            {"role": "user", "content": note},
        ]
    else:
        # Nothing to show back, such as an empty refusal: ask again in the one user turn.
        messages = [{"role": "user", "content": f"{first['content']}\n\n{note}"}]
    return {**params, "messages": messages}


def extract_abstract(
    abstract: Abstract,
    config: QuestionConfig,
    client: ModelClient,
    choice: ModelChoice = ModelChoice.HAIKU,
) -> ExtractionResult:
    """One abstract's extraction: invalid output gets one retry naming the error."""
    params = request(abstract, config, choice)
    errors: list[str] = []
    usage: list[Usage] = []

    def attempt(params: Mapping[str, Any]) -> tuple[Reply, ExtractedStudy | None]:
        reply = client.create(params)
        usage.append(reply.usage)
        try:
            return reply, read_extraction(reply, abstract, config)
        except InvalidOutput as error:
            errors.append(str(error))
            return reply, None

    reply, study = attempt(params)
    if study is None:
        _, study = attempt(_retry(params, reply, errors[-1]))
    return ExtractionResult(
        pmid=abstract.pmid, model=SETTINGS[choice].model, study=study, errors=errors, usage=usage
    )


# Recording the gold set


def gold_replay_dir(replay_root: Path, config: QuestionConfig, choice: ModelChoice) -> Path:
    """Where a model's gold recordings for one question are committed, apart from other models'."""
    return replay_root / config.id / "extraction" / choice.value


class GoldRecording(Record):
    results: list[ExtractionResult]
    # Gold abstracts with no recording for the current request.
    missing: list[str]
    # Recording files no current request uses, left behind by an earlier prompt or schema.
    stale: list[str]


def record_gold(
    abstracts: Sequence[Abstract],
    config: QuestionConfig,
    client: ModelClient,
    choice: ModelChoice = ModelChoice.HAIKU,
) -> GoldRecording:
    """Every gold abstract through the client: in replay, a check that each is recorded."""
    results: list[ExtractionResult] = []
    missing: list[str] = []
    for abstract in abstracts:
        try:
            results.append(extract_abstract(abstract, config, client, choice))
        except ReplayMiss:
            missing.append(abstract.pmid)
    stale = sorted(p.name for p in client.replay_dir.glob("*.json") if p.stem not in client.used)
    return GoldRecording(results=results, missing=missing, stale=stale)
