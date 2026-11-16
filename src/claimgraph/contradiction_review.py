"""A local page for labeling the drawn contradiction pairs, served from this machine only.

The page shows each eval pair's two findings side by side, as extracted, beside the guide's
rules. It reads the draw, the extraction and the abstracts, never a classification, so no
classifier's verdict can show. Each click saves the whole label file, which stays the source of
truth and can be edited by hand between sessions.
"""

import errno
import random
import threading
import tomllib
from collections import Counter
from collections.abc import Sequence
from http import HTTPStatus
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

import typer
from pydantic import Field, ValidationError, field_validator, model_validator

from claimgraph import extract, gold, pair_sample, paths
from claimgraph.config import NOT_REPORTED, QuestionConfig, load_config, resolve_question
from claimgraph.extract import ExtractionResult
from claimgraph.kind_review import guide_section
from claimgraph.labels import ContradictionLabel
from claimgraph.models import (
    Design,
    DimensionValue,
    Finding,
    NonBlank,
    PairKind,
    Reason,
    Record,
    Verdict,
    check_also_differs,
)
from claimgraph.outcome_pairs import toml_string
from claimgraph.pair_sample import DrawnPair, PairSample
from claimgraph.retrieve import Abstract, write_atomic
from claimgraph.review import Response, json_response, serve

PORT = 8767
LABEL_FILE = "contradiction_pairs.toml"
GUIDE_SECTION = "Contradiction pairs"
# Read once at startup, as the main CLI does: CLAIMGRAPH_DATA can move the run outputs.
RUNS_DIR = paths.runs_dir()


# The label file


class PairDraft(Record):
    """One [[pair]] of the label file, labeled or not: verdict and reason stay blank until set."""

    outcome: NonBlank
    study_a: NonBlank
    study_b: NonBlank
    verdict: Verdict | Literal[""] = ""
    reason: Reason | Literal[""] = ""
    also_differs: list[Reason] = Field(default_factory=list[Reason])
    detail: str | None = None
    note: str | None = None

    @field_validator("detail", "note")
    @classmethod
    def _blank_is_absent(cls, value: str | None) -> str | None:
        # TOML has no null and the page sends an empty box as "", so both mean no text.
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _also_differs(self) -> Self:
        if self.reason:
            check_also_differs(self.reason, self.also_differs)
        elif Reason.UNCLEAR in self.also_differs:
            raise ValueError("unclear is not a difference")
        elif len(set(self.also_differs)) != len(self.also_differs):
            raise ValueError("also_differs must not repeat a reason")
        return self

    @property
    def pair_id(self) -> str:
        return f"{self.outcome}|{self.study_a}|{self.study_b}"

    @property
    def labeled(self) -> bool:
        return bool(self.verdict and self.reason)


def _list(values: Sequence[str]) -> str:
    return "[" + ", ".join(toml_string(v) for v in values) + "]"


def render(question: str, drafts: Sequence[PairDraft]) -> str:
    verdicts = " or ".join(f'"{v}"' for v in Verdict)
    reasons = ", ".join(f'"{r}"' for r in Reason)
    lines = [
        f"# Contradiction-pair labels for {question}: {len(drafts)} pairs.",
        f"# Set each verdict to {verdicts}, and each reason to one of",
        f"# {reasons},",
        "# by the rules in evals/gold/LABELING.md, here or on the page that",
        "# gold contradiction-pairs review serves. also_differs, detail and note are optional.",
    ]
    for d in drafts:
        lines += [
            "",
            "[[pair]]",
            f"outcome = {toml_string(d.outcome)}",
            f"study_a = {toml_string(d.study_a)}",
            f"study_b = {toml_string(d.study_b)}",
            f'verdict = "{d.verdict}"',
            f'reason = "{d.reason}"',
            f"also_differs = {_list(d.also_differs)}",
        ]
        if d.detail is not None:
            lines.append(f"detail = {toml_string(d.detail)}")
        if d.note is not None:
            lines.append(f"note = {toml_string(d.note)}")
    return "\n".join(lines) + "\n"


def write(path: Path, question: str, drafts: Sequence[PairDraft]) -> None:
    write_atomic(path, render(question, drafts).encode())


def template(sample: PairSample) -> list[PairDraft]:
    """The eval pairs, blank, in one shuffled order, so the draw's strata do not run in blocks."""
    pairs = list(sample.eval)
    random.Random(sample.seed).shuffle(pairs)
    return [PairDraft(outcome=p.outcome, study_a=p.study_a, study_b=p.study_b) for p in pairs]


def write_template(out_dir: Path, sample: PairSample) -> Path:
    """Write the label file with every eval pair and no labels; never over one that exists."""
    path = out_dir / LABEL_FILE
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write(path, sample.question, template(sample))
    return path


