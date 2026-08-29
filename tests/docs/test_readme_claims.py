"""Public wording is intentionally narrow and host-authority preserving."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"


def test_readme_defines_the_bounded_metaphor_and_non_goals() -> None:
    text = README.read_text(encoding="utf-8").lower()

    assert "proposal-policy/search-breadth metaphor" in text
    assert "not decoder-temperature control" in text
    assert "not strict simulated annealing" in text
    assert "## non-goals" in text
    assert "does not grant" in text


def test_readme_names_the_host_as_the_execution_authority() -> None:
    text = README.read_text(encoding="utf-8").lower()

    assert "host retains authority" in text
    assert "credentials" in text
    assert "tools" in text
    assert "side effects" in text
