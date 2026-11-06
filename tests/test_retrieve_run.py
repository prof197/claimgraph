"""The retrieve stage end to end, against a mock E-utilities server."""

import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from test_retrieve_parse import article
from typer.testing import CliRunner

from claimgraph import cli
from claimgraph.config import QuestionConfig, load_config
from claimgraph.retrieve import (
    DropReason,
    EutilsClient,
    NcbiIdentity,
    RateLimiter,
    RunManifest,
    describe_dates,
    retrieve,
)

SEARCHED_AT = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def config(**overrides: Any) -> QuestionConfig:
    fields: dict[str, Any] = {
        "id": "synthetic-question",
        "title": "Synthetic question for tests?",
        "exposure_terms": ["synthetic"],
        "outcome_terms": ["placebo*"],
        "outcome_domain": ["synthetic symptom score"],
        "end_date": date(2026, 10, 31),
    }
    return QuestionConfig.model_validate(fields | overrides)


# Four synthetic records: two kept, one with no abstract, one retracted. 999000005 is in
# the search result but EFetch never returns it.
RECORDS = {
    "999000001": article(pmid="999000001"),
    "999000002": article(pmid="999000002", language="fre", title="[Synthetic title]."),
    "999000003": article(pmid="999000003", abstract=None, language="ger"),
    "999000004": article(
        pmid="999000004", publication_types=("Journal Article", "Retracted Publication")
    ),
}
PMIDS = [*RECORDS, "999000005"]