class _LabelFile(Record):
    pair: list[PairDraft] = Field(default_factory=list[PairDraft])


def load(path: Path) -> list[PairDraft]:
    """Every entry of the label file, or ValueError if any entry does not read."""
    with path.open("rb") as f:
        drafts = _LabelFile.model_validate(tomllib.load(f)).pair
    seen: set[str] = set()
    for d in drafts:
        if d.pair_id in seen:
            raise ValueError(f"labeled more than once: {d.pair_id}")
        seen.add(d.pair_id)
    return drafts


def _messages(error: ValidationError) -> list[str]:
    """One message per field. A blank-or-value field fails once per union member, and the
    first, the value's, is the one that says what is allowed."""
    found: dict[str, str] = {}
    for e in error.errors():
        # Union members join the location as names such as str-enum[Reason]; the field is enough.
        loc = ".".join(str(p) for p in e["loc"] if isinstance(p, int) or p.isidentifier())
        found.setdefault(loc, e["msg"].removeprefix("Value error, "))
    return [f"{loc}: {msg}" if loc else msg for loc, msg in found.items()]


def _raw_pair_id(entry: object) -> str | None:
    """The pair an entry that does not read is about, so it is not also reported missing."""
    if not isinstance(entry, dict):
        return None
    fields = cast(dict[str, Any], entry)
    names = [fields.get(k) for k in ("outcome", "study_a", "study_b")]
    return "|".join(cast(list[str], names)) if all(isinstance(v, str) for v in names) else None


def label_problems(draft: PairDraft) -> list[str]:
    """What the gold label model says of a labeled pair; an unlabeled pair is only counted."""
    if not draft.labeled:
        return []
    try:
        ContradictionLabel.model_validate(draft.model_dump(exclude_none=True))
    except ValidationError as error:
        return _messages(error)
    return []


# Checking


class Report(Record):
    drawn: int
    verdicts: dict[Verdict, int]
    unlabeled: int
    problems: list[str]


def check(sample: PairSample, path: Path) -> Report:
    """Check the label file entry by entry against the eval pairs; blank pairs are counted."""
    drawn = {p.pair_id for p in sample.eval}
    verdicts: Counter[Verdict] = Counter()
    unlabeled = 0
    problems: list[str] = []
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (FileNotFoundError, tomllib.TOMLDecodeError) as error:
        data = {}
        problems.append(str(error))
    raw: Any = data.get("pair", [])
    entries = cast(list[Any], raw) if isinstance(raw, list) else []
    if not isinstance(raw, list):
        problems.append("pairs are written [[pair]], one table each")
    if stray := sorted(set(data) - {"pair"}):
        problems.append(f"unknown tables: {', '.join(stray)}")
    seen: set[str] = set()
    for n, entry in enumerate(entries, start=1):
        try:
            draft = PairDraft.model_validate(entry)
        except ValidationError as error:
            problems += [f"pair {n}: {m}" for m in _messages(error)]
            if (raw_id := _raw_pair_id(entry)) is not None:
                seen.add(raw_id)
            continue
        if draft.pair_id in seen:
            problems.append(f"pair {n}: {draft.pair_id} is labeled more than once")
            continue
        seen.add(draft.pair_id)
        if draft.pair_id not in drawn:
            problems.append(f"pair {n}: {draft.pair_id} is not an eval pair of the draw")
            continue
        if not draft.labeled:
            unlabeled += 1
            continue
        if found := label_problems(draft):
            problems += [f"pair {n}: {m}" for m in found]
        else:
            verdicts[Verdict(draft.verdict)] += 1
    if missing := [p.pair_id for p in sample.eval if p.pair_id not in seen]:
        listed = "; ".join(missing[:5])
        more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
        problems.append(f"{len(missing)} drawn pairs are not in the file: {listed}{more}")
    if unlabeled:
        problems.append(f"{unlabeled} of {len(drawn)} pairs have no verdict and reason yet")
    return Report(
        drawn=len(drawn),
        verdicts={v: verdicts[v] for v in Verdict},
        unlabeled=unlabeled,
        problems=problems,
    )


# What the page shows and saves


class Side(Record):
    """One pair's finding, with its study's design and size and its paper's year."""

    study: str
    pmid: str
    finding_id: str
    claim: str
    quote: str
    outcome_reported: str
    population: str
    intervention: str
    comparator: str
    design: Design
    sample_size: int | None
    year: int
    # Each dimension of the question, in its order, as text.
    dimensions: dict[str, str]


class PagePair(Record):
    pair_id: str
    outcome: str
    kind: PairKind
    a: Side
    b: Side
    label: PairDraft
    problems: list[str]


class PageDimension(Record):
    name: str
    definition: str


class PageState(Record):
    question: str
    title: str
    verdicts: list[Verdict]
    reasons: list[Reason]
    dimensions: list[PageDimension]
    rules: str
    pairs: list[PagePair]


