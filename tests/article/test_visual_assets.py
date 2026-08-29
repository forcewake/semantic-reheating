"""Visual artifact and provenance checks."""

from __future__ import annotations

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

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
    assert "@resvg/resvg-js" in assets
    assert "ImageMagick" not in assets


def test_cover_renderer_is_repo_local_and_lockfile_pinned() -> None:
    package = json.loads(
        (ROOT / "tools/assets/package.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (ROOT / "tools/assets/package-lock.json").read_text(encoding="utf-8")
    )
    assert package["devDependencies"]["@resvg/resvg-js"] == "2.6.2"
    assert lock["packages"][""]["devDependencies"]["@resvg/resvg-js"] == "2.6.2"
    locked_renderer = lock["packages"]["node_modules/@resvg/resvg-js"]
    assert locked_renderer["version"] == "2.6.2"
    assert locked_renderer["integrity"].startswith("sha512-")
    renderer = ROOT / "tools/assets/render-cover.mjs"
    assert renderer.is_file()
    renderer_source = renderer.read_text(encoding="utf-8")
    assert '"@resvg/resvg-js"' in renderer_source
    assert "magick" not in renderer_source.lower()


def test_cover_renderer_rejects_missing_paths() -> None:
    result = subprocess.run(
        ["node", "tools/assets/render-cover.mjs"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "usage: node render-cover.mjs SOURCE.svg DESTINATION.png" in result.stderr


def test_ci_uses_locked_renderer_without_system_imagemagick() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "ubuntu-24.04" in workflow
    assert "apt-get install -y imagemagick" not in workflow
    assert "npm ci --prefix tools/assets" in workflow


def test_ci_reports_tree_drift_before_clean_checkout() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    status_index = workflow.index("git status --short --untracked-files=all")
    diff_index = workflow.index("git diff --exit-code")
    verify_index = workflow.index("tools/clean_checkout_verify.py --local")
    assert status_index < diff_index < verify_index


def test_repository_ignores_local_asset_dependencies() -> None:
    result = subprocess.run(
        [
            "git",
            "check-ignore",
            "--no-index",
            "--quiet",
            "tools/assets/node_modules/package.json",
        ],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0


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