class FakeEutils:
    """Serves the synthetic records and logs every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/esearch.fcgi"):
            result = {
                "count": str(len(PMIDS)),
                "idlist": PMIDS,
                "webenv": "SYNTHETIC_WEBENV",
                "querykey": "1",
            }
            return httpx.Response(200, json={"esearchresult": result})
        if request.method == "POST":
            wanted = parse_qs(request.content.decode())["id"][0].split(",")
        else:
            wanted = PMIDS
        body = b"".join(RECORDS[p] for p in wanted if p in RECORDS)
        return httpx.Response(200, content=b"<PubmedArticleSet>" + body + b"</PubmedArticleSet>")

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path.rsplit('/', 1)[-1]}" for r in self.requests]


def client(server: FakeEutils) -> EutilsClient:
    identity = NcbiIdentity(tool="claimgraph", email=None, api_key=None)
    return EutilsClient(
        httpx.Client(transport=httpx.MockTransport(server)),
        identity,
        RateLimiter(1000, sleep=lambda _: None),
        sleep=lambda _: None,
    )


def run(tmp_path: Path, server: FakeEutils, **options: Any) -> RunManifest:
    return retrieve(
        options.pop("config", config()),
        tmp_path / "synthetic-question",
        client(server),
        now=lambda: SEARCHED_AT,
        **options,
    )


def test_first_run_writes_the_stage_files(tmp_path: Path) -> None:
    server = FakeEutils()

    run(tmp_path, server)

    run_dir = tmp_path / "synthetic-question"
    assert server.paths() == ["GET esearch.fcgi", "GET efetch.fcgi"]
    assert sorted(p.name for p in (run_dir / "raw").iterdir()) == [
        f"{pmid}.xml" for pmid in RECORDS
    ]
    lines = (run_dir / "abstracts.jsonl").read_text().splitlines()
    assert [json.loads(line)["pmid"] for line in lines] == ["999000001", "999000002"]
    saved = json.loads((run_dir / "search.json").read_text())
    assert saved["pmids"] == PMIDS


def test_manifest_counts_and_reports_languages(tmp_path: Path) -> None:
    manifest = run(tmp_path, FakeEutils())

    stage = manifest.retrieve
    assert stage.question == "synthetic-question"
    assert stage.date_range == "records added to PubMed through October 31, 2026"
    assert stage.searched_at == SEARCHED_AT
    assert stage.found == 5
    assert (stage.fetched, stage.cached) == (4, 0)
    assert stage.unavailable == ["999000005"]
    assert stage.kept == 2
    assert stage.dropped == {DropReason.NO_ABSTRACT: 1, DropReason.RETRACTED: 1}
    assert stage.languages_kept == {"eng": 1, "fre": 1}
    assert stage.languages_dropped == {
        DropReason.NO_ABSTRACT: {"ger": 1},
        DropReason.RETRACTED: {"eng": 1},
    }
    assert stage.requests == 2
    assert stage.raw_bytes == sum(
        p.stat().st_size for p in (tmp_path / "synthetic-question" / "raw").iterdir()
    )


def test_manifest_is_written_beside_the_stage_files(tmp_path: Path) -> None:
    manifest = run(tmp_path, FakeEutils())

    path = tmp_path / "synthetic-question" / "manifest.json"
    assert RunManifest.model_validate_json(path.read_text()) == manifest


def test_rerun_makes_zero_requests(tmp_path: Path) -> None:
    run(tmp_path, FakeEutils())
    server = FakeEutils()

    manifest = run(tmp_path, server)

    assert server.requests == []
    assert manifest.retrieve.requests == 0
    assert (manifest.retrieve.fetched, manifest.retrieve.cached) == (0, 4)
    assert manifest.retrieve.searched_at == SEARCHED_AT
    assert manifest.retrieve.kept == 2


def test_interrupted_fetch_resumes_by_id(tmp_path: Path) -> None:
    run(tmp_path, FakeEutils())
    (tmp_path / "synthetic-question" / "raw" / "999000002.xml").unlink()
    server = FakeEutils()

    manifest = run(tmp_path, server)

    assert server.paths() == ["POST efetch.fcgi"]
    posted = parse_qs(server.requests[0].content.decode())["id"][0].split(",")
    assert posted == ["999000002"]
    assert (manifest.retrieve.fetched, manifest.retrieve.cached) == (1, 3)


def test_changed_query_searches_again(tmp_path: Path) -> None:
    run(tmp_path, FakeEutils())
    server = FakeEutils()

    run(tmp_path, server, config=config(filters=["humans[mh]"]))

    assert server.paths() == ["GET esearch.fcgi", "POST efetch.fcgi"]


def test_refresh_searches_and_fetches_everything_again(tmp_path: Path) -> None:
    run(tmp_path, FakeEutils())
    server = FakeEutils()

    manifest = run(tmp_path, server, refresh=True)

    assert server.paths() == ["GET esearch.fcgi", "GET efetch.fcgi"]
    assert (manifest.retrieve.fetched, manifest.retrieve.cached) == (4, 0)
    assert manifest.retrieve.unavailable == ["999000005"]


def test_unavailable_records_are_remembered(tmp_path: Path) -> None:
    run(tmp_path, FakeEutils())

    saved = json.loads((tmp_path / "synthetic-question" / "search.json").read_text())
    assert saved["unavailable"] == ["999000005"]


def test_no_partial_files_are_left_behind(tmp_path: Path) -> None:
    run(tmp_path, FakeEutils())

    assert not list((tmp_path / "synthetic-question").rglob("*.tmp"))


@pytest.mark.parametrize(
    ("start", "text"),
    [
        (None, "records added to PubMed through October 31, 2026"),
        (
            date(2001, 2, 3),
            "records added to PubMed from February 3, 2001 through October 31, 2026",
        ),
    ],
)
def test_date_range_is_described_in_words(start: date | None, text: str) -> None:
    assert describe_dates(config(start_date=start)) == text


# Command line


def test_run_stage_retrieve_from_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).parents[1]
    runs = tmp_path / "runs"
    committed = load_config(root / "questions" / "vitamin-d-depression.toml")
    retrieve(committed, runs / "vitamin-d-depression", client(FakeEutils()))
    server = FakeEutils()

    @contextmanager
    def connect(env: object) -> Generator[EutilsClient]:
        yield client(server)

    monkeypatch.setattr(cli, "connect_eutils", connect)
    monkeypatch.chdir(root)

    result = CliRunner().invoke(
        cli.app, ["run", "vitamin-d-depression", "--stage", "retrieve", "--runs-dir", str(runs)]
    )

    assert result.exit_code == 0, result.output
    assert "0 requests" in result.output
    assert "2 kept" in result.output
    assert server.requests == []
