"""Render local Mermaid and SVG article assets without remote services."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "article" / "semantic-reheating"
MMDC = ROOT / "tools" / "assets" / "node_modules" / ".bin" / "mmdc"


def check_assets() -> None:
    for path in (BUNDLE / "architecture.svg", BUNDLE / "cover.svg"):
        ET.parse(path)
    with Image.open(BUNDLE / "cover.png") as image:
        if image.size != (1600, 900) or image.mode not in {"RGB", "RGBA"}:
            raise ValueError("cover.png must be an RGB/RGBA 1600x900 image")


def _image_renderer() -> str:
    for command in ("magick", "convert"):
        executable = shutil.which(command)
        if executable is not None:
            return executable
    raise FileNotFoundError("ImageMagick command is required to render cover.png")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        check_assets()
        print("article assets validate")
        return 0
    if not MMDC.exists():
        print(f"missing repository-local Mermaid CLI: {MMDC}", file=sys.stderr)
        return 1
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
                str(BUNDLE / "architecture.svg"),
                "-b",
                "transparent",
                "-p",
                config.name,
            ],
            check=True,
        )
    subprocess.run(
        [
            _image_renderer(),
            str(BUNDLE / "cover.svg"),
            "-strip",
            "-define",
            "png:exclude-chunk=date,time",
            str(BUNDLE / "cover.png"),
        ],
        check=True,
    )
    check_assets()
    print("rendered article assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
