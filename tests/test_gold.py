"""The gold and dev sampler: licences, design strata, screening and quotas."""

import json
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from test_retrieve_parse import article

from claimgraph.gold import (
    DevEntry,
    GoldAbstract,
    Licence,
    PmcBucket,
    PmcVersion,
    Screening,
    SkipReason,
    Stratum,
    attribution,
    composition,
    draw,
    licence,
    load_screening,
    open_pmcids,
    stratum,
)
from claimgraph.retrieve import Abstract, EutilsClient, NcbiIdentity, RateLimiter, parse_record

FIXTURES = Path(__file__).parent / "fixtures" / "pmc"


def abstract(
    n: int, *, types: tuple[str, ...] = ("Journal Article",), year: int = 2021
) -> Abstract:
    pmid = f"9990{n:05d}"
    ids = f'<ArticleIdList><ArticleId IdType="pmc">PMC9990{n:05d}</ArticleId></ArticleIdList>'
    record = parse_record(
        article(
            pmid=pmid,
            title=f"Synthetic study {n}.",
            publication_types=types,
            pub_date=f"<Year>{year}</Year>",
            article_ids=ids,
        )
    )
    assert isinstance(record, Abstract)
    return record


def version(pmcid: str = "PMC999000001", **overrides: Any) -> PmcVersion:
    fields: dict[str, Any] = {
        "pmcid": pmcid,
        "version": 1,
        "license_code": "CC BY",
        "is_retracted": False,
        "is_manuscript": False,
    }
    return PmcVersion.model_validate(fields | overrides)


# Design strata


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        (("Journal Article",), Stratum.OBSERVATIONAL),
        (("Journal Article", "Observational Study"), Stratum.OBSERVATIONAL),
        (("Randomized Controlled Trial",), Stratum.TRIAL),
        (("Journal Article", "Clinical Trial, Phase II"), Stratum.TRIAL),
        (("Meta-Analysis", "Randomized Controlled Trial"), Stratum.SYSTEMATIC_REVIEW),
        (("Systematic Review",), Stratum.SYSTEMATIC_REVIEW),
        (("Review",), Stratum.REVIEW),
        (("Case Reports",), None),
        (("Review", "Case Reports"), None),
        (("Clinical Trial Protocol", "Randomized Controlled Trial"), None),
    ],
)
def test_stratum_comes_from_publication_types(
    types: tuple[str, ...], expected: Stratum | None
) -> None:
    assert stratum(types) is expected


# Licences


@pytest.mark.parametrize("code", ["CC BY", "CC0"])
def test_open_licences_are_accepted(code: str) -> None:
    found = licence([version(license_code=code)])

    assert isinstance(found, Licence)
    assert found.code == code


@pytest.mark.parametrize(
    ("versions", "reason"),
    [
        pytest.param([], SkipReason.NO_METADATA, id="not in the bucket"),
        pytest.param([version(license_code="CC BY-NC")], SkipReason.NOT_OPEN, id="non-commercial"),
        pytest.param([version(license_code="CC BY-SA")], SkipReason.NOT_OPEN, id="share-alike"),
        pytest.param([version(license_code="TDM")], SkipReason.NOT_OPEN, id="text mining only"),
        pytest.param([version(license_code=None)], SkipReason.NOT_OPEN, id="no licence"),
        pytest.param(
            [version(), version(version=2, license_code="CC BY-NC-ND")],
            SkipReason.NOT_OPEN,
            id="one version not open",
        ),
        pytest.param([version(is_retracted=True)], SkipReason.RETRACTED, id="retracted"),
    ],
)
def test_other_licences_are_skipped(versions: list[PmcVersion], reason: SkipReason) -> None:
    assert licence(versions) is reason


def test_licence_records_every_version() -> None:
    found = licence([version(), version(version=2, is_manuscript=True)])

    assert isinstance(found, Licence)
    assert found.versions == [1, 2]


# Drawing the sample

QUOTAS = {
    Stratum.TRIAL: 2,
    Stratum.OBSERVATIONAL: 2,
    Stratum.SYSTEMATIC_REVIEW: 1,
    Stratum.REVIEW: 1,
    Stratum.OFF_TOPIC: 1,
}
DESIGNS = {
    Stratum.TRIAL: ("Randomized Controlled Trial",),
    Stratum.OBSERVATIONAL: ("Journal Article",),
    Stratum.SYSTEMATIC_REVIEW: ("Systematic Review",),
    Stratum.REVIEW: ("Review",),
}


