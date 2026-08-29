"""Verify current committed content from an isolated clean checkout."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.public_hygiene import scan_repository


class CleanCheckoutError(RuntimeError):
    pass


def _run(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise CleanCheckoutError(f"git {' '.join(args)} failed")
    return result.stdout.strip()


def verify_local(root: Path) -> bool:
    """Clone HEAD to a generic temporary root and run offline hygiene there."""
    root = root.resolve()
    if _run(root, "status", "--porcelain"):
        raise CleanCheckoutError("local verification requires a clean worktree")
    with tempfile.TemporaryDirectory(prefix="semantic-reheating-clean-") as temporary:
        checkout = Path(temporary) / "checkout"
        clone = subprocess.run(
            ["git", "clone", "--no-local", "--quiet", str(root), str(checkout)],
            capture_output=True,
            text=True,
            check=False,
        )
        if clone.returncode:
            raise CleanCheckoutError("unable to create clean checkout")
        if _run(checkout, "status", "--porcelain"):
            raise CleanCheckoutError("clean checkout is dirty")
        findings = scan_repository(checkout, tracked_only=True)
        if findings:
            raise CleanCheckoutError("clean checkout hygiene failed")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local", action="store_true", help="verify current local committed checkout"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if not args.local:
        parser.error("only --local is supported")
    verify_local(args.root)
    print("clean checkout verification valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
