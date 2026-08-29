"""Validate the local article bundle without publication side effects."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    bundle = args.bundle
    for name in ("article-data-manifest", "sources-ledger"):
        schema = load(bundle / f"{name}.schema.json")
        value = load(bundle / f"{name}.json")
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    text = (bundle / "index.md").read_text(encoding="utf-8")
    if "draft: false" not in text or "TocOpen: false" not in text:
        raise ValueError("frontmatter gate failed")
    cited = set(re.findall(r"\[\^([a-z0-9-]+)\]", text))
    ledger = {
        source["citation_key"]
        for source in load(bundle / "sources-ledger.json")["sources"]
    }
    if cited != ledger:
        raise ValueError("citation ledger mismatch")
    subprocess.run(
        [sys.executable, str(ROOT / "tools/generate_article_data.py"), "--check"],
        check=True,
    )
    subprocess.run(
        [sys.executable, str(ROOT / "tools/render_assets.py"), "--check"], check=True
    )
    with Image.open(bundle / "cover.png") as image:
        assert image.size == (1600, 900) and image.mode in {"RGB", "RGBA"}
    print("article bundle validates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
