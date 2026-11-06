"""Gold and dev samples: openly licensed abstracts drawn from a question's retrieved corpus.

The extraction eval runs on the gold sample. The dev sample is drawn the same way and never
overlaps it; prompts are tuned and tokens measured on dev, so tuning never sees the eval.
"""

import random
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
from enum import StrEnum
from pathlib import Path
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from claimgraph.models import NonBlank, Pmcid, Pmid, Record
from claimgraph.retrieve import Abstract, EutilsClient, send_with_retries, write_atomic

# NLM's PMC Cloud Service: one world-readable S3 bucket, with a metadata object per article
# version that states its licence.
PMC_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"
# Licences under which the abstract text can be committed and shared with attribution.
OPEN_LICENCES = frozenset({"CC BY", "CC0"})
OPEN_FILTER = '"cc by license"[filter] OR "cc0 license"[filter]'
# PMC UIDs per licence-filter search, which keeps each query string a few kilobytes.
UID_BATCH = 200
SEED = 20261105
# Unscreened records listed per run of the sampler.
SCREENING_BATCH = 40


class Stratum(StrEnum):
    """A quota bucket: a design group from PubMed's publication types, or off topic."""

    TRIAL = "trial"
    OBSERVATIONAL = "observational"
    # Systematic reviews and meta-analyses.
    SYSTEMATIC_REVIEW = "systematic_review"
    REVIEW = "review"
    # Screened as reporting nothing in the outcome domain, whatever its design.
    OFF_TOPIC = "off_topic"


class Split(StrEnum):
    GOLD = "gold"
    DEV = "dev"


QUOTAS: dict[Stratum, int] = {
    Stratum.TRIAL: 18,
    Stratum.OBSERVATIONAL: 18,
    Stratum.SYSTEMATIC_REVIEW: 3,
    Stratum.REVIEW: 3,
    Stratum.OFF_TOPIC: 8,
}

_TRIAL_TYPES = frozenset(
    {
        "Randomized Controlled Trial",
        "Clinical Trial",
        "Controlled Clinical Trial",
        "Pragmatic Clinical Trial",
        "Equivalence Trial",
        "Adaptive Clinical Trial",
    }
)
# Case reports are excluded by default, and protocols report no results.
_NOT_SAMPLED_TYPES = frozenset({"Case Reports", "Clinical Trial Protocol"})


def stratum(publication_types: Iterable[str]) -> Stratum | None:
    """The design stratum a record's publication types put it in, or None if not sampled.

    Most observational studies carry no design type in PubMed, so a record with no review
    or trial type counts as observational. The hand labels record the actual design.
    """
    types = set(publication_types)
    if types & _NOT_SAMPLED_TYPES:
        return None
    if types & {"Systematic Review", "Meta-Analysis"}:
        return Stratum.SYSTEMATIC_REVIEW
    if types & {"Review", "Scoping Review"}:
        return Stratum.REVIEW
    if types & _TRIAL_TYPES or any(t.startswith("Clinical Trial, Phase") for t in types):
        return Stratum.TRIAL
    return Stratum.OBSERVATIONAL


# Licences


class PmcVersion(BaseModel):
    """The fields used from one article version's metadata object; the rest are ignored."""

    pmcid: Pmcid
    version: int
    # A Creative Commons code such as "CC BY", or "TDM" for text-mining-only manuscripts.
    license_code: str | None
    is_retracted: bool
    is_manuscript: bool


_VERSIONS = TypeAdapter(list[PmcVersion])


class SkipReason(StrEnum):
    NO_METADATA = "no_metadata"
    NOT_OPEN = "not_open"
    RETRACTED = "retracted"


class Licence(Record):
    pmcid: Pmcid
    code: NonBlank
    versions: list[int]


def licence(versions: Sequence[PmcVersion]) -> Licence | SkipReason:
    """The article's open licence, when every version PMC holds carries the same one."""
    if not versions:
        return SkipReason.NO_METADATA
    if any(v.is_retracted for v in versions):
        return SkipReason.RETRACTED
    codes = {v.license_code for v in versions}
    code = codes.pop() if len(codes) == 1 else None
    if code not in OPEN_LICENCES:
        return SkipReason.NOT_OPEN
    return Licence(pmcid=versions[0].pmcid, code=code, versions=sorted(v.version for v in versions))


