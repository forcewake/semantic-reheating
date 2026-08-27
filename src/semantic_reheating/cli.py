"""Command-line interface for semantic reheating."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Run the minimal command-line interface."""
    parser = argparse.ArgumentParser(
        prog="reheat",
        description="Display semantic reheating reference-kit help.",
    )
    parser.parse_args(argv)
    return 0