class Saved(Record):
    labeled: int
    total: int
    verdicts: dict[Verdict, int]
    problems: list[str]


def _dimension_text(value: DimensionValue, unit: str | None) -> str:
    if value is None or value == NOT_REPORTED:
        return "not reported"
    if isinstance(value, str):
        return value
    number = str(int(value)) if value.is_integer() else str(value)
    return f"{number} {unit}" if unit else number


class ContradictionSession:
    """The eval pairs of one question, their findings, and the label file the page writes."""

    def __init__(
        self,
        out_dir: Path,
        config: QuestionConfig,
        results: Sequence[ExtractionResult],
        abstracts: Sequence[Abstract],
        guide: Path,
    ):
        self.path = out_dir / LABEL_FILE
        self.config = config
        self.guide = guide
        self.sample = pair_sample.read_sample(out_dir / pair_sample.SAMPLE_FILE)
        findings = {f.finding_id: (r, f) for r in results if r.study for f in r.study.findings}
        years = {a.pmid: a.year for a in abstracts}
        self.drawn = {p.pair_id: p for p in self.sample.eval}
        self.sides = {
            p.pair_id: (
                self._side(p.study_a, p.finding_a, findings, years),
                self._side(p.study_b, p.finding_b, findings, years),
            )
            for p in self.sample.eval
        }
        # The server answers requests on threads; each save reads, edits and rewrites the file.
        self._lock = threading.Lock()

    def _side(
        self,
        study: str,
        finding_id: str,
        findings: dict[str, tuple[ExtractionResult, Finding]],
        years: dict[str, int],
    ) -> Side:
        if finding_id not in findings:
            raise ValueError(f"finding {finding_id} of the draw is not in the extraction")
        result, f = findings[finding_id]
        assert result.study is not None
        if f.pmid not in years:
            raise ValueError(f"PMID {f.pmid} of the draw is not in the abstracts")
        return Side(
            study=study,
            pmid=f.pmid,
            finding_id=finding_id,
            claim=f.claim,
            quote=f.quote,
            outcome_reported=f.outcome_reported,
            population=f.population,
            intervention=f.intervention,
            comparator=f.comparator,
            design=result.study.design,
            sample_size=result.study.sample_size,
            year=years[f.pmid],
            dimensions={
                d.name: _dimension_text(f.dimensions.get(d.name), d.unit)
                for d in self.config.dimensions
            },
        )

    def _drafts(self) -> list[PairDraft]:
        """The label file, which must hold exactly the eval pairs, as its template does."""
        drafts = load(self.path)
        if stray := [d.pair_id for d in drafts if d.pair_id not in self.drawn]:
            raise ValueError(f"not an eval pair of the draw: {stray[0]}")
        if len(drafts) != len(self.drawn):
            raise ValueError(f"{len(self.drawn) - len(drafts)} eval pairs are not in the file")
        return drafts

    def state(self) -> PageState:
        pairs: list[PagePair] = []
        for draft in self._drafts():
            drawn: DrawnPair = self.drawn[draft.pair_id]
            a, b = self.sides[draft.pair_id]
            pairs.append(
                PagePair(
                    pair_id=draft.pair_id,
                    outcome=draft.outcome,
                    kind=drawn.kind,
                    a=a,
                    b=b,
                    label=draft,
                    problems=label_problems(draft),
                )
            )
        return PageState(
            question=self.config.id,
            title=self.config.title,
            verdicts=list(Verdict),
            reasons=list(Reason),
            dimensions=[
                PageDimension(name=d.name, definition=d.definition) for d in self.config.dimensions
            ],
            rules=guide_section(self.guide.read_text(), GUIDE_SECTION),
            pairs=pairs,
        )

    def save(self, edit: PairDraft) -> Saved:
        with self._lock:
            drafts = self._drafts()
            ids = [d.pair_id for d in drafts]
            if edit.pair_id not in ids:
                raise KeyError(edit.pair_id)
            drafts[ids.index(edit.pair_id)] = edit
            write(self.path, self.sample.question, drafts)
        counts = Counter(d.verdict for d in drafts if d.labeled)
        return Saved(
            labeled=sum(d.labeled for d in drafts),
            total=len(drafts),
            verdicts={v: counts[v] for v in Verdict},
            problems=label_problems(edit),
        )


def page() -> bytes:
    return files("claimgraph").joinpath("contradiction_page.html").read_bytes()


