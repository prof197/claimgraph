import itertools
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from claimgraph.config import QuestionConfig, TopicMatch
from claimgraph.retrieve import (
    BATCH_SIZE,
    SEARCH_CEILING,
    EutilsClient,
    EutilsError,
    NcbiIdentity,
    RateLimiter,
    build_term,
)

FIXTURES = Path(__file__).parent / "fixtures" / "pubmed"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


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


def esearch_body(count: int, pmids: list[str]) -> dict[str, Any]:
    return {
        "esearchresult": {
            "count": str(count),
            "retmax": str(len(pmids)),
            "idlist": pmids,
            "webenv": "SYNTHETIC_WEBENV",
            "querykey": "1",
        }
    }


def params(request: httpx.Request) -> dict[str, str]:
    """Query parameters of a GET, or form fields of a POST, one value each."""
    raw = request.content.decode() if request.method == "POST" else request.url.query.decode()
    return {key: values[0] for key, values in parse_qs(raw).items()}


def client(
    handler: Any, identity: NcbiIdentity | None = None, clock: FakeClock | None = None
) -> EutilsClient:
    clock = clock or FakeClock()
    identity = identity or NcbiIdentity(tool="claimgraph", email=None, api_key=None)
    return EutilsClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        identity,
        RateLimiter(identity.rate, clock=clock, sleep=clock.sleep),
        sleep=clock.sleep,
    )


# Search term


TOPIC = '(("synthetic"[tiab]) AND (placebo*[tiab]))'


def test_term_bounds_entrez_date_with_no_start() -> None:
    term = build_term(config())

    assert term == f'{TOPIC} AND ("1000/01/01"[edat] : "2026/10/31"[edat])'


def test_term_ands_each_filter_and_the_start_date() -> None:
    term = build_term(config(filters=["humans[mh]", "english[la]"], start_date=date(2001, 2, 3)))

    assert term == (
        f'{TOPIC} AND (humans[mh]) AND (english[la]) AND ("2001/02/03"[edat] : "2026/10/31"[edat])'
    )


def test_term_can_use_the_other_topic_match() -> None:
    term = build_term(config(), TopicMatch.TITLE)

    assert term.startswith('(("synthetic"[ti]) AND (placebo*[ti])) AND ')


# Identity and rate


def test_identity_reads_the_environment() -> None:
    identity = NcbiIdentity.from_env(
        {"NCBI_TOOL": "synthetic-tool", "NCBI_EMAIL": "synthetic@example.org", "NCBI_API_KEY": "k"}
    )

    assert identity == NcbiIdentity(
        tool="synthetic-tool", email="synthetic@example.org", api_key="k"
    )


def test_identity_treats_blank_values_as_unset() -> None:
    identity = NcbiIdentity.from_env({"NCBI_TOOL": "", "NCBI_EMAIL": " ", "NCBI_API_KEY": ""})

    assert identity == NcbiIdentity(tool="claimgraph", email=None, api_key=None)


@pytest.mark.parametrize(("api_key", "rate"), [(None, 3), ("k", 10)])
def test_api_key_raises_the_rate(api_key: str | None, rate: int) -> None:
    assert NcbiIdentity(tool="claimgraph", email=None, api_key=api_key).rate == rate


@pytest.mark.parametrize("rate", [3, 10])
def test_limiter_spaces_requests_evenly(rate: int) -> None:
    clock = FakeClock()
    limiter = RateLimiter(rate, clock=clock, sleep=clock.sleep)

    starts: list[float] = []
    for _ in range(rate * 2):
        limiter.wait()
        starts.append(clock.now)

    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert starts[0] == 0
    assert all(gap == pytest.approx(1 / rate) for gap in gaps)


def test_limiter_does_not_wait_after_a_pause() -> None:
    clock = FakeClock()
    limiter = RateLimiter(3, clock=clock, sleep=clock.sleep)

    limiter.wait()
    clock.now += 5
    limiter.wait()

    assert clock.sleeps == []


# Requests


def test_every_request_identifies_the_client() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(params(request))
        return httpx.Response(200, json=esearch_body(1, ["999000001"]))

    identity = NcbiIdentity(tool="claimgraph", email="synthetic@example.org", api_key="k")
    client(handler, identity).search("synthetic[tiab]")

    assert seen[0]["tool"] == "claimgraph"
    assert seen[0]["email"] == "synthetic@example.org"
    assert seen[0]["api_key"] == "k"


def test_identity_omits_unset_values() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(params(request))
        return httpx.Response(200, json=esearch_body(1, ["999000001"]))

    client(handler).search("synthetic[tiab]")

    assert "email" not in seen[0]
    assert "api_key" not in seen[0]


