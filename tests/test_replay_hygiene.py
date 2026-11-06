"""Committed replay files may hold responses for gold abstracts only.

Dev abstracts can carry any licence, so their text and their model responses stay in the
gitignored run directory. These tests fail if a committed replay file names a PMID outside
the gold set, either in a "pmid" field or anywhere as a known dev or screened PMID.
"""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
_PMID_FIELD = re.compile(r'"pmid"\s*:\s*"?(\d+)')


def gold_pmids(gold_dir: Path) -> set[str]:
    return {
        json.loads(line)["abstract"]["pmid"]
        for path in gold_dir.glob("*/gold.jsonl")
        for line in path.read_text().splitlines()
    }


def known_pmids(gold_dir: Path) -> set[str]:
    """Every PMID the committed samples name: gold, dev and screened."""
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
    return gold_pmids(gold_dir) | dev | screened


def non_gold_pmids_in_replay(replay_dir: Path, gold_dir: Path) -> set[str]:
    gold = gold_pmids(gold_dir)
    outside = known_pmids(gold_dir) - gold
    found: set[str] = set()
    for path in (p for p in replay_dir.rglob("*") if p.is_file()):
        text = path.read_text(errors="replace")
        found |= set(_PMID_FIELD.findall(text)) - gold
        found |= {p for p in outside if re.search(rf"\b{p}\b", text)}
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
