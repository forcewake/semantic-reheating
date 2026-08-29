"""Publication-facing README and security-reporting promises stay actionable."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
SECURITY = ROOT / "SECURITY.md"
ARTICLE = "article/semantic-reheating/index.md"
REPOSITORY = "https://github.com/forcewake/semantic-reheating"


def test_readme_links_the_published_article_and_current_cli_commands() -> None:
    text = README.read_text(encoding="utf-8")

    assert ARTICLE in text
    assert (ROOT / ARTICLE).is_file()
    assert (
        "Later releases may add deterministic validation and decision capabilities"
        not in text
    )
    for command in ("validate", "analyze", "explain", "benchmark"):
        assert f"`reheat {command}`" in text


def test_security_policy_has_private_github_reporting_and_no_personal_contact() -> None:
    text = SECURITY.read_text(encoding="utf-8")

    assert f"{REPOSITORY}/security/advisories/new" in text
    assert f"{REPOSITORY}/issues/new/choose" in text
    assert "repository maintainer contact channel" not in text.casefold()
    assert re.search(r"(?i)mailto:|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", text) is None
