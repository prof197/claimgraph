"""Shipping the hosted page's export as a GitHub release asset, never as a committed file.

Most abstracts the page quotes are under publisher copyright, and committed text comes only
from openly licensed articles, so the export stays out of git history. The repository commits
the asset's name and SHA-256 in the lock file; the deploy downloads that asset and refuses it
when the hash differs. An asset's name carries its hash, so every commit keeps pointing at the
export it was published with.
"""

import hashlib
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

from pydantic import StringConstraints

from claimgraph.export import Export
from claimgraph.models import NonBlank, Record
from claimgraph.retrieve import write_atomic

LOCK_FILE = Path("site/export.lock.json")
RELEASE_TAG = "exports"
RELEASE_NOTES = (
    "The page's data, one JSON export per publish. Each asset's name carries the start of its "
    "SHA-256, and site/export.lock.json pins the one the page is built from."
)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class ExportLock(Record):
    question: NonBlank
    tag: NonBlank
    asset: NonBlank
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class PublishError(Exception):
    pass


def gh(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def _checked(run: Runner, args: Sequence[str]) -> str:
    done = run(args)
    if done.returncode != 0:
        raise PublishError(f"gh {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def hostable(export: Export) -> list[str]:
    """Why the export cannot be hosted; empty when it can."""
    problems: list[str] = []
    if not export.question.mapping_reviewed:
        problems.append("the outcome mapping is not reviewed")
    if export.method.depth is None:
        problems.append("its contradictions are stubs, not classified pairs")
    return problems


def asset_name(question: str, sha256: str) -> str:
    return f"{question}-{sha256[:12]}.json"


def read_lock(path: Path) -> ExportLock:
    return ExportLock.model_validate_json(path.read_text())


def publish(
    text: str, work_dir: Path, lock_path: Path, run: Runner = gh, tag: str = RELEASE_TAG
) -> ExportLock:
    """Upload the export `text` to the release and pin it in the lock file."""
    export = Export.model_validate_json(text)
    problems = hostable(export)
    if problems:
        raise PublishError("not hostable: " + "; ".join(problems))
    digest = hashlib.sha256(text.encode()).hexdigest()
    lock = ExportLock(
        question=export.question.id,
        tag=tag,
        asset=asset_name(export.question.id, digest),
        sha256=digest,
    )
    if run(["release", "view", tag]).returncode != 0:
        # Not the latest release: package releases keep that place.
        _checked(
            run,
            [
                "release",
                "create",
                tag,
                "--title",
                "Page data",
                "--notes",
                RELEASE_NOTES,
                "--latest=false",
            ],
        )
    names = _checked(run, ["release", "view", tag, "--json", "assets", "--jq", ".assets[].name"])
    # The name carries the hash, so an asset already there is this export.
    if lock.asset not in names.split():
        path = work_dir / lock.asset
        work_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(path, text.encode())
        _checked(run, ["release", "upload", tag, str(path)])
    write_atomic(lock_path, (lock.model_dump_json(indent=2) + "\n").encode())
    return lock


def fetch(lock: ExportLock, out: Path, run: Runner = gh) -> None:
    """Download the pinned export to `out`, refusing it unless its hash matches the lock."""
    out.parent.mkdir(parents=True, exist_ok=True)
    _checked(
        run,
        [
            "release",
            "download",
            lock.tag,
            "--pattern",
            lock.asset,
            "--output",
            str(out),
            "--clobber",
        ],
    )
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    if digest != lock.sha256:
        out.unlink()
        raise PublishError(f"{lock.asset} has SHA-256 {digest}, not the pinned {lock.sha256}")
