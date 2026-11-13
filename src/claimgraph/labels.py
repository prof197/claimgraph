"""Hand labels for the evals: one TOML file per gold abstract, plus outcome and contradiction pairs.

Labels are TOML so they can be written and reviewed by hand. Every value is explicit: a missing
number or text is written "not_reported", because TOML has no null.
"""

import json
import textwrap
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from claimgraph.config import NOT_REPORTED, Dimension, DimensionType, QuestionConfig
from claimgraph.design_tree import QUESTIONS, follow
from claimgraph.gold import GoldAbstract, SampleManifest, Stratum, attribution
from claimgraph.models import (
    REVIEW_DESIGNS,
    Design,
    Effect,
    NonBlank,
    Pmid,
    Reason,
    Record,
    Verdict,
    check_also_differs,
)
from claimgraph.retrieve import Abstract, write_atomic
from claimgraph.verbatim import abstract_source, normalize_quote

Participants = Annotated[int, Field(strict=True, gt=0)]
# Abstract lines in a template are wrapped to this width, after the "# " comment prefix.
TEMPLATE_WIDTH = 96


class FindingLabel(Record):
    """One finding in the outcome domain, as the labeler reads it from the abstract."""

    quote: NonBlank
    outcome_reported: NonBlank
    effect: Effect
    is_primary: StrictBool
    is_subgroup: StrictBool
    # Which part of the sample, as the abstract names it; it tells apart two subgroup findings
    # backed by one sentence.
    subgroup: NonBlank | None = None
    # One value per config dimension, checked against the config by check_abstract_label.
    dimensions: dict[str, str | float | bool] = Field(default_factory=dict[str, str | float | bool])
    note: str | None = None

    @model_validator(mode="after")
    def _a_subgroup_is_not_primary(self) -> Self:
        if self.is_primary and self.is_subgroup:
            raise ValueError("a subgroup result is never primary")
        if self.subgroup is not None and not self.is_subgroup:
            raise ValueError("only a subgroup result names a subgroup")
        return self


class AbstractLabel(Record):
    pmid: Pmid
    design: Design
    sample_size: Participants | Literal["not_reported"]
    # For a review only: systematic, meta-analysis or narrative. Optional, since the eval does
    # not score it; a blank one reads as none.
    review_summary: NonBlank | None = None
    findings: list[FindingLabel] = Field(default_factory=list[FindingLabel], alias="finding")
    note: str | None = None
    # The labeler's answers to the design questions, by question: provenance for the design,
    # optional and never scored, since in gold the design itself is the label. When given, the
    # design must follow from them.
    design_answers: dict[str, str] = Field(default_factory=dict[str, str])

    @field_validator("review_summary", mode="before")
    @classmethod
    def _blank_summary_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _design_follows_the_answers(self) -> Self:
        if not self.design_answers:
            return self
        try:
            settled = follow(self.design_answers)
        except ValueError as error:
            raise ValueError(f"design_answers: {error}") from error
        if settled is not self.design:
            raise ValueError(f"design is {self.design}, but the design answers lead to {settled}")
        return self

    @model_validator(mode="after")
    def _reviews_summarize_and_others_report(self) -> Self:
        if self.design in REVIEW_DESIGNS:
            if self.findings:
                raise ValueError("a review has no findings to count")
        elif self.review_summary is not None:
            raise ValueError("only a review takes a review_summary")
        elif not self.findings and not (self.note or "").strip():
            raise ValueError("an abstract with no findings needs a note saying why")
        return self

    @property
    def participants(self) -> int | None:
        return None if self.sample_size == NOT_REPORTED else self.sample_size


class OutcomePairLabel(Record):
    """Should these two reported outcome strings fall under one normalized outcome?"""

    a: NonBlank
    b: NonBlank
    merge: StrictBool
    note: str | None = None

    @model_validator(mode="after")
    def _two_strings(self) -> Self:
        if self.a == self.b:
            raise ValueError("an outcome pair needs two different strings")
        return self


