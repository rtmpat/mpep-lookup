"""Phase 4 verification.

Runs against skill/data/mpep.db. Fails loud if any check doesn't pass.

Checks:
1. Coverage by kind within expected ranges (post-dedup, post-CEO-revised)
2. 11 spot-retrievals (citation -> expected title substring, case-insensitive)
3. 5 spot-searches (FTS5 query -> expected citation in top 3)

Usage: python build/04_verify_corpus.py
"""

import pathlib
import sqlite3
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "skill" / "data" / "mpep.db"

SPOT_RETRIEVALS = [
    ("MPEP 2141", "obviousness"),
    ("MPEP 2141.01(a)", "analogous"),
    ("MPEP 2106", "eligibility"),
    ("MPEP 2143.02", "reasonable expectation"),
    ("MPEP 706", "rejection"),
    ("35 USC 101", "inventions patentable"),
    ("35 USC 102", "novelty"),
    ("35 USC 103", "non-obvious"),
    ("35 USC 112", "specification"),
    ("37 CFR 1.131", "affidavit"),
    ("37 CFR 1.132", "affidavit"),
]

# (query, expected_citation_or_chapter, optional_kind_filter)
SPOT_SEARCHES = [
    ('"reasonable expectation of success"', "MPEP 2143.02", "mpep_section"),
    ("analogous art", "MPEP 2141.01(a)", "mpep_section"),
    ("prima facie obviousness", "MPEP 2142", "mpep_section"),
    ("Alice Mayo framework", "MPEP 2106", "mpep_section"),
    ("restriction practice", "MPEP 800", "mpep_section"),  # chapter 800; will match any 800.xx
]

KIND_RANGES = {
    # (low, high) post-dedup. A naive "700-900" estimate for mpep_section
    # was top-level only, didn't account for subsection records:
    # each section file (~736) contains 1 top + ~2-3 subsections on
    # average, so total ~1500-2500.
    "mpep_section": (1500, 2500),
    "statute": (180, 260),
    "cfr_rule": (700, 900),
    "form_paragraph": (700, 850),
    "index_entry": (6000, 15000),  # Subject Matter Index (mpep-index-a..z); ~8.3k at r-01.2024
    "appendix": (0, 50),
}


def _resolve_normalized(citation: str) -> str:
    """Use _common.normalize for consistency."""
    sys.path.insert(0, str(REPO_ROOT / "skill" / "scripts"))
    from _common import normalize
    return normalize(citation)


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB missing: {DB_PATH}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(str(DB_PATH))

    failures: list[str] = []

    # 1. Coverage by kind
    print("=== Coverage by kind ===")
    for kind, (lo, hi) in KIND_RANGES.items():
        n = conn.execute(
            "SELECT COUNT(*) FROM sections WHERE kind = ?", (kind,)
        ).fetchone()[0]
        ok = lo <= n <= hi
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] {kind:18} {n:>5}  (expected {lo}-{hi})")
        if not ok and lo > 0:  # only fail if a non-empty range is required
            failures.append(f"kind {kind}: count {n} outside [{lo}, {hi}]")

    # 2. Spot retrievals
    print("\n=== Spot retrievals (11) ===")
    for citation, expected_substr in SPOT_RETRIEVALS:
        slug = _resolve_normalized(citation)
        row = conn.execute(
            "SELECT citation, title FROM sections WHERE citation_normalized = ?",
            (slug,),
        ).fetchone()
        if row is None:
            failures.append(f"spot retrieval miss: {citation} (slug={slug})")
            print(f"  [FAIL] {citation:25} -> NOT FOUND (slug={slug})")
            continue
        title_lower = row[1].lower()
        if expected_substr.lower() not in title_lower:
            failures.append(
                f"{citation}: title {row[1]!r} does not contain {expected_substr!r}"
            )
            print(f"  [FAIL] {citation:25} -> {row[1]!r}")
            continue
        print(f"  [OK]   {citation:25} -> {row[1][:60]}")

    # 3. Spot searches (FTS5)
    print("\n=== Spot searches (5) ===")
    for query, expected, kind_filter in SPOT_SEARCHES:
        if kind_filter:
            sql = """SELECT citation, bm25(sections_fts, 4.0, 4.0, 1.0) AS rank
                     FROM sections_fts WHERE sections_fts MATCH ? AND kind = ?
                     ORDER BY rank LIMIT 3"""
            params: tuple = (query, kind_filter)
        else:
            sql = """SELECT citation, bm25(sections_fts, 4.0, 4.0, 1.0) AS rank
                     FROM sections_fts WHERE sections_fts MATCH ?
                     ORDER BY rank LIMIT 3"""
            params = (query,)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            failures.append(f"search FTS5 error for {query!r}: {exc}")
            print(f"  [FAIL] {query!r}: {exc}")
            continue
        cites_top3 = [r[0] for r in rows]
        # For chapter-level expectations like "MPEP 800", any record in
        # that chapter (800-899, etc.) counts. Compute the chapter of each
        # hit and compare.
        if expected.endswith("00") and len(expected.split()) == 2:
            target_chapter = expected.split()[1]  # e.g. "800"
            def _chapter_of(cite: str) -> str:
                if not cite.startswith("MPEP "):
                    return ""
                num = cite.split()[1].split(".")[0]  # "807" or "2141"
                if len(num) <= 2:
                    return num
                return num[:-2] + "00"
            ok = any(_chapter_of(c) == target_chapter for c in cites_top3)
        else:
            ok = expected in cites_top3
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] {query[:35]:35} -> {cites_top3}")
        if not ok:
            failures.append(f"search {query!r}: expected {expected} in top 3, got {cites_top3}")

    # Summary
    print(f"\n{'=' * 60}")
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("ALL VERIFICATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
