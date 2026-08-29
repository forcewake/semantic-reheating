"""Write a closed release receipt outside a repository after independent Git readback."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ReceiptError(RuntimeError):
    pass


Runner = Callable[[list[str], Path], bytes]


def _subprocess_runner(args: list[str], root: Path) -> bytes:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False
    )
    if completed.returncode:
        raise ReceiptError(f"git {' '.join(args)} failed")
    return completed.stdout


def _text(runner: Runner, root: Path, args: list[str]) -> str:
    try:
        return runner(args, root).decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise ReceiptError(f"non-text git response for {' '.join(args)}") from error


def _safe_relative(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not path or "\\" in path:
        raise ReceiptError(f"unsafe readback path: {path}")
    return candidate.as_posix()


def _assert_outside(repo: Path, output: Path) -> Path:
    resolved = output.resolve(strict=False)
    try:
        resolved.relative_to(repo.resolve())
    except ValueError:
        return resolved
    raise ReceiptError("receipt output must be outside the repository")


def write_release_receipt(
    repo: Path,
    output: Path,
    required_paths: Sequence[str],
    *,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Verify public origin/main bytes then atomically write an external receipt."""
    repo = repo.resolve()
    command = _subprocess_runner if runner is None else runner
    if _text(command, repo, ["status", "--porcelain"]):
        raise ReceiptError("dirty worktree")
    local_sha = _text(command, repo, ["rev-parse", "HEAD"])
    remote_sha = _text(command, repo, ["rev-parse", "origin/main"])
    if local_sha != remote_sha:
        raise ReceiptError("local and origin/main SHA mismatch")
    remote_url = _text(command, repo, ["remote", "get-url", "origin"])
    if not remote_url.startswith(("https://", "ssh://", "git@")):
        raise ReceiptError("origin is not public remote metadata")
    if "?" in remote_url or "#" in remote_url:
        raise ReceiptError("origin URL includes disallowed sensitive components")
    if remote_url.startswith(("https://", "ssh://")):
        parsed_origin = urlsplit(remote_url)
        if parsed_origin.password is not None:
            raise ReceiptError("origin URL includes disallowed sensitive components")
        if remote_url.startswith("https://") and parsed_origin.username is not None:
            raise ReceiptError(
                "origin HTTPS URL includes disallowed sensitive components"
            )
    remote_metadata = _text(command, repo, ["remote", "show", "origin"])
    if "HEAD branch: main" not in remote_metadata:
        raise ReceiptError("origin default branch is not main")
    if not required_paths:
        raise ReceiptError("receipt requires at least one verified path")
    files: list[dict[str, Any]] = []
    for raw_path in required_paths:
        path = _safe_relative(raw_path)
        local_file = repo / path
        if not local_file.is_file():
            raise ReceiptError(f"missing local readback path: {path}")
        local_bytes = local_file.read_bytes()
        remote_blob_sha = _text(command, repo, ["rev-parse", f"origin/main:{path}"])
        try:
            remote_bytes = command(["show", f"origin/main:{path}"], repo)
        except Exception as error:
            raise ReceiptError(f"missing remote byte readback for {path}") from error
        if remote_bytes != local_bytes:
            raise ReceiptError(f"remote decoded bytes differ for {path}")
        files.append(
            {
                "path": path,
                "remote_blob_sha": remote_blob_sha,
                "sha256": hashlib.sha256(local_bytes).hexdigest(),
                "size": len(local_bytes),
            }
        )
    output_path = _assert_outside(repo, output)
    receipt: dict[str, Any] = {
        "receipt_version": "1",
        "commit": local_sha,
        "branch": "main",
        "origin": remote_url,
        "files": files,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--path", action="append", required=True, dest="paths")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    write_release_receipt(args.repo, args.output, args.paths)
    print("external release receipt written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