def handle(
    session: ContradictionSession, method: str, path: str, body: bytes, content_type: str
) -> Response:
    """Answer one request; the server only adapts this to HTTP, so it is tested directly."""
    try:
        if method == "GET" and path in ("/", "/index.html"):
            return Response(
                status=HTTPStatus.OK, content_type="text/html; charset=utf-8", body=page()
            )
        if method == "GET" and path == "/api/state":
            return json_response(HTTPStatus.OK, session.state())
        if method == "POST" and path == "/api/label":
            # A JSON content type cannot be sent by a plain cross-site form, so another site
            # open in the browser cannot write labels through this server.
            if content_type.split(";")[0].strip() != "application/json":
                return json_response(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send application/json"}
                )
            try:
                edit = PairDraft.model_validate_json(body)
            except ValidationError as error:
                return json_response(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
            try:
                return json_response(HTTPStatus.OK, session.save(edit))
            except KeyError:
                return json_response(
                    HTTPStatus.NOT_FOUND, {"error": f"{edit.pair_id} is not in the label file"}
                )
    except (OSError, ValueError) as error:
        # The file was changed by hand into something that does not read; it is never
        # rewritten then, so nothing in it is lost.
        return json_response(
            HTTPStatus.CONFLICT,
            {"error": f"{session.path} does not read: {error}. Fix it by hand, then reload."},
        )
    return json_response(HTTPStatus.NOT_FOUND, {"error": f"no route for {method} {path}"})


# Commands, mounted under gold by the main CLI

app = typer.Typer(
    no_args_is_help=True, help="Label the drawn contradiction pairs on a local page, and check."
)

Question = Annotated[str, typer.Argument(help="A question id, or the path to its config.")]
GoldDir = Annotated[Path, typer.Option(help="Where the samples and labels live.")]


def _sample(out_dir: Path) -> PairSample:
    source = out_dir / pair_sample.SAMPLE_FILE
    try:
        return pair_sample.read_sample(source)
    except FileNotFoundError as error:
        typer.echo(f"no drawn pairs in {source}; draw the contradiction pairs first", err=True)
        raise typer.Exit(1) from error


@app.command("template")
def template_command(question: Question, gold_dir: GoldDir = Path("evals/gold")) -> None:
    """Write the label file: every eval pair, shuffled, unlabeled. Never overwrites."""
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    try:
        path = write_template(out_dir, _sample(out_dir))
    except FileExistsError as error:
        typer.echo(f"{error} already exists and may hold labels; not overwritten", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"wrote {path}")


@app.command("check")
def check_command(question: Question, gold_dir: GoldDir = Path("evals/gold")) -> None:
    """Check the labels against the drawn eval pairs; exits 1 until every pair is labeled."""
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    report = check(_sample(out_dir), out_dir / LABEL_FILE)
    counts = ", ".join(f"{n} {v}" for v, n in report.verdicts.items())
    typer.echo(f"{sum(report.verdicts.values())} of {report.drawn} pairs labeled: {counts}")
    for problem in report.problems:
        typer.echo(problem, err=True)
    if report.problems:
        raise typer.Exit(1)


@app.command("review")
def review_command(
    question: Question,
    gold_dir: GoldDir = Path("evals/gold"),
    runs_dir: Annotated[Path, typer.Option(help="Where run outputs are read from.")] = RUNS_DIR,
    model: Annotated[
        extract.ModelChoice, typer.Option(help="The extraction the pairs were drawn from.")
    ] = extract.DEFAULT_MODEL,
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1; 0 picks a free one.")] = PORT,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open a browser.")] = True,
) -> None:
    """Serve a local page for labeling each eval pair; every click is saved.

    The page shows the two findings side by side beside the guide's rules, and never a
    classifier's output.
    """
    config = load_config(resolve_question(question))
    out_dir = gold_dir / config.id
    run_dir = runs_dir / config.id
    _sample(out_dir)
    if not (out_dir / LABEL_FILE).exists():
        typer.echo(
            f"no label file in {out_dir}; run gold contradiction-pairs template first", err=True
        )
        raise typer.Exit(1)
    source = run_dir / "extract" / f"corpus-{model.value}.jsonl"
    try:
        lines = source.read_text().splitlines()
        results = [ExtractionResult.model_validate_json(line) for line in lines]
        abstracts = gold.read_abstracts(run_dir / "abstracts.jsonl")
    except (FileNotFoundError, ValidationError) as error:
        typer.echo(f"no current extraction or abstracts in {run_dir}: {error}", err=True)
        raise typer.Exit(1) from error
    try:
        session = ContradictionSession(
            out_dir, config, results, abstracts, gold_dir / "LABELING.md"
        )
        session.state()
    except (OSError, ValueError) as error:
        typer.echo(f"not served: {error}", err=True)
        raise typer.Exit(1) from error
    try:
        serve(
            lambda method, path, body, kind: handle(session, method, path, body, kind),
            port,
            open_browser,
        )
    except OSError as error:
        if error.errno != errno.EADDRINUSE:
            raise
        typer.echo(f"port {port} is in use; pass --port 0 to pick a free one", err=True)
        raise typer.Exit(1) from error
