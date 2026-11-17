"""Committed replay files may hold responses for gold abstracts and the contradiction eval's
openly licensed pairs only.

Dev abstracts can carry any licence, so their text and their model responses stay in the
gitignored run directory. The outcome proposal is the one recording made over the whole corpus:
it holds the outcome names findings report and no abstract text, the same names the committed
mapping file lists. These tests fail if a committed replay file names a PMID outside
the gold set, either in a "pmid" field or anywhere as a known dev or screened PMID.
"""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
_PMID_FIELD = re.compile(r'"pmid"\s*:\s*"?(\d+)')
# A whole number standing alone, as a PMID appears in text: the same matches as searching for
# each known PMID between word boundaries, in one pass over a file.
_NUMBER = re.compile(r"\b\d+\b")


def _pair_pmids(gold_dir: Path, split: str) -> set[str]:
    return {
        finding.rsplit("-", 1)[0]
        for path in gold_dir.glob("*/contradiction_sample.json")
        for pair in json.loads(path.read_text())[split]
        for finding in (pair["finding_a"], pair["finding_b"])
    } | {
        study
        for path in gold_dir.glob("*/contradiction_sample.json")
        for pair in json.loads(path.read_text())[split]
        for study in (pair["study_a"], pair["study_b"])
    }


def gold_pmids(gold_dir: Path) -> set[str]:
    """The gold abstracts, and the eval pairs' papers: both openly licensed."""
    return {
        json.loads(line)["abstract"]["pmid"]
        for path in gold_dir.glob("*/gold.jsonl")
        for line in path.read_text().splitlines()
    } | _pair_pmids(gold_dir, "eval")


def known_pmids(gold_dir: Path) -> set[str]:
    """Every PMID the committed samples name: gold, dev, screened and the drawn pairs."""
    dev = {
        json.loads(line)["pmid"]
        for path in gold_dir.glob("*/dev.jsonl")
        for line in path.read_text().splitlines()
    }
    screened = {
        entry["pmid"]
        for path in gold_dir.glob("*/screening.toml")
        for entry in tomllib.loads(path.read_text()).get("record", [])
    }
    return gold_pmids(gold_dir) | dev | screened | _pair_pmids(gold_dir, "dev")


def non_gold_pmids_in_replay(replay_dir: Path, gold_dir: Path) -> set[str]:
    gold = gold_pmids(gold_dir)
    outside = known_pmids(gold_dir) - gold
    found: set[str] = set()
    for path in (p for p in replay_dir.rglob("*") if p.is_file()):
        text = path.read_text(errors="replace")
        found |= set(_PMID_FIELD.findall(text)) - gold
        found |= set(_NUMBER.findall(text)) & outside
    return found


def test_committed_replay_names_only_gold_pmids() -> None:
    assert non_gold_pmids_in_replay(ROOT / "replay", ROOT / "evals" / "gold") == set()


def write_samples(gold_dir: Path) -> None:
    question = gold_dir / "synthetic-question"
    question.mkdir(parents=True)
    (question / "gold.jsonl").write_text(json.dumps({"abstract": {"pmid": "999000001"}}) + "\n")
    (question / "dev.jsonl").write_text(json.dumps({"pmid": "999000002"}) + "\n")
    (question / "screening.toml").write_text(
        '[[record]]\npmid = "999000003"\non_topic = false\nnote = "Synthetic note."\n'
    )
    pair = {
        "study_a": "999000004",
        "study_b": "999000005",
        "finding_a": "999000004-1",
        "finding_b": "999000005-1",
    }
    dev = {
        "study_a": "999000006",
        "study_b": "999000007",
        "finding_a": "999000006-1",
        "finding_b": "999000007-1",
    }
    (question / "contradiction_sample.json").write_text(json.dumps({"eval": [pair], "dev": [dev]}))


def test_a_gold_response_passes(tmp_path: Path) -> None:
    write_samples(tmp_path / "gold")
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "synthetic.json").write_text('{"pmid": "999000001", "response": "Synthetic."}')

    assert non_gold_pmids_in_replay(replay, tmp_path / "gold") == set()


def test_a_dev_response_is_caught(tmp_path: Path) -> None:
    write_samples(tmp_path / "gold")
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "synthetic.json").write_text('{"pmid": "999000002", "response": "Synthetic."}')

    assert non_gold_pmids_in_replay(replay, tmp_path / "gold") == {"999000002"}


def test_a_known_non_gold_pmid_anywhere_in_a_file_is_caught(tmp_path: Path) -> None:
    write_samples(tmp_path / "gold")
    replay = tmp_path / "replay" / "nested"
    replay.mkdir(parents=True)
    (replay / "synthetic.txt").write_text("Synthetic prompt for PMID 999000003.")

    assert non_gold_pmids_in_replay(tmp_path / "replay", tmp_path / "gold") == {"999000003"}


def test_an_eval_pair_s_paper_passes_and_a_dev_pair_s_is_caught(tmp_path: Path) -> None:
    write_samples(tmp_path / "gold")
    replay = tmp_path / "replay"
    replay.mkdir()
    (replay / "eval.json").write_text('{"pmid": "999000004", "response": "Synthetic."}')
    (replay / "dev.txt").write_text("Synthetic request naming PMID 999000007.")

    assert non_gold_pmids_in_replay(replay, tmp_path / "gold") == {"999000007"}