class ContradictionLabel(Record):
    """The label for one candidate pair: does the disagreement hold, and why?"""

    outcome: NonBlank
    study_a: NonBlank
    study_b: NonBlank
    verdict: Verdict
    reason: Reason
    also_differs: list[Reason] = Field(default_factory=list[Reason])
    detail: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.study_a < self.study_b:
            raise ValueError("study_a must sort strictly before study_b, as in candidate pairs")
        check_also_differs(self.reason, self.also_differs)
        # A genuine conflict may have no standout reason; an apparent one is dissolved by a
        # known difference, which the detail names, such as "underpowered: n = 40".
        if self.verdict is Verdict.APPARENT:
            if self.reason is Reason.UNCLEAR:
                raise ValueError("an apparent pair names the reason the conflict dissolves")
            if self.detail is None:
                raise ValueError("an apparent pair needs a detail saying what dissolves it")
        return self

    @property
    def pair_id(self) -> str:
        return f"{self.outcome}|{self.study_a}|{self.study_b}"


class _OutcomePairs(Record):
    pair: list[OutcomePairLabel] = Field(default_factory=list[OutcomePairLabel])


class _ContradictionPairs(Record):
    pair: list[ContradictionLabel] = Field(default_factory=list[ContradictionLabel])


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_abstract_label(path: Path) -> AbstractLabel:
    return AbstractLabel.model_validate(_read_toml(path))


def load_outcome_pairs(path: Path) -> list[OutcomePairLabel]:
    pairs = _OutcomePairs.model_validate(_read_toml(path)).pair
    _no_repeats([" / ".join(sorted((p.a, p.b))) for p in pairs])
    return pairs


def load_contradiction_pairs(path: Path) -> list[ContradictionLabel]:
    pairs = _ContradictionPairs.model_validate(_read_toml(path)).pair
    _no_repeats([p.pair_id for p in pairs])
    return pairs


def _no_repeats(keys: list[str]) -> None:
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise ValueError(f"labeled more than once: {key}")
        seen.add(key)


# Checking labels against their abstract and question


def check_abstract_label(
    label: AbstractLabel, abstract: Abstract, config: QuestionConfig
) -> list[str]:
    problems: list[str] = []
    if label.pmid != abstract.pmid:
        problems.append(f"label is for PMID {label.pmid}, not {abstract.pmid}")
    source = abstract_source(abstract)
    # One sentence can back several findings, on different outcomes or different subgroups; only
    # the same quote, outcome and subgroup together is one finding entered twice.
    seen: dict[tuple[str, str, str | None], int] = {}
    for n, finding in enumerate(label.findings, start=1):
        quote = normalize_quote(finding.quote)
        if quote not in source:
            problems.append(f"finding {n}: quote not found in the abstract")
        problems += [f"finding {n}: {p}" for p in _dimension_problems(finding, config)]
        key = (quote, finding.outcome_reported, finding.subgroup)
        if key in seen:
            problems.append(
                f"finding {n} repeats finding {seen[key]}; name each subgroup if they differ"
            )
        seen.setdefault(key, n)
    return problems


def _dimension_problems(finding: FindingLabel, config: QuestionConfig) -> list[str]:
    expected = {d.name: d for d in config.dimensions}
    problems = [f"missing dimension {name}" for name in expected if name not in finding.dimensions]
    problems += [f"unknown dimension {name}" for name in finding.dimensions if name not in expected]
    for name, value in finding.dimensions.items():
        dimension = expected.get(name)
        if dimension is None:
            continue
        match dimension.type:
            case DimensionType.OPTIONS:
                options = dimension.options or []
                if value not in options:
                    problems.append(f"{name} must be one of {', '.join(options)}")
            case DimensionType.NUMBER:
                if isinstance(value, bool) or not (
                    isinstance(value, float | int) or value == NOT_REPORTED
                ):
                    problems.append(
                        f'{name} must be a number in {dimension.unit} or "not_reported"'
                    )
            case DimensionType.TEXT:
                if not isinstance(value, str) or not value.strip():
                    problems.append(f'{name} must be text or "not_reported"')
    return problems


