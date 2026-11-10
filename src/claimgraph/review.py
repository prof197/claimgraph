"""A local page for labeling the gold abstracts, served from this machine only.

The page never fills in a label value. It makes each value one click: a sentence becomes a
quote, a number becomes the sample size, and every save writes the label file and runs the
same check as `gold check`, so the files stay the source of truth.
"""

import contextlib
import json
import re
import webbrowser
from enum import StrEnum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

from pydantic import Field, NonNegativeFloat, ValidationError

from claimgraph.config import DimensionType, QuestionConfig
from claimgraph.gold import GoldAbstract, Stratum
from claimgraph.labels import (
    LabelDraft,
    blank_draft,
    check_label_file,
    is_review_stratum,
    load_draft,
    read_gold,
    render_label,
)
from claimgraph.models import REVIEW_DESIGNS, Design, Pmid, Record

HOST = "127.0.0.1"
PAGE_DIR = Path("local-data/eval/review")

# Sentences


# Words that end in a full stop without ending the sentence. Initials and dotted forms such
# as "e.g." or "U.S." are caught by _keeps_going instead.
_ABBREVIATIONS = frozenset(
    {"vs", "al", "approx", "ca", "cf", "Dr", "Fig", "Figs", "Mr", "Mrs", "Ms", "No", "Prof", "St"}
)
# A sentence may end after closing quotes or brackets, and the next one may open with them.
_CLOSERS = "'\"\u201d\u2019)\\]"
_OPENERS = "'\"\u201c\u2018(\\["
# Some abstracts drop the space after a full stop, as in "supplementation.We conducted"; a
# lower-case word followed at once by a capitalized one marks a boundary there too.
_BOUNDARY = re.compile(
    f"[.?!][{_CLOSERS}]*(?=\\s+[{_OPENERS}]?[A-Z0-9])|(?<=[a-z]{{2}})\\.(?=[A-Z][a-z])"
)
# Dotted letter forms such as "e.g" or "U.S", seen without their final full stop.
_DOTTED = re.compile(r"[A-Za-z]{1,3}(?:\.[A-Za-z]{1,3})+")


def split_sentences(text: str) -> list[str]:
    """The sentences of one abstract section, each copied exactly from the text."""
    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        if match.group().startswith(".") and _keeps_going(text[start : match.start()]):
            continue
        sentences.append(text[start : match.end()].strip())
        start = match.end()
    if tail := text[start:].strip():
        sentences.append(tail)
    return sentences


def _keeps_going(before: str) -> bool:
    words = before.split()
    if not words:
        return True
    word = words[-1].lstrip("([")
    single_initial = len(word) == 1 and word.isupper()
    return word in _ABBREVIATIONS or single_initial or _DOTTED.fullmatch(word) is not None


# Rules from the labeling guide

_BLOCK_START = re.compile(r"^(?:\*\*`(?P<field>\w+)`\*\*|#{3} (?P<heading>.+))")


def field_rules(guide: str) -> dict[str, str]:
    """The guide's text for each abstract field, keyed by field name, Markdown as written.

    A dimension's section is keyed by its heading in snake case, so "Baseline vitamin D status"
    is found under its dimension's name.
    """
    section = guide.split("\n## Abstracts\n", 1)[-1].split("\n## ", 1)[0]
    rules: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in section.splitlines():
        if match := _BLOCK_START.match(line):
            key = match["field"] or re.sub(r"\W+", "_", match["heading"].strip().lower())
            current = rules.setdefault(key, []) if key not in rules else None
        if current is not None:
            current.append(line)
    return {key: "\n".join(lines).strip() for key, lines in rules.items()}


# What the page shows and saves


