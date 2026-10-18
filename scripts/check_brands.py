"""Fail if a real consumer or enterprise brand appears in code, docs or sample data.

Portfolio rule: every company, merchant and customer in this repo is fictional.
Technology vendors that the code genuinely integrates with (Azure, OpenAI,
Ollama ...) are fine and are not on the list. Matching is case-sensitive on the
brand's usual spelling, to avoid flagging ordinary words.
Run: python scripts/check_brands.py   (CI runs it on every push)
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

BRANDS = [
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
    "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND", "REDACTED-BRAND",
]
PATTERN = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(b) for b in BRANDS) + r")(?![A-Za-z])")
SKIP = {"scripts/check_brands.py"}
TEXT = re.compile(r"\.(py|md|txt|json|ya?ml|toml|csv|sql|html|ts|js)$")


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    files = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
                           capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for f in files:
        if f in SKIP or not TEXT.search(f):
            continue
        for n, line in enumerate((root / f).read_text(errors="ignore").splitlines(), 1):
            for m in PATTERN.finditer(line):
                hits.append(f"{f}:{n}: {m.group(1)}")
    if hits:
        print("Real brand names found (use fictional names instead):")
        print("\n".join(f"  {h}" for h in hits))
        return 1
    print(f"No real brand names in {len(files)} tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
