"""Tests for deterministic, bounded generated article content."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_generator_check_has_no_drift_and_owns_only_delimited_section() -> None:
    result = subprocess.run(
        ["python", "tools/generate_article_data.py", "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    article = (ROOT / "article/semantic-reheating/index.md").read_text(encoding="utf-8")
    assert article.count("<!-- BEGIN GENERATED RESULTS -->") == 1
    assert article.count("<!-- END GENERATED RESULTS -->") == 1
    section = article.split("<!-- BEGIN GENERATED RESULTS -->", 1)[1].split(
        "<!-- END GENERATED RESULTS -->", 1
    )[0]
    assert "benchmark/results/deterministic-results.json" in section
    assert "sample size" in section.lower()
    assert "sha256" in section.lower()


def test_generator_detects_a_hand_edited_generated_section(tmp_path: Path) -> None:
    # The real generator exposes its pure rendering seam, so a changed generated
    # section is observable without permitting it to rewrite surrounding prose.
    from tools import generate_article_data

    article = ROOT / "article/semantic-reheating/index.md"
    text = article.read_text(encoding="utf-8")
    changed = text.replace(
        "<!-- END GENERATED RESULTS -->", "hand edit\n<!-- END GENERATED RESULTS -->"
    )
    assert generate_article_data.generated_section_matches(changed, ROOT) is False
