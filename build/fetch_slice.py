"""Vertical-slice fetcher (development helper).

Sequentially fetches the 5 representative HTML pages we need to verify the
full pipeline end-to-end before scaling to the full corpus. This is NOT the
production fetcher (01_fetch_corpus.py). It hardcodes the 5 URLs and
intentionally avoids the worker pool / discovery logic.

Usage: python build/fetch_slice.py
"""

import pathlib
import sys
import time

import requests

USER_AGENT = "mpep-lookup-skill-builder/1.0"

# 5 representative records covering the kinds the parser must handle.
SOURCES = [
    # MPEP top-level section
    ("mpep_section_top",
     "https://www.uspto.gov/web/offices/pac/mpep/s2141.html",
     "build/raw_html/sections/s2141.html"),
    # MPEP subsection with paren-letter
    ("mpep_section_subsection",
     "https://www.uspto.gov/web/offices/pac/mpep/s2141.html",  # subsection lives in same file
     "build/raw_html/sections/s2141_subsection.html"),  # we'll dedupe below
    # 35 USC Appendix L (contains AIA + pre-AIA splits)
    ("statute_appx_l",
     "https://www.uspto.gov/web/offices/pac/mpep/mpep-9015-appx-l.html",
     "build/raw_html/appendices/mpep-9015-appx-l.html"),
    # 37 CFR Appendix R (contains 1.131 etc.)
    ("cfr_appx_r",
     "https://www.uspto.gov/web/offices/pac/mpep/mpep-9020-appx-r.html",
     "build/raw_html/appendices/mpep-9020-appx-r.html"),
    # Form Paragraphs Consolidated Chapter
    ("form_paragraphs",
     "https://www.uspto.gov/web/offices/pac/mpep/mpep-9095-Form-Paragraph-Chapter.html",
     "build/raw_html/appendices/mpep-9095-Form-Paragraph-Chapter.html"),
]


def fetch_one(url: str, dest_path: pathlib.Path) -> tuple[bool, int, int]:
    """Return (ok, status, content_length)."""
    if dest_path.exists():
        size = dest_path.stat().st_size
        print(f"  SKIP (cached): {dest_path} ({size} bytes)")
        return True, 200, size
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    except requests.RequestException as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return False, 0, 0
    if resp.status_code != 200:
        print(f"  HTTP {resp.status_code}: {url}", file=sys.stderr)
        return False, resp.status_code, 0
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(resp.content)
    size = len(resp.content)
    print(f"  OK ({size} bytes): {dest_path}")
    return True, 200, size


def main() -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    seen = set()
    failures = []
    for label, url, rel_path in SOURCES:
        if url in seen:
            print(f"[{label}] dedup of {url}")
            continue
        seen.add(url)
        dest = repo_root / rel_path
        print(f"[{label}] {url}")
        ok, status, _ = fetch_one(url, dest)
        if not ok:
            failures.append((label, url, status))
        time.sleep(1.0)
    if failures:
        print(f"\nFAILED ({len(failures)} URLs):", file=sys.stderr)
        for label, url, status in failures:
            print(f"  [{label}] HTTP {status}: {url}", file=sys.stderr)
        return 1
    print("\nAll fetches complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