def pool(per_design: int = 6) -> list[Abstract]:
    designs = [d for d in DESIGNS for _ in range(per_design)]
    return [abstract(n, types=DESIGNS[d]) for n, d in enumerate(designs, start=1)] + [
        abstract(900, types=("Case Reports",))
    ]


def screen_all(
    abstracts: Iterable[Abstract], off_topic: Iterable[str] = ()
) -> dict[str, Screening]:
    off = set(off_topic)
    return {
        a.pmid: Screening(pmid=a.pmid, on_topic=a.pmid not in off, note="Synthetic screening.")
        for a in abstracts
    }


def open_licence(a: Abstract) -> Licence:
    assert a.pmcid is not None
    return Licence(pmcid=a.pmcid, code="CC BY", versions=[1])


def run_draw(abstracts: list[Abstract], screening: dict[str, Screening], **options: Any) -> Any:
    options.setdefault("licence_of", open_licence)
    return draw(abstracts, screening, seed=options.pop("seed", 7), quotas=QUOTAS, **options)


def picked(result: Any) -> list[str]:
    return [p.abstract.pmid for p in result.picks]


def test_the_draw_fills_every_quota() -> None:
    abstracts = pool()
    off = [abstracts[0].pmid, abstracts[7].pmid]

    result = run_draw(abstracts, screen_all(abstracts, off))

    assert result.pending == []
    assert result.shortfall == {}
    slots = [p.slot for p in result.picks]
    assert {s: slots.count(s) for s in QUOTAS} == QUOTAS
    assert all(p.licence is not None for p in result.picks)


def test_off_topic_records_fill_the_off_topic_quota_whatever_their_design() -> None:
    abstracts = pool()
    off = [a.pmid for a in abstracts]

    result = run_draw(abstracts, screen_all(abstracts, off))

    assert [p.slot for p in result.picks] == [Stratum.OFF_TOPIC]
    assert not result.picks[0].on_topic


def test_case_reports_are_never_drawn() -> None:
    abstracts = pool()

    result = run_draw(abstracts, screen_all(abstracts))

    assert abstract(900).pmid not in picked(result)


def test_same_seed_same_sample_and_another_seed_differs() -> None:
    abstracts = pool()
    screening = screen_all(abstracts, [abstracts[3].pmid, abstracts[20].pmid])

    assert picked(run_draw(abstracts, screening)) == picked(run_draw(abstracts, screening))
    assert picked(run_draw(abstracts, screening)) != picked(run_draw(abstracts, screening, seed=8))


def test_input_order_does_not_change_the_sample() -> None:
    abstracts = pool()
    screening = screen_all(abstracts, [abstracts[3].pmid])

    forward = run_draw(abstracts, screening).picks
    backward = run_draw(list(reversed(abstracts)), screening).picks

    assert forward == backward


def test_unscreened_records_are_listed_and_nothing_is_final() -> None:
    abstracts = pool()

    result = run_draw(abstracts, {}, batch=5)

    assert len(result.pending) == 5
    assert result.picks == []


def test_records_without_an_open_licence_are_skipped_and_counted() -> None:
    abstracts = pool()
    closed = {abstracts[0].pmid, abstracts[1].pmid}

    def licence_of(a: Abstract) -> Licence | SkipReason:
        return SkipReason.NOT_OPEN if a.pmid in closed else open_licence(a)

    result = run_draw(abstracts, screen_all(abstracts, [abstracts[9].pmid]), licence_of=licence_of)

    assert not set(picked(result)) & closed


def test_every_skipped_record_is_counted() -> None:
    abstracts = pool()

    result = run_draw(abstracts, screen_all(abstracts), licence_of=lambda _: SkipReason.RETRACTED)

    # Every record but the case report is reached, and none can take a place.
    assert result.skipped == {SkipReason.RETRACTED: len(abstracts) - 1}
    assert result.picks == []


def test_without_a_licence_check_any_record_can_be_drawn() -> None:
    abstracts = pool()

    result = run_draw(abstracts, screen_all(abstracts, [abstracts[9].pmid]), licence_of=None)

    assert len(result.picks) == sum(QUOTAS.values())
    assert all(p.licence is None for p in result.picks)
    assert result.skipped == {}


def test_a_small_pool_reports_its_shortfall() -> None:
    abstracts = pool(per_design=1)

    result = run_draw(abstracts, screen_all(abstracts))

    assert result.shortfall == {Stratum.TRIAL: 1, Stratum.OBSERVATIONAL: 1, Stratum.OFF_TOPIC: 1}


