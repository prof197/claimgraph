"""Recorded fixtures keep response bodies only, never how the client identified itself."""

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIXTURE_DIRS = [ROOT / "tests" / "fixtures"]
SECRET_NAMES = ("NCBI_EMAIL", "NCBI_API_KEY", "ANTHROPIC_API_KEY")
# Request parameters as they would appear in a recorded URL or form body.
IDENTITY_PARAM = re.compile(rb"(?<![A-Za-z_])(email|api_key|tool)=")


def fixture_files() -> list[Path]:
    return sorted(p for d in FIXTURE_DIRS for p in d.rglob("*") if p.is_file())


def local_secrets() -> list[str]:
    """Identity values from the environment and from a local .env, when either has them."""
    values = [os.environ.get(name, "") for name in SECRET_NAMES]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() in SECRET_NAMES:
                values.append(value.strip().strip("\"'"))
    return sorted({v for v in values if v})


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.name)
def test_fixture_has_no_identity_parameters(path: Path) -> None:
    assert not IDENTITY_PARAM.search(path.read_bytes())


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: p.name)
def test_fixture_has_no_local_identity_values(path: Path) -> None:
    content = path.read_bytes()

    assert not [v for v in local_secrets() if v.encode() in content]
