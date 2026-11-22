"""Replaces resource names and principal identifiers in result files before they
become public (AGENTS.md: exported results are public).

Usage: SANITIZE_VALUES="name1 name2 ..." python3 scripts/sanitize.py DIR
Each value, and anything ending in a known Azure host suffix, is replaced.
Exits non-zero if a GUID remains, so an unknown identifier cannot slip through.
"""
import os
import re
import sys
from pathlib import Path

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HOSTS = re.compile(r"[A-Za-z0-9-]+\.(database\.windows\.net|servicebus\.windows\.net|blob\.core\.windows\.net|azurecr\.io)")


def sanitize_text(text: str, values: list[str]) -> str:
    for v in sorted(values, key=len, reverse=True):
        if v:
            text = text.replace(v, "<redacted>")
    text = HOSTS.sub(lambda m: "<redacted>." + m.group(1), text)
    return GUID.sub("<guid>", text)


def main() -> int:
    root = Path(sys.argv[1])
    values = os.environ.get("SANITIZE_VALUES", "").split()
    for path in root.rglob("*"):
        if path.is_file():
            path.write_text(sanitize_text(path.read_text(errors="replace"), values))
    leftovers = [str(p) for p in root.rglob("*") if p.is_file() and GUID.search(p.read_text(errors="replace"))]
    if leftovers:
        print("GUIDs remain in:", leftovers)
        return 1
    print(f"sanitized {sum(1 for p in root.rglob('*') if p.is_file())} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
