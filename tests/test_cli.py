from importlib.metadata import version

from typer.testing import CliRunner

from claimgraph.cli import app

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"claimgraph {version('claimgraph')}"


def test_recorded_only_is_refused_off_the_corpus() -> None:
    result = runner.invoke(
        app, ["classify", "vitamin-d-depression", "--split", "dev", "--recorded-only"]
    )

    assert result.exit_code == 1
    assert "--recorded-only takes --split corpus and --alike" in result.output
