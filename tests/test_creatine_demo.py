"""The creatine-cognition CLI demo end to end at fixture scale: retrieve from recorded PubMed
XML, extract and normalize from recorded responses and compare.

Every record is synthetic, and nothing is sent: E-utilities is a mock transport, and each model
output in tests/fixtures/creatine is recorded under the key of the current request before the
command replays it.
"""

import json
import re
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner, Result

from claimgraph import cli, extract, gold, normalize
from claimgraph.config import load_config
from claimgraph.extract import ExtractionResult
from claimgraph.llm import ModelClient
from claimgraph.retrieve import EutilsClient, NcbiIdentity, RateLimiter, split_articles

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "creatine"
QUESTION = "creatine-cognition"
CONFIG = load_config(ROOT / "questions" / f"{QUESTION}.toml")
MODEL = extract.DEFAULT_MODEL
SEARCHED = [f"99930000{n}" for n in range(1, 7)]


class Pubmed:
    """E-utilities serving the synthetic records, logging every request it receives."""

    def __init__(self) -> None:
        self.document = (FIXTURES / "efetch.xml").read_bytes()
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/esearch.fcgi"):
            result = {
                "count": str(len(SEARCHED)),
                "idlist": SEARCHED,
                "webenv": "SYNTHETIC_WEBENV",
                "querykey": "1",
            }
            return httpx.Response(200, json={"esearchresult": result})
        return httpx.Response(200, content=self.document)


@dataclass
class Demo:
    runs: Path
    replay: Path
    mappings: Path
    pubmed: Pubmed

    @property
    def run_dir(self) -> Path:
        return self.runs / QUESTION

    def invoke(self, *args: str) -> Result:
        return CliRunner().invoke(cli.app, list(args))

    def run(self, stage: str, *args: str) -> Result:
        return self.invoke(
            "run",
            QUESTION,
            "--stage",
            stage,
            "--runs-dir",
            str(self.runs),
            "--replay-dir",
            str(self.replay),
            "--mappings-dir",
            str(self.mappings),
            *args,
        )


def make_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Demo:
    """A demo in tmp_path, with E-utilities served from the synthetic records."""
    pubmed = Pubmed()

    @contextmanager
    def connect(env: object) -> Generator[EutilsClient]:
        identity = NcbiIdentity(tool="claimgraph", email=None, api_key=None)
        yield EutilsClient(
            httpx.Client(transport=httpx.MockTransport(pubmed)),
            identity,
            RateLimiter(1000, sleep=lambda _: None),
            sleep=lambda _: None,
        )

    monkeypatch.setattr(cli, "connect_eutils", connect)
    # Question ids, the price file and the labeling guide resolve from the repository root.
    monkeypatch.chdir(ROOT)
    return Demo(tmp_path / "runs", tmp_path / "replay", tmp_path / "mappings", pubmed)


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Demo:
    return make_demo(tmp_path, monkeypatch)


def response(text: str) -> dict[str, Any]:
    """A Messages API response carrying one fixture output, with synthetic token counts."""
    return {
        "id": "msg_synthetic",
        "model": "synthetic",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 900, "output_tokens": 300},
    }


def record_extractions(demo: Demo) -> None:
    client = ModelClient(demo.run_dir / "replay" / "extraction" / MODEL.value)
    for abstract in gold.read_abstracts(demo.run_dir / "abstracts.jsonl"):
        text = (FIXTURES / "extraction" / f"{abstract.pmid}.json").read_text()
        client.record(extract.request(abstract, CONFIG, MODEL), response(text))


def corpus(demo: Demo) -> list[ExtractionResult]:
    path = demo.run_dir / "extract" / f"corpus-{MODEL.value}.jsonl"
    return [ExtractionResult.model_validate_json(line) for line in path.read_text().splitlines()]


def record_proposal(demo: Demo) -> None:
    strings = normalize.outcome_strings(corpus(demo))
    client = ModelClient(demo.replay / QUESTION / "normalization" / MODEL.value)
    text = (FIXTURES / "normalization.json").read_text()
    client.record(normalize.request(strings, CONFIG, MODEL), response(text))


def through(demo: Demo, last: str) -> None:
    """Run the demo's stages up to and including `last`, each from its recordings."""
    for stage in ("retrieve", "extract", "normalize", "compare"):
        if stage == "extract":
            record_extractions(demo)
        if stage == "normalize":
            record_proposal(demo)
        result = demo.run(stage)
        assert result.exit_code == 0, result.output
        if stage == last:
            return