def test_records_are_screened_only_while_their_quota_has_room() -> None:
    abstracts = pool(per_design=30)
    trials = {a.pmid for a in abstracts if stratum(a.publication_types) is Stratum.TRIAL}
    # Any record might be off topic, so the off-topic quota must fill before the walk can
    # pass over a design whose own quota is full.
    observational = [
        a.pmid for a in abstracts if stratum(a.publication_types) is Stratum.OBSERVATIONAL
    ]
    screening = screen_all(abstracts, observational)
    checked: list[str] = []

    def licence_of(a: Abstract) -> Licence:
        checked.append(a.pmid)
        return open_licence(a)

    run_draw(abstracts, screening, licence_of=licence_of)

    assert len([p for p in checked if p in trials]) < len(trials)


# Screening file


def test_screening_loads_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "screening.toml"
    path.write_text(
        '[[record]]\npmid = "999000001"\non_topic = true\nnote = "Synthetic note."\n\n'
        '[[record]]\npmid = "999000002"\non_topic = false\nnote = "Synthetic note."\n'
    )

    screening = load_screening(path)

    assert screening["999000001"].on_topic
    assert not screening["999000002"].on_topic


def test_missing_screening_file_is_empty(tmp_path: Path) -> None:
    assert load_screening(tmp_path / "screening.toml") == {}


def test_screening_rejects_a_repeated_pmid(tmp_path: Path) -> None:
    path = tmp_path / "screening.toml"
    entry = '[[record]]\npmid = "999000001"\non_topic = true\nnote = "Synthetic note."\n'
    path.write_text(entry + entry)

    with pytest.raises(ValueError, match="999000001"):
        load_screening(path)


# Corpus composition


def test_composition_counts_designs_and_year_bands() -> None:
    abstracts = [
        abstract(1, year=2008),
        abstract(2, types=("Randomized Controlled Trial",), year=2016),
        abstract(3, types=("Case Reports",), year=2025),
    ]

    found = composition(abstracts)

    assert found.records == 3
    assert found.by_design == {"observational": 1, "trial": 1, "not_sampled": 1}
    assert found.by_years == {"before 2010": 1, "2015-2019": 1, "2025 on": 1}


# Attribution


def test_attribution_lists_every_record_with_its_licence() -> None:
    picks = [
        GoldAbstract(
            abstract=abstract(1),
            licence=Licence(pmcid="PMC999000001", code="CC BY", versions=[1]),
            design=Stratum.OBSERVATIONAL,
            slot=Stratum.OBSERVATIONAL,
            on_topic=True,
        ),
        GoldAbstract(
            abstract=abstract(2),
            licence=Licence(pmcid="PMC999000002", code="CC0", versions=[1]),
            design=Stratum.OBSERVATIONAL,
            slot=Stratum.OFF_TOPIC,
            on_topic=False,
        ),
    ]

    text = attribution(picks, checked_on=date(2026, 11, 5))

    assert "Synthetic study 1." in text
    assert "https://pubmed.ncbi.nlm.nih.gov/999000001/" in text
    assert "https://pmc.ncbi.nlm.nih.gov/articles/PMC999000001/" in text
    assert "CC BY" in text
    assert "CC0" in text
    assert "November 5, 2026" in text
    assert text.index("Synthetic study 1.") < text.index("Synthetic study 2.")


# PMC services, against recorded and mock responses


class FakeBucket:
    def __init__(self, listings: dict[str, bytes], objects: dict[str, bytes]) -> None:
        self.listings = listings
        self.objects = objects
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/":
            prefix = request.url.params["prefix"]
            return httpx.Response(200, content=self.listings[prefix])
        key = request.url.path.lstrip("/")
        if key in self.objects:
            return httpx.Response(200, content=self.objects[key])
        return httpx.Response(404)


EMPTY_LISTING = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b"<KeyCount>0</KeyCount><IsTruncated>false</IsTruncated></ListBucketResult>"
)


def bucket(server: FakeBucket, cache: Path) -> PmcBucket:
    return PmcBucket(
        httpx.Client(transport=httpx.MockTransport(server)), cache, sleep=lambda _: None
    )


def test_bucket_reads_the_recorded_metadata(tmp_path: Path) -> None:
    server = FakeBucket(
        {"metadata/PMC8913152.": (FIXTURES / "list-PMC8913152.xml").read_bytes()},
        {"metadata/PMC8913152.1.json": (FIXTURES / "PMC8913152.1.json").read_bytes()},
    )

    versions = bucket(server, tmp_path).versions("PMC8913152")

    assert versions == [
        PmcVersion(
            pmcid="PMC8913152",
            version=1,
            license_code="CC BY",
            is_retracted=False,
            is_manuscript=False,
        )
    ]