class LabelReport(Record):
    labeled: list[Pmid]
    # Gold abstracts with no label file yet.
    missing: list[Pmid]
    problems: dict[str, list[str]]


def read_gold(out_dir: Path) -> list[GoldAbstract]:
    lines = (out_dir / "gold.jsonl").read_text().splitlines()
    return [GoldAbstract.model_validate_json(line) for line in lines]


def check_label_file(
    path: Path, gold: Mapping[str, GoldAbstract], config: QuestionConfig
) -> list[str]:
    """The problems with one label file, in the words `gold check` prints; empty if labeled."""
    try:
        label = load_abstract_label(path)
    except (tomllib.TOMLDecodeError, ValidationError) as error:
        return _messages(error)
    if label.pmid not in gold:
        return [f"PMID {label.pmid} is not in the gold sample"]
    if path.stem != label.pmid:
        return [f"file name must be {label.pmid}.toml"]
    return check_abstract_label(label, gold[label.pmid].abstract, config)


def check_labels(out_dir: Path, config: QuestionConfig) -> LabelReport:
    """Validate every label file for one question against its gold sample."""
    gold = {g.abstract.pmid: g for g in read_gold(out_dir)}
    labeled: list[str] = []
    problems: dict[str, list[str]] = {}
    files = sorted((out_dir / "labels").glob("*.toml"))
    for path in files:
        if found := check_label_file(path, gold, config):
            problems[path.name] = found
        else:
            labeled.append(path.stem)
    for name, loader in (
        ("outcome_pairs.toml", load_outcome_pairs),
        ("contradiction_pairs.toml", load_contradiction_pairs),
    ):
        if (out_dir / name).exists():
            try:
                loader(out_dir / name)
            except (tomllib.TOMLDecodeError, ValueError) as error:
                problems[name] = _messages(error)
    named = {p.stem for p in files}
    return LabelReport(
        labeled=sorted(labeled, key=int),
        missing=sorted((p for p in gold if p not in named), key=int),
        problems=problems,
    )


def _messages(error: Exception) -> list[str]:
    if isinstance(error, ValidationError):
        return [
            f"{'.'.join(str(part) for part in e['loc']) or 'file'}: {e['msg']}"
            for e in error.errors()
        ]
    return [str(error)]


# Drafts and templates


class FindingDraft(Record):
    """A finding as a label file holds it while it is being filled in: any value may be blank."""

    quote: str = ""
    outcome_reported: str = ""
    effect: str = ""
    is_primary: StrictBool | Literal[""] = ""
    is_subgroup: StrictBool | Literal[""] = ""
    subgroup: str | None = None
    dimensions: dict[str, StrictStr | StrictInt | StrictFloat | StrictBool] = Field(
        default_factory=dict[str, StrictStr | StrictInt | StrictFloat | StrictBool]
    )
    note: str | None = None


class LabelDraft(Record):
    """A label file's contents, valid or not yet, keyed as in the TOML file."""

    pmid: Pmid
    design: str = ""
    sample_size: StrictInt | StrictStr = ""
    review_summary: str | None = None
    note: str | None = None
    design_answers: dict[str, str] = Field(default_factory=dict[str, str])
    finding: list[FindingDraft] = Field(default_factory=list[FindingDraft])


def load_draft(path: Path) -> LabelDraft:
    return LabelDraft.model_validate(_read_toml(path))


def is_review_stratum(entry: GoldAbstract) -> bool:
    return entry.design in (Stratum.SYSTEMATIC_REVIEW, Stratum.REVIEW)


def blank_draft(entry: GoldAbstract, config: QuestionConfig) -> LabelDraft:
    """What a template holds: reviews start with a blank summary, others with one blank finding."""
    pmid = entry.abstract.pmid
    if is_review_stratum(entry):
        return LabelDraft(pmid=pmid, review_summary="")
    dimensions: dict[str, str | int | float | bool] = {d.name: "" for d in config.dimensions}
    return LabelDraft(pmid=pmid, finding=[FindingDraft(dimensions=dimensions)])


