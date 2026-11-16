"""Publishing never calls gh here: a fake runner records the commands and plays the release."""

import hashlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from export_fixtures import minimal, synthetic

from claimgraph import publish
from claimgraph.publish import ExportLock, PublishError


class FakeGh:
    def __init__(self, release: bool, assets: Sequence[str] = (), served: bytes = b"") -> None:
        self.release, self.assets, self.served = release, list(assets), served
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        args = list(args)
        self.calls.append(args)
        ok, out = 0, ""
        if args[:2] == ["release", "view"] and "--json" not in args:
            ok = 0 if self.release else 1
        elif args[:2] == ["release", "create"]:
            self.release = True
        elif args[:2] == ["release", "view"]:
            out = "\n".join(self.assets)
        elif args[:2] == ["release", "upload"]:
            self.assets.append(Path(args[3]).name)
        elif args[:2] == ["release", "download"]:
            Path(args[args.index("--output") + 1]).write_bytes(self.served)
        return subprocess.CompletedProcess(args, ok, out, "synthetic gh error" if ok else "")

    def commands(self) -> list[str]:
        return [" ".join(c[:2]) for c in self.calls]


def text() -> str:
    return synthetic().model_dump_json(indent=2) + "\n"


def test_publishing_uploads_the_export_under_its_hash_and_pins_it(tmp_path: Path) -> None:
    fake = FakeGh(release=True)
    lock_path = tmp_path / "export.lock.json"

    lock = publish.publish(text(), tmp_path / "work", lock_path, run=fake)

    digest = hashlib.sha256(text().encode()).hexdigest()
    assert lock == ExportLock(
        question="synthetic-question",
        tag=publish.RELEASE_TAG,
        asset=f"synthetic-question-{digest[:12]}.json",
        sha256=digest,
    )
    assert fake.assets == [lock.asset]
    assert (tmp_path / "work" / lock.asset).read_text() == text()
    assert publish.read_lock(lock_path) == lock


def test_a_missing_release_is_created_first_and_never_marked_latest(tmp_path: Path) -> None:
    fake = FakeGh(release=False)

    publish.publish(text(), tmp_path, tmp_path / "lock.json", run=fake)

    create = next(c for c in fake.calls if c[:2] == ["release", "create"])
    assert "--latest=false" in create
    assert fake.commands().index("release create") < fake.commands().index("release upload")


def test_an_export_already_on_the_release_is_not_uploaded_again(tmp_path: Path) -> None:
    digest = hashlib.sha256(text().encode()).hexdigest()
    fake = FakeGh(release=True, assets=[publish.asset_name("synthetic-question", digest)])

    publish.publish(text(), tmp_path, tmp_path / "lock.json", run=fake)

    assert "release upload" not in fake.commands()


def test_stub_contradictions_are_never_published(tmp_path: Path) -> None:
    fake = FakeGh(release=True)

    with pytest.raises(PublishError, match="stubs"):
        publish.publish(minimal().model_dump_json(), tmp_path, tmp_path / "lock.json", run=fake)
    assert fake.calls == []
    assert not (tmp_path / "lock.json").exists()


def test_an_unreviewed_mapping_is_never_published(tmp_path: Path) -> None:
    export = synthetic()
    unreviewed = export.model_copy(
        update={"question": export.question.model_copy(update={"mapping_reviewed": False})}
    )

    with pytest.raises(PublishError, match="not reviewed"):
        publish.publish(
            unreviewed.model_dump_json(), tmp_path, tmp_path / "lock.json", run=FakeGh(True)
        )


def test_a_failed_upload_leaves_the_lock_alone(tmp_path: Path) -> None:
    def failing(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        code = 1 if list(args[:2]) == ["release", "upload"] else 0
        return subprocess.CompletedProcess(list(args), code, "", "synthetic gh error")

    with pytest.raises(PublishError, match="synthetic gh error"):
        publish.publish(text(), tmp_path, tmp_path / "lock.json", run=failing)
    assert not (tmp_path / "lock.json").exists()


def lock_for(content: bytes) -> ExportLock:
    digest = hashlib.sha256(content).hexdigest()
    return ExportLock(
        question="synthetic-question",
        tag=publish.RELEASE_TAG,
        asset=publish.asset_name("synthetic-question", digest),
        sha256=digest,
    )


def test_fetching_keeps_an_export_whose_hash_matches(tmp_path: Path) -> None:
    content = text().encode()
    out = tmp_path / "public" / "export.json"

    publish.fetch(lock_for(content), out, run=FakeGh(True, served=content))

    assert out.read_bytes() == content


def test_fetching_refuses_and_removes_an_export_whose_hash_differs(tmp_path: Path) -> None:
    out = tmp_path / "export.json"

    with pytest.raises(PublishError, match="not the pinned"):
        publish.fetch(lock_for(b"the pinned export"), out, run=FakeGh(True, served=b"another"))
    assert not out.exists()
