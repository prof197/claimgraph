"""The local page for labeling the drawn contradiction pairs, and its check.

Every study, finding and label here is synthetic.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path

import pytest
from test_compare import paper
from test_labels import ROOT, VITAMIN_D, synthetic_abstract
from typer.testing import CliRunner

from claimgraph import contradiction_review, pair_sample
from claimgraph.contradiction_review import ContradictionSession, PairDraft
from claimgraph.extract import ExtractionResult
from claimgraph.models import Design, Effect, Reason, Verdict
from claimgraph.retrieve import Abstract
from claimgraph.review import Response

GUIDE = ROOT / "evals" / "gold" / "LABELING.md"
CONFIG_PATH = ROOT / "questions" / "vitamin-d-depression.toml"
FIXTURES = ROOT / "tests" / "fixtures" / "contradiction_review"
SAMPLE = ROOT / "tests" / "fixtures" / "pair_sample" / pair_sample.SAMPLE_FILE
OUTCOME = "Synthetic symptoms"
# The draw's eval pairs, as pair ids.
EVAL = [
    f"{OUTCOME}|999000101|999000102",
    f"{OUTCOME}|999000102|999000103",
    f"{OUTCOME}|999000103|999000105",
]


def synthetic_paper(
    pmid: str, effect: Effect, design: Design, size: int | None, status: str
) -> ExtractionResult:
    base = paper(pmid, ("synthetic mood score", effect), design=design)
    assert base.study is not None
    finding = base.study.findings[0].model_copy(
        update={
            "claim": f"Synthetic claim of study {pmid}.",
            "population": f"synthetic adults of study {pmid}",
            "dimensions": {"baseline_vitamin_d_status": status},
        }
    )
    study = base.study.model_copy(update={"findings": [finding], "sample_size": size})
    return base.model_copy(update={"study": study})


RESULTS = [
    synthetic_paper("999000101", Effect.DECREASED, Design.RCT, 120, "below_20_ng_ml"),
    synthetic_paper("999000102", Effect.NO_CHANGE, Design.RCT, 40, "at_or_above_20_ng_ml"),
    synthetic_paper("999000103", Effect.DECREASED, Design.COHORT, None, "not_reported"),
    synthetic_paper("999000105", Effect.NO_CHANGE, Design.CROSS_SECTIONAL, 900, "mixed"),
    synthetic_paper("999000110", Effect.DECREASED, Design.RCT, 60, "mixed"),
    synthetic_paper("999000111", Effect.NO_CHANGE, Design.RCT, 70, "mixed"),
]


def abstracts() -> list[Abstract]:
    return [
        synthetic_abstract(r.pmid).model_copy(update={"year": 2000 + n})
        for n, r in enumerate(RESULTS)
    ]


def gold_dir(tmp_path: Path, labels: Path | None = None) -> Path:
    """A gold directory holding the synthetic draw, and the template or the given labels."""
    out_dir = tmp_path / "gold" / VITAMIN_D.id
    out_dir.mkdir(parents=True)
    shutil.copy(SAMPLE, out_dir / pair_sample.SAMPLE_FILE)
    shutil.copy(GUIDE, tmp_path / "gold" / "LABELING.md")
    if labels is None:
        contradiction_review.write_template(out_dir, pair_sample.read_sample(SAMPLE))
    else:
        shutil.copy(labels, out_dir / contradiction_review.LABEL_FILE)
    return out_dir


def session(tmp_path: Path, labels: Path | None = None) -> ContradictionSession:
    out_dir = gold_dir(tmp_path, labels)
    return ContradictionSession(out_dir, VITAMIN_D, RESULTS, abstracts(), GUIDE)


def draft(pair_id: str, **fields: object) -> PairDraft:
    outcome, a, b = pair_id.split("|")
    return PairDraft.model_validate({"outcome": outcome, "study_a": a, "study_b": b} | fields)


# The label file


def test_the_template_lists_every_eval_pair_once_blank_and_shuffled(tmp_path: Path) -> None:
    out_dir = gold_dir(tmp_path)

    drafts = contradiction_review.load(out_dir / contradiction_review.LABEL_FILE)

    assert sorted(d.pair_id for d in drafts) == EVAL
    assert [d.pair_id for d in drafts] != EVAL
    assert not any(d.verdict or d.reason or d.also_differs or d.detail for d in drafts)
    # The dev pairs are for tuning, never labeled here.
    assert "999000110" not in (out_dir / contradiction_review.LABEL_FILE).read_text()


def test_the_template_never_overwrites(tmp_path: Path) -> None:
    out_dir = gold_dir(tmp_path)

    with pytest.raises(FileExistsError):
        contradiction_review.write_template(out_dir, pair_sample.read_sample(SAMPLE))


def test_a_rendered_file_reads_back_as_written(tmp_path: Path) -> None:
    drafts = [
        draft(EVAL[0], verdict="genuine", reason="population", also_differs=["comparator"]),
        draft(EVAL[1], verdict="apparent", detail='synthetic "quoted" detail', note="a\nb"),
        draft(EVAL[2]),
    ]
    path = tmp_path / contradiction_review.LABEL_FILE

    contradiction_review.write(path, "synthetic-question", drafts)

    assert contradiction_review.load(path) == drafts


def test_blank_text_is_no_text() -> None:
    assert draft(EVAL[0], detail="  ", note="").detail is None
    assert draft(EVAL[0], note="").note is None


def test_also_differs_never_repeats_the_reason_or_names_unclear() -> None:
    with pytest.raises(ValueError, match="primary reason"):
        draft(EVAL[0], reason="population", also_differs=["population"])
    with pytest.raises(ValueError, match="unclear"):
        draft(EVAL[0], also_differs=["unclear"])


def test_an_off_question_verdict_labels_a_pair_without_a_reason() -> None:
    off = draft(EVAL[0], verdict="off_question")

    assert off.labeled
    assert contradiction_review.label_problems(off) == []
    assert not draft(EVAL[0], verdict="genuine").labeled
    with_reason = draft(EVAL[0], verdict="off_question", reason="population")
    assert contradiction_review.label_problems(with_reason) == [
        "an off_question pair takes no reason and no also_differs"
    ]


# Checking


def test_the_check_passes_a_completed_file() -> None:
    report = contradiction_review.check(pair_sample.read_sample(SAMPLE), FIXTURES / "complete.toml")

    assert report.problems == []
    assert report.verdicts == {Verdict.GENUINE: 2, Verdict.APPARENT: 1, Verdict.OFF_QUESTION: 0}
    assert (report.drawn, report.unlabeled) == (3, 0)


def test_the_check_names_every_problem_of_a_bad_file() -> None:
    report = contradiction_review.check(pair_sample.read_sample(SAMPLE), FIXTURES / "bad.toml")

    assert len(report.problems) == 4
    assert report.problems[0].startswith("pair 1: reason:")
    assert report.problems[1].startswith("pair 2:")
    assert "names the reason the conflict dissolves" in report.problems[1]
    assert report.problems[2] == (
        f"pair 3: {OUTCOME}|999000101|999000104 is not an eval pair of the draw"
    )
    assert report.problems[3] == f"1 drawn pairs are not in the file: {EVAL[1]}"


def test_the_check_counts_blank_pairs_until_both_clicks_are_made(tmp_path: Path) -> None:
    page = session(tmp_path)
    page.save(draft(EVAL[0], verdict="genuine"))

    report = contradiction_review.check(page.sample, page.path)

    assert report.unlabeled == 3
    assert report.problems == ["3 of 3 pairs are not labeled yet"]


def test_the_check_finds_a_repeat_and_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / contradiction_review.LABEL_FILE
    text = (FIXTURES / "complete.toml").read_text()
    path.write_text(text + text.split("\n\n", 2)[1])

    repeated = contradiction_review.check(pair_sample.read_sample(SAMPLE), path)
    missing = contradiction_review.check(pair_sample.read_sample(SAMPLE), tmp_path / "none")

    assert repeated.problems == [f"pair 4: {EVAL[0]} is labeled more than once"]
    assert "No such file" in missing.problems[0]


# The page


def test_the_rules_are_the_guide_s_contradiction_section(tmp_path: Path) -> None:
    rules = session(tmp_path).state().rules

    guide = GUIDE.read_text()
    assert rules == guide.split("\n## Contradiction pairs\n", 1)[1].split("\n## ", 1)[0].strip()
    assert "\n## " not in rules


def test_the_page_shows_both_findings_as_extracted(tmp_path: Path) -> None:
    state = session(tmp_path).state()
    pair = next(p for p in state.pairs if p.pair_id == EVAL[1])

    assert (pair.outcome, pair.kind) == (OUTCOME, "effect_vs_none")
    assert (pair.a.study, pair.a.pmid, pair.a.finding_id) == (
        "999000102",
        "999000102",
        "999000102-1",
    )
    assert pair.a.claim == "Synthetic claim of study 999000102."
    assert pair.b.population == "synthetic adults of study 999000103"
    assert (pair.a.design, pair.b.design) == (Design.RCT, Design.COHORT)
    assert (pair.a.sample_size, pair.b.sample_size) == (40, None)
    assert (pair.a.year, pair.b.year) == (2001, 2002)
    assert pair.a.dimensions == {"baseline_vitamin_d_status": "at_or_above_20_ng_ml"}
    assert pair.b.dimensions == {"baseline_vitamin_d_status": "not reported"}
    assert pair.a.quote and pair.a.intervention and pair.a.comparator and pair.a.outcome_reported


def test_the_page_lists_the_pairs_in_the_file_s_order_with_no_classification(
    tmp_path: Path,
) -> None:
    page = session(tmp_path)
    state = json.loads(page.state().model_dump_json())

    assert [p["pair_id"] for p in state["pairs"]] == [
        d.pair_id for d in contradiction_review.load(page.path)
    ]
    assert set(state) == {
        "question",
        "title",
        "verdicts",
        "reasons",
        "dimensions",
        "rules",
        "pairs",
    }
    assert set(state["pairs"][0]) == {"pair_id", "outcome", "kind", "a", "b", "label", "problems"}
    assert "rationale" not in json.dumps(state)
    assert state["reasons"] == [r.value for r in Reason]


def test_a_finding_missing_from_the_extraction_is_named(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="999000105-1"):
        ContradictionSession(gold_dir(tmp_path), VITAMIN_D, RESULTS[:3], abstracts(), GUIDE)


def test_a_save_writes_the_pair_and_keeps_every_other(tmp_path: Path) -> None:
    page = session(tmp_path)
    before = {d.pair_id: d for d in contradiction_review.load(page.path)}
    label = draft(
        EVAL[2], verdict="genuine", reason="population", also_differs=["intervention"], detail="x"
    )

    saved = page.save(label)

    after = {d.pair_id: d for d in contradiction_review.load(page.path)}
    assert after[EVAL[2]] == label
    assert all(after[p] == before[p] for p in EVAL if p != EVAL[2])
    assert (saved.labeled, saved.total, saved.problems) == (1, 3, [])
    assert saved.verdicts == {Verdict.GENUINE: 1, Verdict.APPARENT: 0, Verdict.OFF_QUESTION: 0}


def test_an_apparent_pair_saves_without_a_detail(tmp_path: Path) -> None:
    page = session(tmp_path)

    page.save(draft(EVAL[0], verdict="apparent", reason="design_or_bias"))

    saved = {d.pair_id: d for d in contradiction_review.load(page.path)}[EVAL[0]]
    assert (saved.verdict, saved.reason, saved.detail) == (
        Verdict.APPARENT,
        Reason.DESIGN_OR_BIAS,
        None,
    )


def test_a_label_the_gold_model_refuses_is_saved_and_its_problem_shown(tmp_path: Path) -> None:
    page = session(tmp_path)

    saved = page.save(draft(EVAL[0], verdict="apparent", reason="unclear", detail="x"))

    assert saved.problems and "dissolves" in saved.problems[0]
    shown = next(p for p in page.state().pairs if p.pair_id == EVAL[0])
    assert shown.problems == saved.problems


def test_saves_at_once_all_land(tmp_path: Path) -> None:
    page = session(tmp_path)
    threads = [
        threading.Thread(target=page.save, args=(draft(p, verdict="genuine", reason="unclear"),))
        for p in EVAL
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(d.labeled for d in contradiction_review.load(page.path))


def post(
    page: ContradictionSession, body: object, content_type: str = "application/json"
) -> Response:
    return contradiction_review.handle(
        page, "POST", "/api/label", json.dumps(body).encode(), content_type
    )


def body(pair_id: str, **fields: object) -> dict[str, object]:
    outcome, a, b = pair_id.split("|")
    blank: dict[str, object] = {"verdict": "", "reason": "", "also_differs": [], "detail": ""}
    return {"outcome": outcome, "study_a": a, "study_b": b, "note": ""} | blank | fields


def test_requests(tmp_path: Path) -> None:
    page = session(tmp_path)

    html = contradiction_review.handle(page, "GET", "/", b"", "")
    state = contradiction_review.handle(page, "GET", "/api/state", b"", "")
    saved = post(page, body(EVAL[0], verdict="genuine", reason="comparator"))
    form = post(page, body(EVAL[0], verdict="apparent"), "text/plain")
    unknown = post(page, body(f"{OUTCOME}|999000101|999000104", verdict="genuine"))
    invalid = post(page, body(EVAL[0], reason="dose"))
    repeated = post(page, body(EVAL[0], reason="comparator", also_differs=["comparator"]))
    missing = contradiction_review.handle(page, "GET", "/api/classifications", b"", "")

    assert html.status == HTTPStatus.OK
    assert b"<title>Contradiction pairs</title>" in html.body
    assert state.status == HTTPStatus.OK
    assert saved.status == HTTPStatus.OK
    assert form.status == HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    assert unknown.status == HTTPStatus.NOT_FOUND
    assert invalid.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert repeated.status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert missing.status == HTTPStatus.NOT_FOUND
    labels = {d.pair_id: d for d in contradiction_review.load(page.path)}
    assert (labels[EVAL[0]].verdict, labels[EVAL[0]].reason) == ("genuine", "comparator")


def test_a_file_broken_by_hand_is_never_rewritten(tmp_path: Path) -> None:
    page = session(tmp_path)
    broken = page.path.read_text().replace('verdict = ""', 'verdict = "maybe"', 1)
    page.path.write_text(broken)

    state = contradiction_review.handle(page, "GET", "/api/state", b"", "")
    saved = post(page, body(EVAL[1], verdict="genuine"))

    assert state.status == HTTPStatus.CONFLICT
    assert saved.status == HTTPStatus.CONFLICT
    assert b"Fix it by hand" in state.body
    assert page.path.read_text() == broken


def test_a_file_missing_an_eval_pair_is_refused(tmp_path: Path) -> None:
    page = session(tmp_path)
    kept = [d for d in contradiction_review.load(page.path) if d.pair_id != EVAL[0]]
    contradiction_review.write(page.path, "synthetic-question", kept)

    assert contradiction_review.handle(page, "GET", "/api/state", b"", "").status == (
        HTTPStatus.CONFLICT
    )


def test_the_page_has_an_unlabeled_filter_shortcuts_and_reads_no_classification() -> None:
    html = contradiction_review.page().decode()

    assert 'data-filter="unlabeled"' in html
    assert 'document.addEventListener("keydown"' in html
    assert 'if (ev.target.tagName === "INPUT")' in html
    assert "classif" not in html.replace("any classifier's verdict", "")


# Commands


def write_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / VITAMIN_D.id
    (run_dir / "extract").mkdir(parents=True)
    lines = "".join(r.model_dump_json() + "\n" for r in RESULTS)
    (run_dir / "extract" / "corpus-sonnet.jsonl").write_text(lines)
    (run_dir / "abstracts.jsonl").write_text(
        "".join(a.model_dump_json() + "\n" for a in abstracts())
    )
    return run_dir


def invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(contradiction_review.app, list(args))
    return result.exit_code, result.output


def test_the_check_command_passes_a_completed_file_and_fails_a_bad_one(tmp_path: Path) -> None:
    gold_dir(tmp_path / "done", FIXTURES / "complete.toml")
    gold_dir(tmp_path / "bad", FIXTURES / "bad.toml")

    done = invoke("check", str(CONFIG_PATH), "--gold-dir", str(tmp_path / "done" / "gold"))
    bad = invoke("check", str(CONFIG_PATH), "--gold-dir", str(tmp_path / "bad" / "gold"))

    assert done == (0, "3 of 3 pairs labeled: 2 genuine, 1 apparent, 0 off_question\n")
    assert bad[0] == 1
    assert "is not an eval pair of the draw" in bad[1]


def test_the_template_command_writes_once(tmp_path: Path) -> None:
    out_dir = tmp_path / "gold" / VITAMIN_D.id
    out_dir.mkdir(parents=True)
    shutil.copy(SAMPLE, out_dir / pair_sample.SAMPLE_FILE)
    args = ("template", str(CONFIG_PATH), "--gold-dir", str(tmp_path / "gold"))

    first = invoke(*args)
    again = invoke(*args)

    assert first[0] == 0
    assert again[0] == 1
    assert "not overwritten" in again[1]


def test_the_commands_need_a_draw(tmp_path: Path) -> None:
    code, output = invoke("check", str(CONFIG_PATH), "--gold-dir", str(tmp_path))

    assert code == 1
    assert "no drawn pairs" in output


def test_the_review_command_needs_a_label_file(tmp_path: Path) -> None:
    out_dir = tmp_path / "gold" / VITAMIN_D.id
    out_dir.mkdir(parents=True)
    shutil.copy(SAMPLE, out_dir / pair_sample.SAMPLE_FILE)

    code, output = invoke(
        "review", str(CONFIG_PATH), "--gold-dir", str(tmp_path / "gold"), "--no-open"
    )

    assert code == 1
    assert "template first" in output


def test_the_review_command_serves_the_page_on_a_free_port(tmp_path: Path) -> None:
    gold_dir(tmp_path)
    write_run(tmp_path)
    command = [
        sys.executable,
        "-c",
        "from claimgraph.contradiction_review import app; app()",
        "review",
        str(CONFIG_PATH),
        "--gold-dir",
        str(tmp_path / "gold"),
        "--runs-dir",
        str(tmp_path / "runs"),
        "--port",
        "0",
        "--no-open",
    ]
    env = os.environ | {"PYTHONUNBUFFERED": "1"}
    with subprocess.Popen(command, stdout=subprocess.PIPE, text=True, env=env) as server:
        try:
            assert server.stdout is not None
            line = server.stdout.readline()
            assert line.startswith("labeling page at http://127.0.0.1:")
            url = line.split()[3]
            with urllib.request.urlopen(url + "api/state", timeout=10) as reply:
                state = json.loads(reply.read())
            request = urllib.request.Request(
                url + "api/label",
                data=json.dumps(body(EVAL[0], verdict="genuine", reason="unclear")).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as reply:
                saved = json.loads(reply.read())
            elsewhere = urllib.request.Request(url, headers={"Host": "synthetic.example"})
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(elsewhere, timeout=10)
        finally:
            server.send_signal(signal.SIGINT)
            server.wait(timeout=10)

    assert len(state["pairs"]) == 3
    assert saved["labeled"] == 1
    assert refused.value.code == HTTPStatus.FORBIDDEN