def test_bucket_caches_what_it_reads(tmp_path: Path) -> None:
    listings = {"metadata/PMC8913152.": (FIXTURES / "list-PMC8913152.xml").read_bytes()}
    objects = {"metadata/PMC8913152.1.json": (FIXTURES / "PMC8913152.1.json").read_bytes()}
    bucket(FakeBucket(listings, objects), tmp_path).versions("PMC8913152")
    server = FakeBucket({}, {})

    versions = bucket(server, tmp_path).versions("PMC8913152")

    assert server.requests == []
    assert [v.license_code for v in versions] == ["CC BY"]


def test_article_missing_from_the_bucket_has_no_versions(tmp_path: Path) -> None:
    server = FakeBucket({"metadata/PMC999000001.": EMPTY_LISTING}, {})

    assert bucket(server, tmp_path).versions("PMC999000001") == []


class FakePmcSearch:
    """Answers PMC licence-filter searches: every even UID is openly licensed."""

    def __init__(self) -> None:
        self.terms: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        sent = parse_qs(request.content.decode()) if request.method == "POST" else {}
        params = {k: v[0] for k, v in sent.items()} or dict(request.url.params)
        assert params["db"] == "pmc"
        term = params["term"]
        self.terms.append(term)
        uids = re.findall(r"(\d+)\[uid\]", term)
        hits = [u for u in uids if int(u) % 2 == 0]
        result = {"count": str(len(hits)), "idlist": hits, "webenv": "SYNTHETIC", "querykey": "1"}
        return httpx.Response(200, json={"esearchresult": result})


def test_open_pmcids_asks_pmc_in_batches(tmp_path: Path) -> None:
    server = FakePmcSearch()
    client = EutilsClient(
        httpx.Client(transport=httpx.MockTransport(server)),
        NcbiIdentity(tool="claimgraph", email=None, api_key=None),
        RateLimiter(1000, sleep=lambda _: None),
        sleep=lambda _: None,
    )
    pmcids = [f"PMC{999000000 + n}" for n in range(1, 251)]

    found = open_pmcids(client, pmcids)

    assert len(server.terms) == 2
    assert "cc by license" in server.terms[0]
    assert found == {p for p in pmcids if int(p[3:]) % 2 == 0}


def test_metadata_fixture_parses_without_unknown_field_errors() -> None:
    data = json.loads((FIXTURES / "PMC8913152.1.json").read_text())

    assert PmcVersion.model_validate(data).license_code == "CC BY"


def test_query_string_of_listing_names_the_prefix() -> None:
    server = FakeBucket({"metadata/PMC1.": EMPTY_LISTING}, {})
    PmcBucket(httpx.Client(transport=httpx.MockTransport(server)), None).versions("PMC1")

    query = parse_qs(server.requests[0].url.query.decode())
    assert query == {"list-type": ["2"], "prefix": ["metadata/PMC1."]}


# Command line


