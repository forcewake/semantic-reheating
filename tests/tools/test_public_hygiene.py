from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.clean_checkout_verify import verify_local
from tools.public_hygiene import HygieneError, scan_repository


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "tester@example.invalid")
    git(root, "config", "user.name", "tester")
    (root / "README.md").write_text("# public\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "initial")
    return root


def test_history_detects_deleted_secret_and_private_marker_without_echoing_value(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    token = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    (root / "removed.txt").write_text(
        f"token={token}\n" + "raw" + "-reasoning: private\n"
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "bad")
    (root / "removed.txt").unlink()
    git(root, "commit", "-am", "remove")
    findings = scan_repository(root, history=True)
    assert any("credential" in finding.message for finding in findings)
    assert any("private" + " content marker" in finding.message for finding in findings)
    assert token not in "\n".join(finding.message for finding in findings)


def test_history_detects_deleted_forbidden_artifact_path(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    private_root = root / ".hermes"
    private_root.mkdir()
    (private_root / "state.txt").write_text("state")
    git(root, "add", ".")
    git(root, "commit", "-qm", "bad path")
    (private_root / "state.txt").unlink()
    private_root.rmdir()
    git(root, "commit", "-am", "remove path")
    assert any(
        "forbidden artifact path" in item.message
        for item in scan_repository(root, history=True)
    )


@pytest.mark.parametrize(
    "path",
    [
        "/home/" + "alice/private",
        "/Users/" + "alice/private",
        r"C:\\Users\\" + r"alice\\private",
    ],
)
def test_tracked_scan_detects_user_absolute_paths(tmp_path: Path, path: str) -> None:
    root = make_repo(tmp_path)
    (root / "notes.md").write_text(path)
    git(root, "add", ".")
    git(root, "commit", "-qm", "path")
    assert any(
        "user-specific absolute path" in item.message
        for item in scan_repository(root, tracked_only=True)
    )


def test_tracked_scan_rejects_private_paths_json_links_and_unused_assets(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    (root / ".hermes").mkdir()
    (root / ".hermes" / "state.txt").write_text("x")
    (root / "benchmark").mkdir()
    (root / "benchmark" / "unregistered.json").write_text("{}")
    article = root / "article" / "semantic-reheating"
    article.mkdir(parents=True)
    (article / "index.md").write_text("[missing](nope.md)\n")
    (article / "unused.svg").write_text("<svg/>")
    git(root, "add", ".")
    git(root, "commit", "-qm", "bad files")
    messages = "\n".join(
        item.message for item in scan_repository(root, tracked_only=True)
    )
    assert ".hermes" in messages
    assert "undeclared public domain JSON" in messages
    assert "bad relative link" in messages
    assert "unused article asset" in messages


def test_permits_redacted_trace_policy_words_metadata_and_generic_temp_root(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}')
    (root / "trace.jsonl").write_text('{"detail": "[REDACTED]"}\n')
    (root / "policy.md").write_text(
        "forbidden path classes include /home/"
        + "<user> and C:\\\\Users\\\\"
        + "<user>; /tmp/semantic-reheating-clean is allowed"
    )
    git(root, "add", ".")
    git(root, "commit", "-qm", "safe")
    assert not scan_repository(root, tracked_only=True)


def test_policy_wording_is_narrow_but_docs_tools_and_tests_leaks_are_detected(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    docs = root / "docs"
    tools = root / "tools"
    tests = root / "tests"
    docs.mkdir()
    tools.mkdir()
    tests.mkdir()
    (docs / "policy.md").write_text(
        "Never commit private transcripts or raw reasoning."  # hygiene: test-runtime-fragment
    )
    (docs / "leak.md").write_text("local=" + "/home/" + "alice/private")
    private_phrase = "private " + "transcript"
    (tools / "leak.py").write_text("record = " + repr(private_phrase))
    token = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
    (tests / "leak.py").write_text("token = " + repr(token))
    git(root, "add", ".")
    git(root, "commit", "-qm", "leaks")
    findings = scan_repository(root, tracked_only=True)
    by_path = {item.path: item.message for item in findings}
    assert (
        "docs/leak.md" in by_path
        and "user-specific absolute path" in by_path["docs/leak.md"]
    )
    assert (
        "tools/leak.py" in by_path
        and "private" + " content marker" in by_path["tools/leak.py"]
    )
    assert (
        "tests/leak.py" in by_path
        and "credential-shaped content" in by_path["tests/leak.py"]
    )
    assert "docs/policy.md" not in by_path


def test_scanner_source_is_clean_but_tool_path_has_no_blanket_exemption(
    tmp_path: Path,
) -> None:
    root = make_repo(tmp_path)
    tools = root / "tools"
    tools.mkdir()
    (tools / "public_hygiene.py").write_text(
        (Path(__file__).parents[2] / "tools" / "public_hygiene.py").read_text()
    )
    (tools / "other.py").write_text("record = 'private" + " transcript'\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "scanner")
    findings = scan_repository(root, tracked_only=True)
    assert not any(item.path == "tools/public_hygiene.py" for item in findings)
    assert any(
        item.path == "tools/other.py" and "private" + " content marker" in item.message
        for item in findings
    )


def test_clean_checkout_verifier_clones_and_scans_clean_commit(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert verify_local(root)


def test_cli_raises_fail_closed_error_for_findings(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "raw.md").write_text("private " + "transcript")
    git(root, "add", ".")
    git(root, "commit", "-qm", "bad")
    with pytest.raises(HygieneError):
        scan_repository(root, tracked_only=True, raise_on_findings=True)