def label_template(entry: GoldAbstract, config: QuestionConfig) -> str:
    """A label file to fill in by hand, with the abstract shown as comments.

    Its blank values fail validation, so an unfilled template never passes as a label.
    """
    return render_label(entry, config, blank_draft(entry, config))


def render_label(entry: GoldAbstract, config: QuestionConfig, draft: LabelDraft) -> str:
    """A label file in the template's layout, with the abstract as comments above the values."""
    a = entry.abstract
    screened = "on" if entry.on_topic else "off"
    lines = [
        f"# {a.title}",
        f"# PMID {a.pmid}, {a.journal} {a.year}. Publication types: "
        f"{', '.join(a.publication_types)}.",
        f"# Sampled as {entry.slot}; screened {screened} topic.",
        "#",
        *_comment(_abstract_text(a)),
        "#",
        "# Fill in every value; evals/gold/LABELING.md explains each one.",
        "",
        f"pmid = {_toml_value(draft.pmid)}",
        *_comment("design: " + ", ".join(Design)),
        f"design = {_toml_value(draft.design)}",
        '# Enrolled or randomized, not analyzed; "not_reported" if the abstract does not say.',
        f"sample_size = {_toml_value(draft.sample_size)}",
    ]
    if draft.review_summary is not None:
        lines += [
            "# Optional, since the eval does not score it: one sentence, the review's conclusion.",
            "# A review has no findings.",
            f"review_summary = {_toml_value(draft.review_summary)}",
        ]
    if draft.note is not None:
        lines.append(f"note = {_toml_value(draft.note)}")
    if draft.design_answers:
        order = [q.key for q in QUESTIONS]
        keys = sorted(
            draft.design_answers, key=lambda k: order.index(k) if k in order else len(order)
        )
        lines += [
            "",
            "# Answers to the design questions in LABELING.md, in order; they settle the design.",
            "[design_answers]",
            *(f"{k} = {_toml_value(draft.design_answers[k])}" for k in keys),
        ]
    if draft.finding:
        lines += [
            "",
            "# One block per finding in the outcome domain; delete the block if there are none.",
        ]
    for n, finding in enumerate(draft.finding):
        lines += [""] * (n > 0) + _finding_block(finding, config)
    return "\n".join(lines) + "\n"


def _abstract_text(abstract: Abstract) -> list[str]:
    return [s.text if s.label is None else f"{s.label}: {s.text}" for s in abstract.sections]


def _comment(paragraphs: str | Sequence[str]) -> list[str]:
    items = [paragraphs] if isinstance(paragraphs, str) else paragraphs
    return [f"# {line}" for text in items for line in textwrap.wrap(text, TEMPLATE_WIDTH)]


def _finding_block(finding: FindingDraft, config: QuestionConfig) -> list[str]:
    lines = [
        "[[finding]]",
        f"quote = {_toml_quote(finding.quote)}",
        f"outcome_reported = {_toml_value(finding.outcome_reported)}",
        "# increased, decreased or no_change, with more vitamin D as the exposure side.",
        f"effect = {_toml_value(finding.effect)}",
        "# true only when the abstract calls it the primary outcome; never for a subgroup.",
        f"is_primary = {_toml_value(finding.is_primary)}",
        "# true when the result covers only part of the analyzed sample, such as one sex.",
        f"is_subgroup = {_toml_value(finding.is_subgroup)}",
    ]
    if finding.subgroup is not None:
        lines += [
            "# Which part of the sample, as the abstract names it.",
            f"subgroup = {_toml_value(finding.subgroup)}",
        ]
    if finding.note is not None:
        lines.append(f"note = {_toml_value(finding.note)}")
    hints = {d.name: _dimension_hint(d) for d in config.dimensions}
    # Config dimensions first, in config order, then any the file names that the config lacks,
    # so the check can report them.
    names = [*hints, *(n for n in finding.dimensions if n not in hints)]
    if names:
        lines += ["", "[finding.dimensions]"]
    for name in names:
        if name in hints:
            lines += _comment(f"{name}: {hints[name]}")
        lines.append(f"{name} = {_toml_value(finding.dimensions.get(name, ''))}")
    return lines


