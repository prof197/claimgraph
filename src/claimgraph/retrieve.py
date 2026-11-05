"""Retrieve: search PubMed and fetch its records politely through NCBI E-utilities."""

import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import date

import httpx
from pydantic import BaseModel, Field, ValidationError

from claimgraph.config import QuestionConfig
from claimgraph.models import NonBlank, Pmid, Record

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# EFetch pages through results this many records at a time.
BATCH_SIZE = 200
# ESearch returns at most this many PMIDs for PubMed; a bigger result needs a narrower query.
SEARCH_CEILING = 10_000
# Records PubMed has no Entrez date bound before; used when a config sets no start date.
EARLIEST = date(1000, 1, 1)
ATTEMPTS = 3
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


class EutilsError(RuntimeError):
    pass


def build_term(config: QuestionConfig) -> str:
    # The Entrez date ([edat]) is when PubMed added the record. Bounding it, rather than
    # the publication date, freezes the result set: nothing added after the end date can
    # appear on a later rerun.
    start = f"{config.start_date or EARLIEST:%Y/%m/%d}"
    end = f"{config.end_date:%Y/%m/%d}"
    clauses = [config.query, *config.filters, f'"{start}"[edat] : "{end}"[edat]']
    return " AND ".join(f"({clause})" for clause in clauses)


class NcbiIdentity(Record):
    """How the client names itself to NCBI, as its usage guidelines ask."""

    tool: NonBlank
    email: NonBlank | None
    api_key: NonBlank | None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "NcbiIdentity":
        def value(name: str) -> str | None:
            return env.get(name, "").strip() or None

        return cls(
            tool=value("NCBI_TOOL") or "claimgraph",
            email=value("NCBI_EMAIL"),
            api_key=value("NCBI_API_KEY"),
        )

    @property
    def rate(self) -> int:
        """Requests per second NCBI allows: 3, or 10 with an API key."""
        return 3 if self.api_key is None else 10

    def params(self) -> dict[str, str]:
        params = {"tool": self.tool}
        if self.email is not None:
            params["email"] = self.email
        if self.api_key is not None:
            params["api_key"] = self.api_key
        return params


class RateLimiter:
    """A token bucket holding one token, so requests start at least 1/rate seconds apart."""

    def __init__(
        self,
        rate: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = 1 / rate
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next is not None and now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self._interval


class SearchResult(Record):
    count: int
    pmids: list[Pmid]
    # History-server handles that let EFetch page through the result.
    webenv: str
    query_key: str


class _ESearchResult(BaseModel):
    count: int = 0
    idlist: list[Pmid] = Field(default_factory=list[Pmid])
    webenv: str = ""
    querykey: str = ""
    # Terms PubMed could not match, such as a misspelled phrase, keyed by kind.
    errorlist: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])
    error: str | None = Field(default=None, alias="ERROR")


class EutilsClient:
    def __init__(
        self,
        http: httpx.Client,
        identity: NcbiIdentity,
        limiter: RateLimiter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._identity = identity
        self._limiter = limiter
        self._sleep = sleep
        self.requests = 0

    def search(self, term: str) -> SearchResult:
        response = self._request(
            "GET",
            "esearch.fcgi",
            {
                "db": "pubmed",
                "term": term,
                "usehistory": "y",
                "retmax": str(SEARCH_CEILING),
                "retmode": "json",
            },
        )
        try:
            result = _ESearchResult.model_validate(response.json()["esearchresult"])
        except (KeyError, ValueError, ValidationError) as error:
            raise EutilsError(f"unexpected ESearch response: {error}") from error
        if result.error:
            raise EutilsError(f"ESearch error: {result.error}")
        not_found = [term for terms in result.errorlist.values() for term in terms]
        if not_found:
            raise EutilsError(f"PubMed did not find: {', '.join(not_found)}")
        if result.count > SEARCH_CEILING:
            raise EutilsError(
                f"{result.count} records exceed the {SEARCH_CEILING} PubMed returns; "
                "narrow the query or its filters"
            )
        return SearchResult(
            count=result.count,
            pmids=result.idlist,
            webenv=result.webenv,
            query_key=result.querykey,
        )

    def fetch_history(self, search: SearchResult) -> Iterator[bytes]:
        """Yield one PubmedArticleSet document per batch of the search's results."""
        for start in range(0, search.count, BATCH_SIZE):
            yield self._request(
                "GET",
                "efetch.fcgi",
                {
                    "db": "pubmed",
                    "WebEnv": search.webenv,
                    "query_key": search.query_key,
                    "retstart": str(start),
                    "retmax": str(BATCH_SIZE),
                    "retmode": "xml",
                },
            ).content

    def fetch_ids(self, pmids: Sequence[str]) -> Iterator[bytes]:
        """Yield one PubmedArticleSet document per batch of the given PMIDs."""
        for start in range(0, len(pmids), BATCH_SIZE):
            batch = pmids[start : start + BATCH_SIZE]
            yield self._request(
                "POST",
                "efetch.fcgi",
                {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"},
            ).content

    def _request(self, method: str, endpoint: str, params: dict[str, str]) -> httpx.Response:
        params = params | self._identity.params()
        url = f"{EUTILS}/{endpoint}"
        failure = ""
        for attempt in range(1, ATTEMPTS + 1):
            self._limiter.wait()
            self.requests += 1
            try:
                if method == "POST":
                    response = self._http.post(url, data=params)
                else:
                    response = self._http.get(url, params=params)
            except httpx.TransportError as error:
                failure = f"{endpoint}: {error}"
            else:
                if response.status_code == 200:
                    return response
                failure = f"{endpoint}: HTTP {response.status_code} {response.text[:200]}".strip()
                if response.status_code not in TRANSIENT_STATUSES:
                    raise EutilsError(failure)
            if attempt < ATTEMPTS:
                self._sleep(2**attempt)
        raise EutilsError(f"{failure} after {ATTEMPTS} attempts")
