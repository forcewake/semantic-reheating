"""Visual artifact and provenance checks."""

from __future__ import annotations

import hashlib
import json
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
    assert "@resvg/resvg-js" in assets
    assert "ImageMagick" not in assets
    assert "within-environment byte stability" in assets
    assert "exact SVG bytes" in assets
    assert "decoded RGBA pixels" in assets
    assert "Cross-environment PNG-container byte equality is not claimed" in assets


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
    assert "uv run python tools/render_assets.py --verify-render" in workflow


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


def test_packaged_parity_rejects_architecture_drift(tmp_path: Path) -> None:
    generated_architecture = tmp_path / "generated.svg"
    packaged_architecture = tmp_path / "packaged.svg"
    generated_architecture.write_text('<svg id="generated"/>', encoding="utf-8")
    packaged_architecture.write_text('<svg id="packaged"/>', encoding="utf-8")
    generated_cover = tmp_path / "generated.png"
    packaged_cover = tmp_path / "packaged.png"
    Image.new("RGBA", (1, 1), "black").save(generated_cover)
    Image.new("RGBA", (1, 1), "black").save(packaged_cover)

    with pytest.raises(ValueError, match="architecture.svg differs"):
        render_assets._assert_packaged_parity(
            generated_architecture,
            generated_cover,
            packaged_architecture,
            packaged_cover,
        )


def test_packaged_parity_rejects_cover_pixel_drift(tmp_path: Path) -> None:
    generated_architecture = tmp_path / "generated.svg"
    packaged_architecture = tmp_path / "packaged.svg"
    generated_architecture.write_text("<svg/>", encoding="utf-8")
    packaged_architecture.write_text("<svg/>", encoding="utf-8")
    generated_cover = tmp_path / "generated.png"
    packaged_cover = tmp_path / "packaged.png"
    Image.new("RGBA", (1, 1), "black").save(generated_cover)
    Image.new("RGBA", (1, 1), "white").save(packaged_cover)

    with pytest.raises(ValueError, match="cover.png pixels differ"):
        render_assets._assert_packaged_parity(
            generated_architecture,
            generated_cover,
            packaged_architecture,
            packaged_cover,
        )


def test_verify_render_rejects_packaged_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not (ROOT / "tools/assets/node_modules/.bin/mmdc").exists():
        pytest.skip("requires npm ci --prefix tools/assets")
    (tmp_path / "cover.svg").write_bytes((BUNDLE / "cover.svg").read_bytes())
    (tmp_path / "cover.png").write_bytes((BUNDLE / "cover.png").read_bytes())
    (tmp_path / "architecture.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr(render_assets, "BUNDLE", tmp_path)

    with pytest.raises(ValueError, match="architecture.svg differs"):
        render_assets.verify_render()


def test_verify_render_is_non_mutating_and_deterministic() -> None:
    if not (ROOT / "tools/assets/node_modules/.bin/mmdc").exists():
        pytest.skip("requires npm ci --prefix tools/assets")
    assets = (BUNDLE / "architecture.svg", BUNDLE / "cover.png")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
    result = subprocess.run(
        ["python", "tools/render_assets.py", "--verify-render"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
    assert result.returncode == 0, result.stderr
    assert (
        "article asset rendering is deterministic and matches packaged visual content"
        in result.stdout
    )
    assert after == before
