"""Package skill/ as mpep-lookup.skill (zip).

Excludes __pycache__, .pyc, .DS_Store. Verifies size 15-50 MB. Output
goes to repo root.

Usage: python build/05_package_skill.py [--output PATH]
"""

import argparse
import pathlib
import shutil
import sys
import zipfile


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skill"
DEFAULT_OUTPUT = REPO_ROOT / "mpep-lookup.skill"

EXCLUDE_NAMES = {"__pycache__", ".DS_Store", ".pytest_cache"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def should_skip(path: pathlib.Path) -> bool:
    if path.name in EXCLUDE_NAMES:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    if any(part in EXCLUDE_NAMES for part in path.parts):
        return True
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    p.add_argument("--max-mb", type=float, default=50.0,
                   help="Fail if zip exceeds this size (default 50 MB)")
    p.add_argument("--min-mb", type=float, default=1.0,
                   help="Fail if zip smaller than this (default 1 MB)")
    args = p.parse_args()

    if not SKILL_DIR.exists():
        print(f"SKILL_DIR missing: {SKILL_DIR}", file=sys.stderr)
        return 1
    db_path = SKILL_DIR / "data" / "mpep.db"
    if not db_path.exists():
        print(f"WARNING: mpep.db missing at {db_path} - the bundle will not "
              "be functional. Run 03_build_database.py first.", file=sys.stderr)

    # Copy the canonical changelog from repo root into the skill so the version
    # history ships in the bundle (and in the local symlink install). The copy
    # in skill/ is a build artifact; CHANGELOG.md at the repo root is canonical.
    root_changelog = REPO_ROOT / "CHANGELOG.md"
    if root_changelog.exists():
        shutil.copy2(root_changelog, SKILL_DIR / "CHANGELOG.md")
    else:
        print(f"WARNING: {root_changelog} missing; bundle will lack CHANGELOG.md",
              file=sys.stderr)

    args.output.unlink(missing_ok=True)
    n_files = 0
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(SKILL_DIR.rglob("*")):
            if not f.is_file():
                continue
            if should_skip(f):
                continue
            arcname = str(f.relative_to(SKILL_DIR))
            zf.write(f, arcname=arcname)
            n_files += 1

    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"Wrote {args.output} ({n_files} files, {size_mb:.2f} MB)")
    if size_mb < args.min_mb:
        print(f"FAIL: too small ({size_mb:.2f} MB < {args.min_mb} MB minimum)",
              file=sys.stderr)
        return 1
    if size_mb > args.max_mb:
        print(f"FAIL: too large ({size_mb:.2f} MB > {args.max_mb} MB maximum)",
              file=sys.stderr)
        return 1
    print(f"  size OK ({args.min_mb} <= {size_mb:.2f} <= {args.max_mb} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