def lines(result: Result) -> list[str]:
    return result.output.splitlines()


# Retrieve


def test_retrieve_keeps_the_records_with_an_abstract(demo: Demo) -> None:
    result = demo.run("retrieve")

    assert result.exit_code == 0, result.output
    assert "6 found" in result.output
    assert "5 kept; dropped: 1 no_abstract" in result.output
    pmids = [a.pmid for a in gold.read_abstracts(demo.run_dir / "abstracts.jsonl")]
    assert pmids == SEARCHED[:5]
    assert all(r.url.host == "eutils.ncbi.nlm.nih.gov" for r in demo.pubmed.requests)


def test_retrieve_searches_the_creatine_query(demo: Demo) -> None:
    demo.run("retrieve")

    terms = [r.url.params["term"] for r in demo.pubmed.requests if "term" in r.url.params]
    assert terms
    assert all('"creatine"' in t and "cogniti*" in t and "supplement*" in t for t in terms)
    assert not any("vitamin" in t.casefold() for t in terms)


# Extract


def test_extract_dry_run_estimates_every_abstract_and_sends_nothing(demo: Demo) -> None:
    through(demo, "retrieve")

    result = demo.run("extract", "--dry-run")

    assert result.exit_code == 0, result.output
    estimate = r"dry run: 5 abstracts to send to claude-sonnet-5-5, about \d+ input tokens"
    assert re.search(estimate, result.output)
    assert not (demo.run_dir / "extract").exists()


def test_extract_replays_and_misses_without_a_recording(demo: Demo) -> None:
    through(demo, "retrieve")

    result = demo.run("extract")

    assert result.exit_code == 1
    assert "5 extracted" not in result.output
    assert "missing: 999300001" in result.output


def test_extract_replays_the_recorded_outputs(demo: Demo) -> None:
    through(demo, "extract")

    results = corpus(demo)
    assert [r.pmid for r in results] == SEARCHED[:5]
    findings = [f for r in results if r.study is not None for f in r.study.findings]
    assert len(findings) == 7
    assert {f.domain_outcome for f in findings} == {"memory", "attention", "processing speed"}
    studies = {r.pmid: r.study for r in results if r.study is not None}
    assert studies["999300005"].findings == []
    assert studies["999300001"].outside_domain == 1
    manifest = json.loads((demo.run_dir / "extract" / "manifest.json").read_text())
    assert manifest["runs"]["corpus-sonnet"]["findings"] == 7


# Normalize


def test_normalize_dry_run_counts_the_strings_and_sends_nothing(demo: Demo) -> None:
    through(demo, "extract")

    result = demo.run("normalize", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "dry run: 7 outcome strings to send to claude-sonnet-5-5 in one request" in (
        result.output
    )
    assert "not recorded yet" in result.output
    assert not (demo.mappings / f"{QUESTION}.toml").exists()


def test_normalize_writes_the_proposal_unreviewed(demo: Demo) -> None:
    through(demo, "normalize")

    mapping = normalize.load_mapping(demo.mappings / f"{QUESTION}.toml")
    assert not mapping.reviewed
    assert {o.name: len(o.members) for o in mapping.outcomes} == {
        "memory": 3,
        "attention": 2,
        "processing speed": 2,
    }


def test_the_unreviewed_mapping_fails_the_hosted_check(demo: Demo) -> None:
    through(demo, "normalize")

    result = demo.invoke(
        "mapping",
        "check",
        QUESTION,
        "--runs-dir",
        str(demo.runs),
        "--mappings-dir",
        str(demo.mappings),
    )

    assert result.exit_code == 1
    assert "3 outcomes, 0 reviewed; 7 strings, 0 unmapped" in result.output


# Compare


def test_compare_finds_the_pairs_and_labels_the_mapping_unreviewed(demo: Demo) -> None:
    through(demo, "normalize")

    result = demo.run("compare")

    assert result.exit_code == 0, result.output
    assert lines(result)[:2] == [
        "3 candidate pairs from 4 studies on 3 outcomes; 0 findings dropped as out of domain",
        "from an unreviewed mapping, which the hosted build refuses",
    ]
    assert "     2 pairs   3 studies  memory" in result.output
    assert "     1 pairs   2 studies  attention" in result.output
    assert "     0 pairs   2 studies  processing speed" in result.output


def test_the_fixture_holds_every_searched_record() -> None:
    assert split_articles((FIXTURES / "efetch.xml").read_bytes()).keys() == set(SEARCHED)