def _dimension_hint(dimension: Dimension) -> str:
    match dimension.type:
        case DimensionType.OPTIONS:
            return ", ".join(dimension.options or [])
        case DimensionType.NUMBER:
            return f'a number in {dimension.unit}, or "not_reported"'
        case DimensionType.TEXT:
            return 'text, or "not_reported"'


def _toml_value(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    # A JSON string is a TOML basic string, except that TOML also forbids a raw DEL.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


# A TOML multi-line literal string: no escapes, so quotes read as they appear in the abstract.
DELIMITER = "'" * 3


def _toml_quote(text: str) -> str:
    """A quote as a literal string, as the guide asks hand-written quotes to be, when it can be."""
    literal_safe = (
        DELIMITER not in text
        and not text.endswith("'")
        and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)
    )
    return f"{DELIMITER}{text}{DELIMITER}" if literal_safe else _toml_value(text)


def write_templates(out_dir: Path, config: QuestionConfig) -> list[str]:
    """Write a template for each gold abstract that has no label file; never overwrites."""
    labels = out_dir / "labels"
    labels.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for entry in read_gold(out_dir):
        path = labels / f"{entry.abstract.pmid}.toml"
        if not path.exists():
            path.write_text(label_template(entry, config))
            written.append(entry.abstract.pmid)
    return written


class Trimmed(Record):
    kept: list[Pmid]
    dropped: list[Pmid]


def trim_gold(out_dir: Path, config: QuestionConfig) -> Trimmed:
    """Keep only the gold abstracts with a passing label, so the eval runs on what was labeled.

    The rest of the draw is dropped from gold.jsonl and the attribution file, and an untouched
    template of a dropped record is deleted, since `gold template` would write it again.
    sample.json keeps the draw and adds the kept counts. Raises ValueError, changing nothing,
    while any label file is in progress or nothing is labeled.
    """
    gold = read_gold(out_dir)
    by_pmid = {g.abstract.pmid: g for g in gold}
    kept: list[GoldAbstract] = []
    untouched: list[Path] = []
    in_progress: list[Path] = []
    for path in sorted((out_dir / "labels").glob("*.toml")):
        entry = by_pmid.get(path.stem)
        if entry is not None and not check_label_file(path, by_pmid, config):
            continue
        try:
            blank = entry is not None and load_draft(path) == blank_draft(entry, config)
        except (tomllib.TOMLDecodeError, ValidationError):
            blank = False
        (untouched if blank else in_progress).append(path)
    if in_progress:
        names = ", ".join(p.name for p in in_progress)
        raise ValueError(f"finish or remove the labels in progress first: {names}")
    for entry in gold:
        path = out_dir / "labels" / f"{entry.abstract.pmid}.toml"
        if path.exists() and path not in untouched:
            kept.append(entry)
    if not kept:
        raise ValueError("nothing is labeled yet")

    manifest = SampleManifest.model_validate_json((out_dir / "sample.json").read_bytes())
    labeled = dict(Counter(e.slot for e in kept).most_common())
    manifest = manifest.model_copy(update={"labeled": labeled})
    write_atomic(out_dir / "gold.jsonl", "".join(e.model_dump_json() + "\n" for e in kept).encode())
    write_atomic(out_dir / "ATTRIBUTION.md", attribution(kept, manifest.checked_on).encode())
    write_atomic(out_dir / "sample.json", manifest.model_dump_json(indent=2).encode() + b"\n")
    for path in untouched:
        path.unlink()
    pmids = {e.abstract.pmid for e in kept}
    return Trimmed(
        kept=[e.abstract.pmid for e in kept],
        dropped=[g.abstract.pmid for g in gold if g.abstract.pmid not in pmids],
    )
