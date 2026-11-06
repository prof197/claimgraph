"""Retrieve: search PubMed, fetch its records politely and parse their abstracts."""

import re
import time
from collections import Counter
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, Field, ValidationError

from claimgraph.config import QuestionConfig, TopicMatch
from claimgraph.models import NonBlank, Pmcid, Pmid, Record, TrialId

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# EFetch pages through results this many records at a time.
BATCH_SIZE = 200
# ESearch returns at most this many PMIDs for PubMed; a bigger result needs a narrower query.
SEARCH_CEILING = 10_000
# Records PubMed has no Entrez date bound before; used when a config sets no start date.
EARLIEST = date(1000, 1, 1)
ATTEMPTS = 3
# Longer search terms, such as a list of IDs, go by POST: NCBI rejects long GET URLs.
LONG_TERM = 2_000
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


class EutilsError(RuntimeError):
    pass


def build_term(config: QuestionConfig, match: TopicMatch | None = None) -> str:
    # The Entrez date ([edat]) is when PubMed added the record. Bounding it, rather than
    # the publication date, freezes the result set: nothing added after the end date can
    # appear on a later rerun.
    start = f"{config.start_date or EARLIEST:%Y/%m/%d}"
    end = f"{config.end_date:%Y/%m/%d}"
    clauses = [config.topic_query(match), *config.filters, f'"{start}"[edat] : "{end}"[edat]']
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

    def search(self, term: str, db: str = "pubmed") -> SearchResult:
        """Search an Entrez database; for PMC, the IDs returned are PMCIDs without "PMC"."""
        response = self._request(
            "POST" if len(term) > LONG_TERM else "GET",
            "esearch.fcgi",
            {
                "db": db,
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
            raise EutilsError(f"ESearch did not find: {', '.join(not_found)}")
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

        def send() -> httpx.Response:
            self._limiter.wait()
            self.requests += 1
            if method == "POST":
                return self._http.post(url, data=params)
            return self._http.get(url, params=params)

        return send_with_retries(send, endpoint, self._sleep)


def send_with_retries(
    send: Callable[[], httpx.Response], name: str, sleep: Callable[[float], None]
) -> httpx.Response:
    """Send until a 200, retrying rate limits, server errors and network failures."""
    failure = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = send()
        except httpx.TransportError as error:
            failure = f"{name}: {error}"
        else:
            if response.status_code == 200:
                return response
            failure = f"{name}: HTTP {response.status_code} {response.text[:200]}".strip()
            if response.status_code not in TRANSIENT_STATUSES:
                raise EutilsError(failure)
        if attempt < ATTEMPTS:
            sleep(2**attempt)
    raise EutilsError(f"{failure} after {ATTEMPTS} attempts")


# Parsing


class DropReason(StrEnum):
    BOOK = "book"
    RETRACTED = "retracted"
    NO_ABSTRACT = "no_abstract"
    # PubMed puts English in Abstract and other languages in OtherAbstract.
    NON_ENGLISH_ONLY = "non_english_only"


class AbstractSection(Record):
    label: str | None
    category: str | None
    text: NonBlank


class Abstract(Record):
    """One PubMed record with an English abstract: a line of abstracts.jsonl."""

    pmid: Pmid
    # Set when PubMed Central holds the article; its licence is looked up there.
    pmcid: Pmcid | None
    title: str
    first_author: str | None
    journal: str
    year: int
    language: str
    publication_types: list[str]
    mesh: list[str]
    trial_ids: list[TrialId]
    sections: Annotated[list[AbstractSection], Field(min_length=1)]

    @property
    def text(self) -> str:
        return "\n".join(
            section.text if section.label is None else f"{section.label}: {section.text}"
            for section in self.sections
        )


class Dropped(Record):
    pmid: Pmid
    reason: DropReason
    # The article's language, from PubMed.
    language: str


_NCT_ID = re.compile(r"\bNCT\d{8}\b")
CLINICALTRIALS_GOV = "ClinicalTrials.gov"
# The clinical trial registries PubMed links from a record's DataBankList, as NLM lists them.
# Other DataBank entries are data repositories, such as figshare or GenBank.
TRIAL_REGISTRIES = frozenset(
    {
        "ANZCTR",
        "ChiCTR",
        "CRiS",
        CLINICALTRIALS_GOV,
        "CTRI",
        "DRKS",
        "EudraCT",
        "IRCT",
        "ISRCTN",
        "JapicCTI",
        "JMACCT",
        "JPRN",
        "NTR",
        "PACTR",
        "ReBec",
        "REPEC",
        "RPCEC",
        "SLCTR",
        "TCTR",
        "UMIN CTR",
    }
)
_YEAR = re.compile(r"\b(\d{4})\b")


def split_articles(document: bytes) -> dict[str, bytes]:
    """Split one EFetch response into the raw XML of each record, keyed by PMID."""
    root = ElementTree.fromstring(document)
    if root.tag != "PubmedArticleSet":
        message = root.findtext("ERROR") or f"unexpected root element {root.tag}"
        raise EutilsError(f"EFetch error: {message}")
    records: dict[str, bytes] = {}
    for element in root:
        pmid = element.findtext("MedlineCitation/PMID") or element.findtext("BookDocument/PMID")
        if pmid is None:
            raise EutilsError(f"record without a PMID in {element.tag}")
        records[pmid] = ElementTree.tostring(element, encoding="utf-8")
    return records


def parse_record(xml: bytes) -> Abstract | Dropped:
    element = ElementTree.fromstring(xml)
    if element.tag == "PubmedBookArticle":
        document = _required(element, "BookDocument")
        return Dropped(
            pmid=_required_text(document, "PMID"),
            reason=DropReason.BOOK,
            language=document.findtext("Language") or "und",
        )

    citation = _required(element, "MedlineCitation")
    article = _required(citation, "Article")
    pmid = _required_text(citation, "PMID")
    language = article.findtext("Language") or "und"
    publication_types = [_text(t) for t in article.iterfind("PublicationTypeList/PublicationType")]

    if "Retracted Publication" in publication_types:
        return Dropped(pmid=pmid, reason=DropReason.RETRACTED, language=language)
    sections = [
        AbstractSection(label=s.get("Label"), category=s.get("NlmCategory"), text=_text(s))
        for s in article.iterfind("Abstract/AbstractText")
        if _text(s)
    ]
    if not sections:
        # The DTD defaults an OtherAbstract's language to English.
        non_english = any(
            _text(o) and o.get("Language", "eng") != "eng"
            for o in citation.iterfind("OtherAbstract")
        )
        reason = DropReason.NON_ENGLISH_ONLY if non_english else DropReason.NO_ABSTRACT
        return Dropped(pmid=pmid, reason=reason, language=language)

    title = _text(article.find("ArticleTitle")) or _text(article.find("VernacularTitle"))
    text = " ".join([title, *(s.text for s in sections)])
    return Abstract(
        pmid=pmid,
        pmcid=_pmcid(element),
        title=title,
        first_author=_first_author(article),
        journal=article.findtext("Journal/ISOAbbreviation") or _text(article.find("Journal/Title")),
        year=_year(element, pmid),
        language=language,
        publication_types=publication_types,
        mesh=[_text(d) for d in citation.iterfind("MeshHeadingList/MeshHeading/DescriptorName")],
        trial_ids=_trial_ids(article, text),
        sections=sections,
    )


def _text(element: ElementTree.Element | None) -> str:
    """The element's text with inline markup removed and whitespace collapsed."""
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _required(element: ElementTree.Element, path: str) -> ElementTree.Element:
    found = element.find(path)
    if found is None:
        raise EutilsError(f"record without {path}")
    return found


def _required_text(element: ElementTree.Element, path: str) -> str:
    return _text(_required(element, path))


def _first_author(article: ElementTree.Element) -> str | None:
    author = article.find("AuthorList/Author")
    if author is None:
        return None
    return author.findtext("LastName") or author.findtext("CollectiveName")


def _pmcid(element: ElementTree.Element) -> str | None:
    # The record's own IDs only: each entry in its reference list carries IDs too.
    for article_id in element.iterfind("PubmedData/ArticleIdList/ArticleId"):
        if article_id.get("IdType") == "pmc":
            return _text(article_id)
    return None


def _year(element: ElementTree.Element, pmid: str) -> int:
    # The issue's publication year, then MEDLINE's free-text date, then the electronic
    # publication date, then the date the record entered PubMed.
    pub_date = "MedlineCitation/Article/Journal/JournalIssue/PubDate"
    candidates = [
        element.findtext(f"{pub_date}/Year"),
        element.findtext(f"{pub_date}/MedlineDate"),
        element.findtext("MedlineCitation/Article/ArticleDate/Year"),
        element.findtext("PubmedData/History/PubMedPubDate[@PubStatus='pubmed']/Year"),
    ]
    for candidate in candidates:
        match = _YEAR.search(candidate or "")
        if match:
            return int(match.group(1))
    raise EutilsError(f"record {pmid} has no year")


def _trial_ids(article: ElementTree.Element, text: str) -> list[str]:
    # Papers sharing any registration are one trial, so IDs from every registry are kept.
    # NCT numbers are also read from the text, where many abstracts state them.
    registrations = {(CLINICALTRIALS_GOV, number) for number in _NCT_ID.findall(text)}
    for bank in article.iterfind("DataBankList/DataBank"):
        registry = _text(bank.find("DataBankName"))
        if registry not in TRIAL_REGISTRIES:
            continue
        for number in bank.iterfind("AccessionNumberList/AccessionNumber"):
            accession = _text(number).upper()
            # A malformed entry is skipped rather than failing the whole record.
            malformed = not accession or " " in accession
            if malformed or (registry == CLINICALTRIALS_GOV and not _NCT_ID.fullmatch(accession)):
                continue
            registrations.add((registry, accession))
    return sorted(f"{registry.upper()}:{accession}" for registry, accession in registrations)


# The retrieve stage


class ScopeSearch(Record):
    term: str
    count: int
    pmids: list[Pmid]


class SavedSearch(Record):
    """search.json: what each scope's search returned, so reruns need not search again.

    Both scopes are searched on every run, so their counts and PMIDs can be compared from one
    cache; only the configured scope's records are fetched.
    """

    searched_at: datetime
    scopes: dict[TopicMatch, ScopeSearch]
    # Asked for but not returned by EFetch; retried only on refresh, so reruns stay offline.
    unavailable: list[Pmid] = Field(default_factory=list[Pmid])


class RetrieveManifest(Record):
    question: str
    # The scope this run retrieved, its search term, and how many records each scope finds.
    scope: TopicMatch
    term: str
    scope_counts: dict[TopicMatch, int]
    # What the date bound means, in words, for the page's method section.
    date_range: str
    searched_at: datetime
    found: int
    fetched: int
    cached: int
    # In the search result, but EFetch returned no record for them.
    unavailable: list[Pmid]
    kept: int
    dropped: dict[DropReason, int]
    languages_kept: dict[str, int]
    languages_dropped: dict[DropReason, dict[str, int]]
    requests: int
    raw_bytes: int


class RunManifest(Record):
    """manifest.json: what one run of a question did, stage by stage."""

    retrieve: RetrieveManifest


def describe_dates(config: QuestionConfig) -> str:
    def spoken(day: date) -> str:
        return f"{day:%B} {day.day}, {day.year}"

    end = spoken(config.end_date)
    if config.start_date is None:
        return f"records added to PubMed through {end}"
    return f"records added to PubMed from {spoken(config.start_date)} through {end}"


def retrieve(
    config: QuestionConfig,
    run_dir: Path,
    client: EutilsClient,
    *,
    refresh: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunManifest:
    """Search, fetch what the cache lacks, and parse every record into abstracts.jsonl."""
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    requests_before = client.requests
    scope = config.topic_match
    terms = {match: build_term(config, match) for match in TopicMatch}

    search_path = run_dir / "search.json"
    saved = _load_search(search_path)
    history: SearchResult | None = None
    if refresh or saved is None or {m: s.term for m, s in saved.scopes.items()} != terms:
        # The configured scope is searched last, so its history-server handle is the freshest.
        results: dict[TopicMatch, SearchResult] = {}
        for match in [m for m in TopicMatch if m is not scope] + [scope]:
            results[match] = client.search(terms[match])
        history = results[scope]
        saved = SavedSearch(
            searched_at=now(),
            scopes={
                match: ScopeSearch(term=terms[match], count=result.count, pmids=result.pmids)
                for match, result in sorted(results.items())
            },
        )
        write_atomic(search_path, saved.model_dump_json(indent=2).encode())
    pmids = saved.scopes[scope].pmids

    def raw_path(pmid: str) -> Path:
        return raw_dir / f"{pmid}.xml"

    skip = set(saved.unavailable)
    missing = [p for p in pmids if refresh or not (raw_path(p).exists() or p in skip)]
    if missing:
        # A fresh search pages through the history server; filling gaps left by an
        # interrupted run asks for the missing PMIDs by ID.
        if history is not None and len(missing) == len(pmids):
            documents = client.fetch_history(history)
        else:
            documents = client.fetch_ids(missing)
        wanted = set(missing)
        for document in documents:
            for pmid, xml in split_articles(document).items():
                if pmid in wanted:
                    write_atomic(raw_path(pmid), xml)
                    wanted.discard(pmid)
        fetched = len(missing) - len(wanted)
        # Kept across scopes, so switching scope and back does not ask for them again.
        unavailable = sorted(skip | wanted)
        saved = saved.model_copy(update={"unavailable": unavailable})
        write_atomic(search_path, saved.model_dump_json(indent=2).encode())
    else:
        fetched = 0

    available = [p for p in pmids if raw_path(p).exists()]
    records = [parse_record(raw_path(p).read_bytes()) for p in available]
    abstracts = [r for r in records if isinstance(r, Abstract)]
    drops = [r for r in records if isinstance(r, Dropped)]
    write_atomic(
        run_dir / "abstracts.jsonl",
        "".join(f"{a.model_dump_json()}\n" for a in abstracts).encode(),
    )

    languages_dropped: dict[DropReason, dict[str, int]] = {}
    for drop in drops:
        by_language = languages_dropped.setdefault(drop.reason, {})
        by_language[drop.language] = by_language.get(drop.language, 0) + 1
    manifest = RunManifest(
        retrieve=RetrieveManifest(
            question=config.id,
            scope=scope,
            term=terms[scope],
            scope_counts={match: search.count for match, search in saved.scopes.items()},
            date_range=describe_dates(config),
            searched_at=saved.searched_at,
            found=saved.scopes[scope].count,
            fetched=fetched,
            cached=len(available) - fetched,
            unavailable=[p for p in pmids if not raw_path(p).exists()],
            kept=len(abstracts),
            dropped=dict(sorted(Counter(d.reason for d in drops).items())),
            languages_kept=dict(sorted(Counter(a.language for a in abstracts).items())),
            languages_dropped={
                reason: dict(sorted(counts.items()))
                for reason, counts in sorted(languages_dropped.items())
            },
            requests=client.requests - requests_before,
            raw_bytes=sum(raw_path(p).stat().st_size for p in available),
        )
    )
    write_atomic(run_dir / "manifest.json", manifest.model_dump_json(indent=2).encode())
    return manifest


def _load_search(path: Path) -> SavedSearch | None:
    """The saved search, or None when there is none or it is in an older format."""
    if not path.exists():
        return None
    try:
        return SavedSearch.model_validate_json(path.read_text())
    except ValidationError:
        return None


def write_atomic(path: Path, data: bytes) -> None:
    # Write then rename, so an interrupted run never leaves a half-written cache file.
    partial = path.with_name(f"{path.name}.tmp")
    partial.write_bytes(data)
    partial.replace(path)


@contextmanager
def connect_eutils(env: Mapping[str, str]) -> Generator[EutilsClient]:
    identity = NcbiIdentity.from_env(env)
    with httpx.Client(timeout=60) as http:
        yield EutilsClient(http, identity, RateLimiter(identity.rate))