class Status(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    LABELED = "labeled"


class Mark(Record):
    """Kept for the labeler only, in the local page directory, never in a label file."""

    flagged: bool = False
    flag_note: str = ""
    # Active time on this abstract, measured by the page.
    seconds: NonNegativeFloat = 0


class PageSection(Record):
    label: str | None
    sentences: list[str]


class PageAbstract(Record):
    pmid: Pmid
    title: str
    journal: str
    year: int
    publication_types: list[str]
    slot: Stratum
    on_topic: bool
    review_stratum: bool
    sections: list[PageSection]
    # None when the file no longer reads as a draft; it is then fixed by hand.
    draft: LabelDraft | None
    blank: LabelDraft
    status: Status
    problems: list[str]


class PageDimension(Record):
    name: str
    type: DimensionType
    definition: str
    options: list[str] | None
    unit: str | None


class PageState(Record):
    question: str
    title: str
    designs: list[Design]
    review_designs: list[Design]
    dimensions: list[PageDimension]
    # Topic words to highlight: the config's terms and outcome domain.
    terms: list[str]
    rules: dict[str, str]
    abstracts: list[PageAbstract]
    marks: dict[str, Mark]
    outcomes: list[str]


class Saved(Record):
    status: Status
    problems: list[str]
    outcomes: list[str]


_SLOT_ORDER = list(Stratum)


class ReviewSession:
    """The labels of one question, read and written for the page."""

    def __init__(self, out_dir: Path, config: QuestionConfig, guide: Path, page_dir: Path):
        self.out_dir = out_dir
        self.config = config
        self.guide = guide
        self.marks_path = page_dir / f"{config.id}.marks.json"
        gold = read_gold(out_dir)
        # Grouped by slot, so trials are labeled together, then observational studies and so on.
        ordered = sorted(enumerate(gold), key=lambda p: (_SLOT_ORDER.index(p[1].slot), p[0]))
        self.gold = {g.abstract.pmid: g for _, g in ordered}

    def _path(self, pmid: str) -> Path:
        return self.out_dir / "labels" / f"{pmid}.toml"

    def _status(self, entry: GoldAbstract) -> tuple[LabelDraft | None, Status, list[str]]:
        path = self._path(entry.abstract.pmid)
        if not path.exists():
            return None, Status.NOT_STARTED, []
        try:
            draft = load_draft(path)
        except (ValueError, OSError):
            draft = None
        problems = check_label_file(path, self.gold, self.config)
        if draft == blank_draft(entry, self.config):
            return draft, Status.NOT_STARTED, problems
        return draft, Status.IN_PROGRESS if problems else Status.LABELED, problems

    def _outcomes(self) -> list[str]:
        found: set[str] = set()
        for pmid in self.gold:
            path = self._path(pmid)
            try:
                found |= {f.outcome_reported for f in load_draft(path).finding}
            except (ValueError, OSError):
                continue
        return sorted(o for o in found if o.strip())

    def _marks(self) -> dict[str, Mark]:
        if not self.marks_path.exists():
            return {}
        raw: dict[str, object] = json.loads(self.marks_path.read_text())
        return {pmid: Mark.model_validate(mark) for pmid, mark in raw.items()}

    def state(self) -> PageState:
        abstracts: list[PageAbstract] = []
        for entry in self.gold.values():
            a = entry.abstract
            draft, status, problems = self._status(entry)
            sections = [
                PageSection(label=s.label, sentences=split_sentences(s.text)) for s in a.sections
            ]
            abstracts.append(
                PageAbstract(
                    pmid=a.pmid,
                    title=a.title,
                    journal=a.journal,
                    year=a.year,
                    publication_types=a.publication_types,
                    slot=entry.slot,
                    on_topic=entry.on_topic,
                    review_stratum=is_review_stratum(entry),
                    sections=sections,
                    draft=draft,
                    blank=blank_draft(entry, self.config),
                    status=status,
                    problems=problems,
                )
            )
        c = self.config
        return PageState(
            question=c.id,
            title=c.title,
            designs=list(Design),
            review_designs=[d for d in Design if d in REVIEW_DESIGNS],
            dimensions=[
                PageDimension(
                    name=d.name,
                    type=d.type,
                    definition=d.definition,
                    options=d.options,
                    unit=d.unit,
                )
                for d in c.dimensions
            ],
            terms=[*c.exposure_terms, *c.outcome_terms, *c.outcome_domain],
            rules=field_rules(self.guide.read_text()),
            abstracts=abstracts,
            marks=self._marks(),
            outcomes=self._outcomes(),
        )

    def save(self, pmid: str, draft: LabelDraft) -> Saved:
        entry = self.gold.get(pmid)
        if entry is None or draft.pmid != pmid:
            raise KeyError(pmid)
        path = self._path(pmid)
        text = render_label(entry, self.config, draft)
        if not path.exists() or path.read_text() != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Written whole and swapped in, so an interrupted save never leaves half a label.
            partial = path.with_suffix(".toml.partial")
            partial.write_text(text)
            partial.replace(path)
        _, status, problems = self._status(entry)
        return Saved(status=status, problems=problems, outcomes=self._outcomes())

    def mark(self, pmid: str, mark: Mark) -> None:
        if pmid not in self.gold:
            raise KeyError(pmid)
        marks = self._marks() | {pmid: mark}
        self.marks_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {p: m.model_dump(mode="json") for p, m in marks.items()}
        self.marks_path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")


# Requests


class Response(Record):
    status: HTTPStatus
    content_type: str
    body: bytes = Field(repr=False)


def _json(status: HTTPStatus, payload: Record | dict[str, str]) -> Response:
    body = payload.model_dump_json() if isinstance(payload, Record) else json.dumps(payload)
    return Response(status=status, content_type="application/json", body=body.encode())


def handle(
    session: ReviewSession, page: Path, method: str, path: str, body: bytes, content_type: str
) -> Response:
    """Answer one request; the server only adapts this to HTTP, so it is tested directly."""
    if method == "GET" and path in ("/", "/index.html"):
        return Response(
            status=HTTPStatus.OK, content_type="text/html; charset=utf-8", body=page.read_bytes()
        )
    if method == "GET" and path == "/api/state":
        return _json(HTTPStatus.OK, session.state())
    if method == "POST" and (match := re.fullmatch(r"/api/(label|mark)/([0-9]+)", path)):
        # A JSON content type cannot be sent by a plain cross-site form, so another site open
        # in the browser cannot write labels through this server.
        if content_type.split(";")[0].strip() != "application/json":
            return _json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send application/json"})
        kind, pmid = match.groups()
        try:
            if kind == "label":
                return _json(
                    HTTPStatus.OK, session.save(pmid, LabelDraft.model_validate_json(body))
                )
            session.mark(pmid, Mark.model_validate_json(body))
            return _json(HTTPStatus.OK, {"saved": pmid})
        except ValidationError as error:
            return _json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
        except KeyError:
            return _json(HTTPStatus.NOT_FOUND, {"error": f"PMID {pmid} is not in the gold sample"})
    return _json(HTTPStatus.NOT_FOUND, {"error": f"no route for {method} {path}"})


def write_page(page_dir: Path) -> Path:
    """Copy the page into the local page directory, which is never committed."""
    page_dir.mkdir(parents=True, exist_ok=True)
    page = page_dir / "index.html"
    page.write_text(files("claimgraph").joinpath("review_page.html").read_text())
    return page


def serve(session: ReviewSession, page: Path, port: int, open_browser: bool) -> None:
    allowed_hosts: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        def _answer(self, method: str) -> None:
            # Requests addressed to another host name are refused, so a page elsewhere that
            # resolves its own name to this machine cannot reach the labels.
            if self.headers.get("Host", "") not in allowed_hosts:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            content_type = self.headers.get("Content-Type", "")
            response = handle(session, page, method, self.path, body, content_type)
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(response.body)

        def do_GET(self) -> None:
            self._answer("GET")

        def do_POST(self) -> None:
            self._answer("POST")

        def log_message(self, format: str, *args: object) -> None:
            return

    with ThreadingHTTPServer((HOST, port), Handler) as server:
        bound = server.server_address[1]
        allowed_hosts.update({f"{HOST}:{bound}", f"localhost:{bound}"})
        url = f"http://{HOST}:{bound}/"
        print(f"labeling page at {url} (Ctrl-C to stop)", flush=True)
        if open_browser:
            webbrowser.open(url)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