class PmcBucket:
    """Reads licence metadata from the PMC Cloud Service, caching each article's on disk."""

    def __init__(
        self,
        http: httpx.Client,
        cache_dir: Path | None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._cache_dir = cache_dir
        self._sleep = sleep
        self.requests = 0

    def versions(self, pmcid: str) -> list[PmcVersion]:
        cached = None if self._cache_dir is None else self._cache_dir / f"{pmcid}.json"
        if cached is not None and cached.exists():
            return _VERSIONS.validate_json(cached.read_bytes())
        # The trailing dot keeps PMC123 from matching PMC1234's versions.
        listing = self._get("/", {"list-type": "2", "prefix": f"metadata/{pmcid}."})
        keys = [key.text or "" for key in ElementTree.fromstring(listing).iter(f"{_S3}Key")]
        versions = sorted(
            (PmcVersion.model_validate_json(self._get(f"/{key}", {})) for key in keys),
            key=lambda v: v.version,
        )
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(cached, _VERSIONS.dump_json(versions))
        return versions

    def _get(self, path: str, params: dict[str, str]) -> bytes:
        def send() -> httpx.Response:
            self.requests += 1
            return self._http.get(f"{PMC_BUCKET}{path}", params=params)

        return send_with_retries(send, f"PMC bucket {path}", self._sleep).content


def open_pmcids(client: EutilsClient, pmcids: Sequence[str]) -> set[str]:
    """The PMCIDs that PMC's licence filters list as CC BY or CC0."""
    uids = sorted(p.removeprefix("PMC") for p in pmcids)
    found: set[str] = set()
    for start in range(0, len(uids), UID_BATCH):
        ids = " OR ".join(f"{uid}[uid]" for uid in uids[start : start + UID_BATCH])
        found |= {
            f"PMC{uid}" for uid in client.search(f"({ids}) AND ({OPEN_FILTER})", db="pmc").pmids
        }
    return found


# Screening


class Screening(Record):
    """A reading of one abstract before sampling: does it report anything in the outcome domain?

    Screening only sorts records into quotas; the hand labels are authoritative.
    """

    pmid: Pmid
    on_topic: bool
    note: NonBlank


class _ScreeningFile(Record):
    record: list[Screening] = Field(default_factory=list[Screening])


def load_screening(path: Path) -> dict[str, Screening]:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        entries = _ScreeningFile.model_validate(tomllib.load(f)).record
    repeated = sorted(p for p, n in Counter(e.pmid for e in entries).items() if n > 1)
    if repeated:
        raise ValueError(f"screened more than once: {', '.join(repeated)}")
    return {e.pmid: e for e in entries}


# Drawing


class Pick(Record):
    abstract: Abstract
    design: Stratum
    slot: Stratum
    on_topic: bool
    # Checked for the gold draw only.
    licence: Licence | None


class Draw(Record):
    picks: list[Pick]
    # Records the walk reached that still need screening; the draw is final only when this
    # is empty.
    pending: list[Pmid]
    skipped: dict[SkipReason, int]
    # Places the pool could not fill, by slot.
    shortfall: dict[Stratum, int]


def draw(
    abstracts: Sequence[Abstract],
    screening: Mapping[str, Screening],
    *,
    seed: int,
    quotas: Mapping[Stratum, int] = QUOTAS,
    licence_of: Callable[[Abstract], Licence | SkipReason] | None = None,
    batch: int = SCREENING_BATCH,
) -> Draw:
    """Walk the records in a seeded order, taking each one that fits a quota with room.

    An on-topic record takes a place in its design's quota and an off-topic one a place in
    the off-topic quota, so off-topic records count inside the total. Licences are checked,
    and screening needed, only for records that could still take a place.
    """
    order = sorted(abstracts, key=lambda a: int(a.pmid))
    random.Random(seed).shuffle(order)
    room = dict(quotas)
    picks: list[Pick] = []
    pending: list[str] = []
    skipped: Counter[SkipReason] = Counter()

    for record in order:
        if len(pending) >= batch or not any(room.values()):
            break
        design = stratum(record.publication_types)
        if design is None or not (room[design] or room[Stratum.OFF_TOPIC]):
            continue
        found = None if licence_of is None else licence_of(record)
        if isinstance(found, SkipReason):
            skipped[found] += 1
            continue
        screened = screening.get(record.pmid)
        if screened is None:
            pending.append(record.pmid)
            continue
        slot = design if screened.on_topic else Stratum.OFF_TOPIC
        if not room[slot]:
            continue
        room[slot] -= 1
        picks.append(
            Pick(
                abstract=record,
                design=design,
                slot=slot,
                on_topic=screened.on_topic,
                licence=found,
            )
        )

    return Draw(
        picks=[] if pending else picks,
        pending=pending,
        skipped=dict(sorted(skipped.items())),
        shortfall={slot: n for slot, n in room.items() if n},
    )


# Reports


class Composition(Record):
    records: int
    by_design: dict[str, int]
    by_years: dict[str, int]


def year_band(year: int) -> str:
    if year < 2010:
        return "before 2010"
    if year >= 2025:
        return "2025 on"
    start = year - year % 5
    return f"{start}-{start + 4}"


def composition(abstracts: Sequence[Abstract]) -> Composition:
    """Counts by design stratum and year, to compare the open pool with the whole corpus."""
    designs = Counter(str(stratum(a.publication_types) or "not_sampled") for a in abstracts)
    years = Counter(year_band(a.year) for a in abstracts)
    return Composition(
        records=len(abstracts),
        by_design=dict(designs.most_common()),
        by_years=dict(sorted(years.items(), key=lambda item: (item[0] != "before 2010", item[0]))),
    )


class GoldAbstract(Record):
    """One gold abstract with its licence: a line of gold.jsonl."""

    abstract: Abstract
    licence: Licence
    design: Stratum
    slot: Stratum
    on_topic: bool


class DevEntry(Record):
    """A line of the committed dev.jsonl. Dev abstracts may carry any licence, so only the
    PMID is committed; the text is fetched again from PubMed and kept in the run directory."""

    pmid: Pmid
    design: Stratum
    slot: Stratum
    on_topic: bool


def attribution(gold: Sequence[GoldAbstract], checked_on: date) -> str:
    checked = f"{checked_on:%B} {checked_on.day}, {checked_on.year}"
    lines = [
        "# Attribution",
        "",
        "The abstracts in `gold.jsonl` are reproduced from the articles below, each under the",
        "licence its publisher gave it. Licences were read from the PubMed Central Cloud",
        f"Service metadata and PMC's licence search filters on {checked}.",
        "Changes: inline markup removed and whitespace collapsed; section labels are PubMed's.",
        "This file is generated by `claimgraph gold sample`.",
        "",
    ]
    for record in gold:
        a = record.abstract
        # PubMed's first author only; the article page lists them all.
        author = f"{a.first_author} (first author). " if a.first_author else ""
        lines.append(
            f"- {author}{a.title} *{a.journal}* {a.year}. "
            f"PMID [{a.pmid}](https://pubmed.ncbi.nlm.nih.gov/{a.pmid}/), "
            f"[{record.licence.pmcid}](https://pmc.ncbi.nlm.nih.gov/articles/"
            f"{record.licence.pmcid}/). Licence: {record.licence.code}."
        )
    return "\n".join(lines) + "\n"


# The sample stage


class _OpenPool(Record):
    """PMC's licence-filter answer for a set of PMCIDs, cached with the day it was asked."""

    checked_on: date
    queried: list[Pmcid]
    open: list[Pmcid]


class SampleManifest(Record):
    question: str
    seed: int
    # When the licence filters were read; licences can change after this.
    checked_on: date
    quotas: dict[Stratum, int]
    # Every kept abstract, and the CC BY and CC0 articles the gold sample is drawn from.
    # Dev is drawn from the whole corpus less the gold sample.
    corpus: Composition
    with_pmcid: int
    open_pool: Composition
    skipped: dict[SkipReason, int]
    splits: dict[Split, dict[Stratum, int]]


class Sample(Record):
    gold: Draw
    # Drawn once the gold draw is final, since it must not overlap it.
    dev: Draw | None

    @property
    def pending(self) -> list[str]:
        return self.gold.pending or ([] if self.dev is None else self.dev.pending)


def sample(
    question: str,
    abstracts: Sequence[Abstract],
    screening: Mapping[str, Screening],
    eutils: EutilsClient,
    bucket: PmcBucket,
    *,
    cache_dir: Path,
    seed: int,
    today: date,
) -> tuple[Sample, SampleManifest]:
    """Gold from the CC BY and CC0 articles; dev, by the same quotas, from everything else."""
    pmcids = sorted({a.pmcid for a in abstracts if a.pmcid is not None})
    found = _load_open_pool(cache_dir / "open.json", pmcids)
    if found is None:
        found = _OpenPool(
            checked_on=today, queried=pmcids, open=sorted(open_pmcids(eutils, pmcids))
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(cache_dir / "open.json", found.model_dump_json(indent=2).encode())
    open_ids = set(found.open)
    pool = [a for a in abstracts if a.pmcid in open_ids]

    def licence_of(record: Abstract) -> Licence | SkipReason:
        assert record.pmcid is not None
        return licence(bucket.versions(record.pmcid))

    gold = draw(pool, screening, seed=seed, quotas=QUOTAS, licence_of=licence_of)
    dev = None
    if not gold.pending:
        taken = {p.abstract.pmid for p in gold.picks}
        rest = [a for a in abstracts if a.pmid not in taken]
        dev = draw(rest, screening, seed=seed, quotas=QUOTAS)
    result = Sample(gold=gold, dev=dev)
    drawn = {Split.GOLD: gold, Split.DEV: dev}
    manifest = SampleManifest(
        question=question,
        seed=seed,
        checked_on=found.checked_on,
        quotas=QUOTAS,
        corpus=composition(abstracts),
        with_pmcid=len(pmcids),
        open_pool=composition(pool),
        skipped=gold.skipped,
        splits={
            split: dict(Counter(p.slot for p in d.picks).most_common())
            for split, d in drawn.items()
            if d is not None
        },
    )
    return result, manifest


def _load_open_pool(path: Path, pmcids: list[str]) -> _OpenPool | None:
    if not path.exists():
        return None
    cached = _OpenPool.model_validate_json(path.read_bytes())
    return cached if cached.queried == pmcids else None


def write_sample(out_dir: Path, run_dir: Path, result: Sample, manifest: SampleManifest) -> None:
    """Commit-ready files in out_dir; the dev abstracts' text only in the run directory."""
    assert result.dev is not None and not result.pending
    gold = [
        GoldAbstract(
            abstract=p.abstract,
            licence=p.licence,
            design=p.design,
            slot=p.slot,
            on_topic=p.on_topic,
        )
        for p in result.gold.picks
        if p.licence is not None
    ]
    dev = [
        DevEntry(pmid=p.abstract.pmid, design=p.design, slot=p.slot, on_topic=p.on_topic)
        for p in result.dev.picks
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    write_atomic(out_dir / "gold.jsonl", _lines(gold))
    write_atomic(out_dir / "dev.jsonl", _lines(dev))
    write_atomic(out_dir / "sample.json", manifest.model_dump_json(indent=2).encode() + b"\n")
    write_atomic(out_dir / "ATTRIBUTION.md", attribution(gold, manifest.checked_on).encode())
    (run_dir / "dev").mkdir(parents=True, exist_ok=True)
    write_atomic(
        run_dir / "dev" / "abstracts.jsonl", _lines([p.abstract for p in result.dev.picks])
    )


def _lines(records: Sequence[BaseModel]) -> bytes:
    return "".join(r.model_dump_json() + "\n" for r in records).encode()


def read_abstracts(path: Path) -> list[Abstract]:
    return [Abstract.model_validate_json(line) for line in path.read_text().splitlines()]


@contextmanager
def connect_bucket(cache_dir: Path) -> Generator[PmcBucket]:
    with httpx.Client(timeout=60) as http:
        yield PmcBucket(http, cache_dir)
