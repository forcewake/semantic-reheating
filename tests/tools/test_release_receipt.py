from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.release_receipt import ReceiptError, write_release_receipt


class Runner:
    def __init__(
        self,
        *,
        dirty: bool = False,
        mismatch: bool = False,
        missing_readback: bool = False,
        origin_url: str = "https://github.com/example/public-repo.git",
    ) -> None:
        self.dirty, self.mismatch, self.missing_readback = (
            dirty,
            mismatch,
            missing_readback,
        )
        self.origin_url = origin_url

    def __call__(self, args: list[str], root: Path) -> bytes:
        command = tuple(args)
        if command == ("status", "--porcelain"):
            return b"M change\n" if self.dirty else b""
        if command == ("rev-parse", "HEAD"):
            return b"a" * 40 + b"\n"
        if command == ("rev-parse", "origin/main"):
            return (b"b" if self.mismatch else b"a") * 40 + b"\n"
        if command == ("remote", "get-url", "origin"):
            return f"{self.origin_url}\n".encode()
        if command == ("remote", "show", "origin"):
            return b"HEAD branch: main\n"
        if command == ("rev-parse", "origin/main:README.md"):
            return b"c" * 40 + b"\n"
        if command == ("show", "origin/main:README.md"):
            if self.missing_readback:
                raise ReceiptError("missing remote byte readback")
            return b"public bytes\n"
        raise AssertionError(command)


def test_receipt_writes_closed_external_record_only_after_verified_readback(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_bytes(b"public bytes\n")
    output = tmp_path / "receipt.json"
    receipt = write_release_receipt(repo, output, ["README.md"], runner=Runner())
    assert output.exists()
    assert receipt["commit"] == "a" * 40
    assert receipt["files"] == [
        {
            "path": "README.md",
            "remote_blob_sha": "c" * 40,
            "sha256": hashlib.sha256(b"public bytes\n").hexdigest(),
            "size": 13,
        }
    ]
    assert json.loads(output.read_text()) == receipt


def test_receipt_rejects_sensitive_https_origins_before_output_write(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_bytes(b"public bytes\n")
    secret = "-".join(("release", "secret", "fragment"))  # noqa: FLY002
    unsafe_origins = [
        f"https://user:{secret}@github.com/example/public-repo.git",
        f"https://github.com/example/public-repo.git?token={secret}",
        f"https://github.com/example/public-repo.git#{secret}",
        f"ssh://git@github.com/example/public-repo.git?token={secret}",
        f"git@github.com:example/public-repo.git#{secret}",
    ]

    for number, origin_url in enumerate(unsafe_origins):
        output = tmp_path / f"unsafe-{number}.json"
        with pytest.raises(ReceiptError):
            write_release_receipt(
                repo, output, ["README.md"], runner=Runner(origin_url=origin_url)
            )
        assert not output.exists()

    output = tmp_path / "public.json"
    receipt = write_release_receipt(
        repo,
        output,
        ["README.md"],
        runner=Runner(origin_url="https://github.com/example/public-repo.git"),
    )
    assert receipt["origin"] == "https://github.com/example/public-repo.git"
    assert secret not in output.read_text()


@pytest.mark.parametrize("kind", ["inside", "mismatch", "readback", "dirty"])
def test_receipt_refuses_unsafe_or_unverified_inputs(tmp_path: Path, kind: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_bytes(b"public bytes\n")
    output = repo / "receipt.json" if kind == "inside" else tmp_path / "receipt.json"
    runner = Runner(
        mismatch=kind == "mismatch",
        missing_readback=kind == "readback",
        dirty=kind == "dirty",
    )
    with pytest.raises(ReceiptError):
        write_release_receipt(repo, output, ["README.md"], runner=runner)
    assert not output.exists()
