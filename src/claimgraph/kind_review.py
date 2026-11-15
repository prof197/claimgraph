"""A local page for labeling outcome strings by kind, served from this machine only.

The page reads only the kind file and the labeling guide, never the mapping or the draw, so the
model's grouping cannot show. It lists every string alone, in one fixed shuffled order, so
neighbours carry no hint either. Each click saves the whole file, which stays the source of
truth and can be edited by hand between sessions.
"""

import random
import threading
from http import HTTPStatus
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from claimgraph import outcome_kinds
from claimgraph.models import NonBlank, Record
from claimgraph.outcome_kinds import Kind, KindLabel
from claimgraph.review import Response, json_response

PORT = 8766
SEED = 20261115
GUIDE_SECTION = "Outcome kinds"


def guide_section(guide: str, heading: str = GUIDE_SECTION) -> str:
    """One level-two section of the guide, as Markdown, without its heading."""
    marker = f"\n## {heading}\n"
    if marker not in guide:
        raise ValueError(f"the guide has no section {heading!r}")
    return guide.split(marker, 1)[1].split("\n## ", 1)[0].strip()


class PageString(Record):
    string: str
    kind: Kind | Literal[""]
    unsure: bool


class PageState(Record):
    question: str
    title: str
    kinds: list[Kind]
    rules: str
    strings: list[PageString]


class Edit(Record):
    string: NonBlank
    kind: Kind | Literal[""]
    unsure: bool


class Saved(Record):
    labeled: int
    unsure: int
    total: int


class KindSession:
    def __init__(self, out_dir: Path, question: str, title: str, guide: Path):
        self.path = out_dir / outcome_kinds.LABEL_FILE
        self.question = question
        self.title = title
        self.guide = guide
        # The server answers requests on threads; each save reads, edits and rewrites the file.
        self._lock = threading.Lock()

    def state(self) -> PageState:
        labels = outcome_kinds.load(self.path)
        shuffled = sorted(labels, key=lambda label: label.string)
        random.Random(SEED).shuffle(shuffled)
        return PageState(
            question=self.question,
            title=self.title,
            kinds=list(Kind),
            rules=guide_section(self.guide.read_text()),
            strings=[
                PageString(string=label.string, kind=label.kind, unsure=label.unsure)
                for label in shuffled
            ],
        )

    def save(self, edit: Edit) -> Saved:
        with self._lock:
            labels = outcome_kinds.load(self.path)
            strings = [label.string for label in labels]
            if edit.string not in strings:
                raise KeyError(edit.string)
            at = strings.index(edit.string)
            labels[at] = KindLabel(string=edit.string, kind=edit.kind, unsure=edit.unsure)
            outcome_kinds.write(self.path, self.question, labels)
        return Saved(
            labeled=sum(bool(label.kind) for label in labels),
            unsure=sum(label.unsure for label in labels),
            total=len(labels),
        )


def page() -> bytes:
    return files("claimgraph").joinpath("kind_page.html").read_bytes()


def handle(
    session: KindSession, method: str, path: str, body: bytes, content_type: str
) -> Response:
    """Answer one request; the server only adapts this to HTTP, so it is tested directly."""
    try:
        if method == "GET" and path in ("/", "/index.html"):
            return Response(
                status=HTTPStatus.OK, content_type="text/html; charset=utf-8", body=page()
            )
        if method == "GET" and path == "/api/state":
            return json_response(HTTPStatus.OK, session.state())
        if method == "POST" and path == "/api/kind":
            # A JSON content type cannot be sent by a plain cross-site form, so another site
            # open in the browser cannot write labels through this server.
            if content_type.split(";")[0].strip() != "application/json":
                return json_response(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send application/json"}
                )
            try:
                edit = Edit.model_validate_json(body)
            except ValidationError as error:
                return json_response(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
            try:
                return json_response(HTTPStatus.OK, session.save(edit))
            except KeyError:
                return json_response(
                    HTTPStatus.NOT_FOUND, {"error": f"{edit.string!r} is not in the kind file"}
                )
    except (OSError, ValueError) as error:
        # The file was changed by hand into something that does not read; it is never
        # rewritten then, so nothing in it is lost.
        return json_response(
            HTTPStatus.CONFLICT,
            {"error": f"{session.path} does not read: {error}. Fix it by hand, then reload."},
        )
    return json_response(HTTPStatus.NOT_FOUND, {"error": f"no route for {method} {path}"})
