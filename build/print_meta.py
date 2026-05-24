"""Print a value from the built database's metadata table, or assert the
skill version. Used by the release workflow.

Usage:
    python build/print_meta.py source_revision        # -> R-01.2024
    python build/print_meta.py --assert-version 1.0.0  # exit 0 if SKILL.md matches
"""

import argparse
import pathlib
import re
import sqlite3
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "skill" / "data" / "mpep.db"
SKILL_MD = REPO_ROOT / "skill" / "SKILL.md"


def skill_version() -> str | None:
    """Return the `version:` field from SKILL.md frontmatter, or None."""
    m = re.search(r"^version:\s*(\d+\.\d+\.\d+)\s*$",
                  SKILL_MD.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else None


def metadata_value(key: str) -> str | None:
    """Return a value from the DB metadata table, or None if absent."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def main() -> int:
    p = argparse.ArgumentParser(description="Print build metadata or assert the skill version.")
    p.add_argument("key", nargs="?", help="metadata key to print (e.g. source_revision)")
    p.add_argument("--assert-version", metavar="X.Y.Z",
                   help="exit non-zero unless SKILL.md's version matches this value")
    args = p.parse_args()

    if args.assert_version is not None:
        v = skill_version()
        if v != args.assert_version:
            print(f"version mismatch: expected {args.assert_version}, SKILL.md has {v}",
                  file=sys.stderr)
            return 1
        return 0

    if not args.key:
        print("provide a metadata key or --assert-version", file=sys.stderr)
        return 1
    val = metadata_value(args.key)
    if val is None:
        print(f"no metadata key {args.key!r} (is the database built?)", file=sys.stderr)
        return 1
    print(val)
    return 0


if __name__ == "__main__":
    sys.exit(main())
