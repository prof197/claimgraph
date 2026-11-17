"""Recorded fixtures and committed model recordings keep request and response bodies only,
never how the client identified itself."""

import os
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
FIXTURE_DIRS = [ROOT / "tests" / "fixtures", ROOT / "replay"]
SECRET_NAMES = (
    "NCBI_EMAIL",
    "NCBI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
)
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


# One pass over every file per check: the model recordings number in the thousands.


def test_no_fixture_has_identity_parameters() -> None:
    assert [p.name for p in fixture_files() if IDENTITY_PARAM.search(p.read_bytes())] == []


def test_no_fixture_has_local_identity_values() -> None:
    secrets = [v.encode() for v in local_secrets()]

    assert [p.name for p in fixture_files() if any(v in p.read_bytes() for v in secrets)] == []
