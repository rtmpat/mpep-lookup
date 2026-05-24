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
import sys

from _common import connect_db, format_record, parse_citation


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

    print(format_record(record, as_json=args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
