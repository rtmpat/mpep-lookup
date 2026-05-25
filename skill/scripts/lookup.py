"""Returns verbatim text from MPEP corpus.

SKILL.md's Verification Pass enforces no-paraphrasing; do not paraphrase
script output before quoting to user.

Usage:
    python scripts/lookup.py "MPEP 2141"
    python scripts/lookup.py "35 USC 102 (pre-AIA)"
    python scripts/lookup.py --json "37 CFR 1.131"
    python scripts/lookup.py --max-words 5000 "MPEP 2106"
"""

import argparse
import json
import sqlite3
import sys

from _common import connect_db, format_record, parse_citation


_SUPER_COLS = (
    "affected_citation", "status", "change_detail", "source_memo", "memo_date",
    "effective_date", "legal_trigger", "source_pdf_url", "pdf_bundled",
    "pdf_local_path", "in_corpus", "revised_text_md", "redline_md", "summary",
)


def _fetch_supersessions(conn: sqlite3.Connection, normalized: str) -> list[dict]:
    """Supersessions whose affected provision linkage matches this record.

    One lookup can surface several (e.g. MPEP 2106.04(d) is linked by both the
    subsection III addition and the 2106.04(d)(1) revision). Returns [] if the
    supersessions table is absent (a pre-1.1.0 DB) rather than crashing - the
    build, not runtime, is where a missing table must fail loud.
    """
    try:
        rows = conn.execute(
            f"SELECT {', '.join(_SUPER_COLS)} FROM supersessions "
            "WHERE affected_citation_normalized = ? ORDER BY id",
            (normalized,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(_SUPER_COLS, r)) for r in rows]


def _format_supersession_block(supers: list[dict]) -> str:
    """Render a clearly-labeled SUPERSEDED block to append after the record."""
    bar = "=" * 72
    out = [
        "",
        bar,
        f"SUPERSEDED IN PART -- {len(supers)} post-revision USPTO update(s) "
        "affect this provision",
        "The text above is the published edition (R-01.2024). For the affected "
        "passages, quote the verbatim revised text below and pin-cite the memo.",
        bar,
    ]
    for i, s in enumerate(supers, 1):
        out.append("")
        out.append(f"[{i}] {s['affected_citation']} -- {s['status'].upper()}")
        out.append(f"    Memo: {s['source_memo']} ({s['memo_date']})")
        if s.get("legal_trigger"):
            out.append(f"    Trigger: {s['legal_trigger']}")
        out.append(f"    Effective: {s['effective_date']}")
        pdf = f"    Source PDF: {s['source_pdf_url']}"
        if s.get("pdf_bundled") and s.get("pdf_local_path"):
            pdf += f"  (bundled: {s['pdf_local_path']})"
        out.append(pdf)
        out.append(f"    Summary: {s['summary']}")
        if s.get("change_detail"):
            out.append(f"    Change: {s['change_detail']}")
        if s.get("revised_text_md"):
            out.append("")
            out.append("    Revised text (verbatim from the memo):")
            for ln in s["revised_text_md"].splitlines():
                out.append(f"      {ln}" if ln else "")
        if s.get("redline_md"):
            out.append("")
            out.append("    Redline ([[ins:...]] added, [[del:...]] removed):")
            for ln in s["redline_md"].splitlines():
                out.append(f"      {ln}" if ln else "")
    return "\n".join(out)


def _truncate_to_words(body: str, max_words: int) -> tuple[str, int, int]:
    """Truncate body_md to max_words words. Return (truncated, used, total)."""
    words = body.split()
    total = len(words)
    if total <= max_words:
        return body, total, total
    truncated = " ".join(words[:max_words])
    truncated += (
        f"\n\n[...truncated to {max_words} words; full body has {total} words. "
        "Re-run without --max-words for full text.]"
    )
    return truncated, max_words, total


def main() -> int:
    p = argparse.ArgumentParser(description="Look up an MPEP / 35 USC / 37 CFR / Form Paragraph citation.")
    p.add_argument("citation", help='e.g., "MPEP 2141", "35 USC 102", "37 CFR 1.131"')
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    p.add_argument("--max-words", type=int, default=None,
                   help="Truncate body to N words (default: full body)")
    args = p.parse_args()

    if not args.citation.strip():
        print("Usage: lookup.py <citation>", file=sys.stderr)
        return 1

    try:
        parsed = parse_citation(args.citation)
    except ValueError as exc:
        print(f"Bad citation format: {exc}", file=sys.stderr)
        return 1

    conn = connect_db()
    row = conn.execute(
        """SELECT citation, citation_normalized, title, kind, chapter,
                  parent_citation, revision, body_md, source_url, word_count
           FROM sections WHERE citation_normalized = ?""",
        (parsed["citation_normalized"],),
    ).fetchone()

    if row is None:
        # Suggest similar citations
        like_pattern = parsed["citation_normalized"].rstrip("_") + "%"
        suggestions = conn.execute(
            "SELECT citation FROM sections WHERE citation_normalized LIKE ? LIMIT 8",
            (like_pattern,),
        ).fetchall()
        suggestion_list = [s[0] for s in suggestions]
        if args.json:
            print(json.dumps({
                "hit": None,
                "citation_normalized": parsed["citation_normalized"],
                "input": args.citation,
                "suggestions": suggestion_list,
            }, indent=2))
        else:
            print(f"Not found: {args.citation} (normalized: {parsed['citation_normalized']})", file=sys.stderr)
            if suggestion_list:
                print("Did you mean:", file=sys.stderr)
                for s in suggestion_list:
                    print(f"  {s}", file=sys.stderr)
        return 2

    record = {
        "citation": row[0],
        "citation_normalized": row[1],
        "title": row[2],
        "kind": row[3],
        "chapter": row[4],
        "parent_citation": row[5],
        "revision": row[6],
        "body_md": row[7],
        "source_url": row[8],
        "word_count": row[9],
    }

    if args.max_words is not None:
        truncated, used, total = _truncate_to_words(record["body_md"], args.max_words)
        record["body_md"] = truncated
        record["body_truncated"] = True
        record["word_count_full"] = total
        record["word_count_returned"] = used

    # Surface any post-revision supersessions affecting this provision so the
    # caller never quotes stale text without seeing the update.
    supers = _fetch_supersessions(conn, parsed["citation_normalized"])

    if args.json:
        record["supersessions"] = supers
        print(format_record(record, as_json=True))
    else:
        print(format_record(record, as_json=False))
        if supers:
            print(_format_supersession_block(supers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
