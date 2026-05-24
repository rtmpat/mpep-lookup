"""Print the CHANGELOG.md section for one version, for GitHub release notes.

Usage: python build/extract_changelog.py 1.0.0
Prints the body of the matching `## vX.Y.Z` section (header line excluded).
Exit 1 if no such section exists.
"""

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def section_body(version: str, text: str) -> str | None:
    """Return the body of the `## v<version>` section (without its header)."""
    head = re.compile(r"^##\s*v?" + re.escape(version) + r"\b")
    nexthead = re.compile(r"^##\s")
    body: list[str] = []
    capturing = False
    for line in text.split("\n"):
        if capturing:
            if nexthead.match(line):
                break
            body.append(line)
        elif head.match(line):
            capturing = True  # skip the header line itself
    if not capturing:
        return None
    return "\n".join(body).strip()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: extract_changelog.py <version>", file=sys.stderr)
        return 1
    body = section_body(sys.argv[1], CHANGELOG.read_text(encoding="utf-8"))
    if not body:
        print(f"no CHANGELOG.md section for v{sys.argv[1]}", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