class AnyBucket:
    """Serves CC BY metadata for every article version 1 asked for."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/":
            pmcid = request.url.params["prefix"].removeprefix("metadata/").rstrip(".")
            listing = (
                '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<Contents><Key>metadata/{pmcid}.1.json</Key></Contents></ListBucketResult>"
            )
            return httpx.Response(200, content=listing.encode())
        pmcid = request.url.path.removeprefix("/metadata/").split(".")[0]
        return httpx.Response(200, content=version(pmcid).model_dump_json().encode())


def write_question(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-question.toml"
    path.write_text(
        'id = "synthetic-question"\ntitle = "Synthetic question for tests?"\n'
        'exposure_terms = ["synthetic"]\noutcome_terms = ["placebo*"]\n'
        'outcome_domain = ["synthetic symptom score"]\nend_date = 2026-10-31\n'
    )
    return path


def cli_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[str], list[Abstract], FakePmcSearch, AnyBucket]:
    from contextlib import contextmanager

    from claimgraph import cli, gold

    # Even PMCIDs pass the licence filter, so this pool holds 12 open records per design.
    abstracts = pool(per_design=24)
    run_dir = tmp_path / "runs" / "synthetic-question"
    run_dir.mkdir(parents=True)
    (run_dir / "abstracts.jsonl").write_text("".join(a.model_dump_json() + "\n" for a in abstracts))
    search, store = FakePmcSearch(), AnyBucket()

    @contextmanager
    def connect_eutils(env: object) -> Any:
        yield EutilsClient(
            httpx.Client(transport=httpx.MockTransport(search)),
            NcbiIdentity(tool="claimgraph", email=None, api_key=None),
            RateLimiter(1000, sleep=lambda _: None),
            sleep=lambda _: None,
        )

    @contextmanager
    def connect_bucket(cache_dir: Path) -> Any:
        yield PmcBucket(httpx.Client(transport=httpx.MockTransport(store)), cache_dir)

    monkeypatch.setattr(cli, "connect_eutils", connect_eutils)
    monkeypatch.setattr(gold, "connect_bucket", connect_bucket)
    monkeypatch.setattr(gold, "QUOTAS", QUOTAS)
    args = [
        "gold",
        "sample",
        str(write_question(tmp_path)),
        "--runs-dir",
        str(tmp_path / "runs"),
        "--gold-dir",
        str(tmp_path / "gold"),
    ]
    return args, abstracts, search, store


def invoke(args: list[str]) -> Any:
    from typer.testing import CliRunner

    from claimgraph import cli

    return CliRunner().invoke(cli.app, args)


def write_screening(tmp_path: Path, abstracts: list[Abstract], off_topic: set[str]) -> None:
    out = tmp_path / "gold" / "synthetic-question"
    out.mkdir(parents=True, exist_ok=True)
    (out / "screening.toml").write_text(
        "".join(
            f'[[record]]\npmid = "{a.pmid}"\non_topic = {str(a.pmid not in off_topic).lower()}\n'
            'note = "Synthetic screening."\n\n'
            for a in abstracts
        )
    )


def test_unscreened_records_are_written_out_for_screening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _, _, _ = cli_setup(tmp_path, monkeypatch)

    result = invoke(args)

    assert result.exit_code == 2, result.output
    to_screen = tmp_path / "runs" / "synthetic-question" / "to_screen.jsonl"
    assert len(to_screen.read_text().splitlines()) > 0
    assert not (tmp_path / "gold" / "synthetic-question" / "gold.jsonl").exists()


def test_screened_pool_writes_the_samples_and_a_rerun_makes_no_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, abstracts, search, store = cli_setup(tmp_path, monkeypatch)
    write_screening(tmp_path, abstracts, {abstracts[1].pmid, abstracts[31].pmid})

    first = invoke(args)

    assert first.exit_code == 0, first.output
    out = tmp_path / "gold" / "synthetic-question"
    files = {name: (out / name).read_text() for name in ("gold.jsonl", "dev.jsonl", "sample.json")}
    gold_lines = [
        GoldAbstract.model_validate_json(line) for line in files["gold.jsonl"].splitlines()
    ]
    assert len(gold_lines) == sum(QUOTAS.values())
    gold_pmids = {g.abstract.pmid for g in gold_lines}
    # Only even PMCIDs pass the fake licence filter.
    assert all(int(p) % 2 == 0 for p in gold_pmids)
    dev_lines = [DevEntry.model_validate_json(line) for line in files["dev.jsonl"].splitlines()]
    dev_pmids = {d.pmid for d in dev_lines}
    assert len(dev_lines) == sum(QUOTAS.values())
    assert not dev_pmids & gold_pmids
    assert any(int(p) % 2 for p in dev_pmids), "dev should draw beyond the open pool"
    assert all(
        set(json.loads(line)) == {"pmid", "design", "slot", "on_topic"}
        for line in files["dev.jsonl"].splitlines()
    )
    dev_text = tmp_path / "runs" / "synthetic-question" / "dev" / "abstracts.jsonl"
    assert {json.loads(line)["pmid"] for line in dev_text.read_text().splitlines()} == dev_pmids
    attributed = (out / "ATTRIBUTION.md").read_text()
    assert all(p in attributed for p in gold_pmids)
    assert not any(f"/{p}/" in attributed for p in dev_pmids)
    manifest = json.loads(files["sample.json"])
    assert set(manifest["splits"]) == {"gold", "dev"}
    assert manifest["open_pool"]["records"] < manifest["corpus"]["records"]
    asked = len(search.terms) + len(store.requests)

    second = invoke(args)

    assert second.exit_code == 0, second.output
    assert len(search.terms) + len(store.requests) == asked
    assert "0 requests" in second.output
    assert {name: (out / name).read_text() for name in files} == files
