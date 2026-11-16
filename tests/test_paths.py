from pathlib import Path

import pytest

from claimgraph import paths, review


def test_the_data_lives_in_the_working_directory_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(paths.DATA_ENV, raising=False)

    assert paths.runs_dir() == Path("runs")
    assert review.page_dir() == Path("local-data/eval/review")


def test_an_absolute_data_root_moves_runs_and_local_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(paths.DATA_ENV, str(tmp_path))

    assert paths.runs_dir() == tmp_path / "runs"
    assert paths.local_dir() == tmp_path / "local-data"
    assert review.page_dir() == tmp_path / "local-data" / "eval" / "review"


def test_a_relative_data_root_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.DATA_ENV, "shared")

    with pytest.raises(ValueError, match="absolute"):
        paths.data_root()
