"""Visual artifact and provenance checks."""

from __future__ import annotations

import hashlib
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from tools import render_assets

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "article/semantic-reheating"


def test_mermaid_and_svg_assets_render_and_parse() -> None:
    diagram = ROOT / "docs/diagrams/controller-state.mmd"
    assert diagram.read_text(encoding="utf-8").startswith("stateDiagram-v2")
    result = subprocess.run(
        ["python", "tools/render_assets.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for asset in (BUNDLE / "architecture.svg", BUNDLE / "cover.svg"):
        root = ET.parse(asset).getroot()
        assert root.tag.endswith("svg")


def test_cover_png_has_required_mode_dimensions_and_local_provenance() -> None:
    with Image.open(BUNDLE / "cover.png") as image:
        assert image.size == (1600, 900)
        assert image.mode in {"RGB", "RGBA"}
    assets = (BUNDLE / "ASSETS.md").read_text(encoding="utf-8")
    for name in ("cover.svg", "cover.png", "architecture.svg", "controller-state.mmd"):
        assert name in assets
    assert "local" in assets.lower()


def test_renderer_selects_installed_imagemagick_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        render_assets.shutil,
        "which",
        lambda command: "/usr/bin/convert" if command == "convert" else None,
    )
    assert render_assets._image_renderer() == "/usr/bin/convert"


def test_ci_installs_the_declared_image_renderer() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "apt-get install -y imagemagick" in workflow


def test_renderer_is_byte_stable_for_packaged_cover() -> None:
    if not (ROOT / "tools/assets/node_modules/.bin/mmdc").exists():
        pytest.skip("requires npm ci --prefix tools/assets")
    cover = BUNDLE / "cover.png"
    for _ in range(2):
        result = subprocess.run(
            ["python", "tools/render_assets.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    before = hashlib.sha256(cover.read_bytes()).hexdigest()
    result = subprocess.run(
        ["python", "tools/render_assets.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert hashlib.sha256(cover.read_bytes()).hexdigest() == before