def test_search_uses_the_history_server() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=esearch_body(2, ["999000002", "999000001"]))

    result = client(handler).search("synthetic[tiab]")

    sent = params(seen[0])
    assert seen[0].url.path.endswith("/esearch.fcgi")
    assert sent["db"] == "pubmed"
    assert sent["term"] == "synthetic[tiab]"
    assert sent["usehistory"] == "y"
    assert sent["retmax"] == str(SEARCH_CEILING)
    assert result.count == 2
    assert result.pmids == ["999000002", "999000001"]
    assert result.webenv == "SYNTHETIC_WEBENV"
    assert result.query_key == "1"


def test_search_refuses_more_than_the_ceiling() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=esearch_body(SEARCH_CEILING + 1, []))

    with pytest.raises(EutilsError, match="narrow"):
        client(handler).search("synthetic[tiab]")


def test_search_reports_terms_pubmed_did_not_find() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = esearch_body(0, [])
        body["esearchresult"]["errorlist"] = {"phrasesnotfound": ["synthtic"], "fieldsnotfound": []}
        return httpx.Response(200, json=body)

    with pytest.raises(EutilsError, match="synthtic"):
        client(handler).search("synthtic[tiab]")


def test_search_reports_an_error_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"esearchresult": {"ERROR": "Invalid query"}})

    with pytest.raises(EutilsError, match="Invalid query"):
        client(handler).search("synthetic[tiab]")


def test_history_fetch_pages_in_batches() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/esearch.fcgi"):
            pmids = [str(999000000 + n) for n in range(1, 451)]
            return httpx.Response(200, json=esearch_body(450, pmids))
        seen.append(params(request))
        return httpx.Response(200, content=b"<PubmedArticleSet/>")

    eutils = client(handler)
    bodies = list(eutils.fetch_history(eutils.search("synthetic[tiab]")))

    assert len(bodies) == 3
    assert [s["retstart"] for s in seen] == ["0", "200", "400"]
    assert {s["retmax"] for s in seen} == {str(BATCH_SIZE)}
    assert {s["WebEnv"] for s in seen} == {"SYNTHETIC_WEBENV"}
    assert {s["query_key"] for s in seen} == {"1"}
    assert {s["retmode"] for s in seen} == {"xml"}


def test_id_fetch_posts_batches() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<PubmedArticleSet/>")

    pmids = [str(999000000 + n) for n in range(1, 202)]
    list(client(handler).fetch_ids(pmids))

    assert [r.method for r in seen] == ["POST", "POST"]
    assert [len(params(r)["id"].split(",")) for r in seen] == [200, 1]
    assert params(seen[1])["id"] == "999000201"


def test_requests_are_rate_limited() -> None:
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<PubmedArticleSet/>")

    pmids = [str(999000000 + n) for n in range(1, 801)]
    list(client(handler, clock=clock).fetch_ids(pmids))

    assert clock.now == pytest.approx(3 / 3)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_failures_are_retried(status: int) -> None:
    clock = FakeClock()
    responses = [httpx.Response(status), httpx.Response(200, content=b"<PubmedArticleSet/>")]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    eutils = client(handler, clock=clock)
    assert list(eutils.fetch_ids(["999000001"])) == [b"<PubmedArticleSet/>"]
    assert eutils.requests == 2


def test_network_errors_are_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("synthetic outage")
        return httpx.Response(200, content=b"<PubmedArticleSet/>")

    assert list(client(handler).fetch_ids(["999000001"])) == [b"<PubmedArticleSet/>"]


def test_retries_give_up() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    eutils = client(handler)
    with pytest.raises(EutilsError, match="503"):
        list(eutils.fetch_ids(["999000001"]))
    assert eutils.requests == 3


def test_client_errors_are_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    eutils = client(handler)
    with pytest.raises(EutilsError, match="400"):
        list(eutils.fetch_ids(["999000001"]))
    assert eutils.requests == 1


# Recorded responses


def test_search_parses_a_recorded_response() -> None:
    body = (FIXTURES / "esearch.json").read_bytes()

    result = client(lambda request: httpx.Response(200, content=body)).search("recorded")

    assert result.count == 3
    assert result.pmids == ["35280228", "32945627", "26585952"]
    assert result.webenv
    assert result.query_key == "1"


def test_history_fetch_returns_the_recorded_document() -> None:
    search = (FIXTURES / "esearch.json").read_bytes()
    fetch = (FIXTURES / "efetch.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=search if "esearch" in request.url.path else fetch)

    eutils = client(handler)

    assert list(eutils.fetch_history(eutils.search("recorded"))) == [fetch]
    assert eutils.requests == 2
