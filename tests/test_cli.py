from importlib.metadata import version

from typer.testing import CliRunner

from claimgraph.cli import app

runner = CliRunner()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"claimgraph {version('claimgraph')}"
