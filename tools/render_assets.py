"""Render local Mermaid and SVG article assets without remote services."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "article" / "semantic-reheating"
MMDC = ROOT / "tools" / "assets" / "node_modules" / ".bin" / "mmdc"
COVER_RENDERER = ROOT / "tools" / "assets" / "render-cover.mjs"


def _check_generated_assets(architecture: Path, cover: Path) -> None:
    ET.parse(architecture)
    with Image.open(cover) as image:
        if image.size != (1600, 900) or image.mode not in {"RGB", "RGBA"}:
            raise ValueError("cover.png must be an RGB/RGBA 1600x900 image")


def check_assets() -> None:
    ET.parse(BUNDLE / "cover.svg")
    _check_generated_assets(BUNDLE / "architecture.svg", BUNDLE / "cover.png")


def _render_generated_assets(architecture: Path, cover: Path) -> None:
    architecture.parent.mkdir(parents=True, exist_ok=True)
    cover.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8"
    ) as config:
        json.dump({"args": ["--no-sandbox"]}, config)
        config.flush()
        subprocess.run(
            [
                str(MMDC),
                "-i",
                str(ROOT / "docs/diagrams/controller-state.mmd"),
                "-o",
                str(architecture),
                "-b",
                "transparent",
                "-p",
                config.name,
            ],
            check=True,
        )
    subprocess.run(
        [
            "node",
            str(COVER_RENDERER),
            str(BUNDLE / "cover.svg"),
            str(cover),
        ],
        check=True,
    )
    _check_generated_assets(architecture, cover)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cover_pixels(path: Path) -> tuple[tuple[int, int], bytes]:
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        return rgba.size, rgba.tobytes()


def _assert_packaged_parity(
    generated_architecture: Path,
    generated_cover: Path,
    packaged_architecture: Path,
    packaged_cover: Path,
) -> None:
    if generated_architecture.read_bytes() != packaged_architecture.read_bytes():
        raise ValueError("generated architecture.svg differs from packaged bytes")
    if _cover_pixels(generated_cover) != _cover_pixels(packaged_cover):
        raise ValueError(
            "generated cover.png pixels differ from packaged visual content"
        )


def verify_render() -> None:
    with tempfile.TemporaryDirectory(prefix="semantic-reheating-assets-") as temporary:
        root = Path(temporary)
        first = root / "first"
        second = root / "second"
        _render_generated_assets(first / "architecture.svg", first / "cover.png")
        _render_generated_assets(second / "architecture.svg", second / "cover.png")
        first_hashes = (
            _sha256(first / "architecture.svg"),
            _sha256(first / "cover.png"),
        )
        second_hashes = (
            _sha256(second / "architecture.svg"),
            _sha256(second / "cover.png"),
        )
        if first_hashes != second_hashes:
            raise ValueError(
                "article asset rendering is not byte-stable in this environment"
            )
        _assert_packaged_parity(
            first / "architecture.svg",
            first / "cover.png",
            BUNDLE / "architecture.svg",
            BUNDLE / "cover.png",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--verify-render", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        check_assets()
        print("article assets validate")
        return 0
    if not MMDC.exists():
        print(f"missing repository-local Mermaid CLI: {MMDC}", file=sys.stderr)
        return 1
    if args.verify_render:
        verify_render()
        print(
            "article asset rendering is deterministic and matches packaged visual content"
        )
        return 0
    _render_generated_assets(BUNDLE / "architecture.svg", BUNDLE / "cover.png")
    check_assets()
    print("rendered article assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
