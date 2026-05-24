"""Route a topic through the Subject Matter Index to verbatim MPEP sections.

Flavor 2 routing: match a Subject Matter Index term, follow its references to
the actual MPEP section records, and return those verbatim. This is the
curated-index layer that complements the keyword search in search.py - the
USPTO editors chose which sections a topic points to, which is often more
precise than raw term-frequency ranking.

All quoted text comes from the resolved section records; SKILL.md's
Verification Pass still applies. Index entries themselves are excluded from
search.py results by default (they are signposts, not authority).

Usage:
    python scripts/index_lookup.py "double patenting"
    python scripts/index_lookup.py --entries 3 --sections 8 "reissue oath"
    python scripts/index_lookup.py --json "incomplete reply"
"""

import argparse
import json
import re
import sqlite3
import sys

from _common import connect_db, format_record, parse_citation

# Refs are stored in an index entry's body_md as "MPEP <num>" tokens; see
# build/02_parse_to_markdown.py:_index_body_md (option-c design).
_REF_RE = re.compile(r"MPEP\s+\d[0-9A-Za-z.()\-]*")

_SECTION_COLS = [
    "citation", "citation_normalized", "title", "kind", "chapter",
    "parent_citation", "revision", "body_md", "source_url", "word_count",
]


def _extract_ref_slugs(body_md: str) -> list[str]:
    """Normalized citation slugs for the MPEP refs named in an index body."""
    slugs: list[str] = []
    seen: set[str] = set()
    for m in _REF_RE.finditer(body_md):
        try:
            parsed = parse_citation(m.group(0))
        except ValueError:
            continue
        slug = parsed["citation_normalized"]
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


def _parse_see_also(body_md: str) -> list[str]:
    """Cross-referenced index terms named in an index body (if any)."""
    m = re.search(r"See also \(index\):\s*(.+)$", body_md, flags=re.MULTILINE)
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(";") if t.strip()]


def main() -> int:
    p = argparse.ArgumentParser(
        description="Route a topic via the Subject Matter Index to verbatim MPEP sections.")
    p.add_argument("term", help='index topic, e.g. "double patenting"')
    p.add_argument("--entries", type=int, default=5,
                   help="Max matching index entries to consider (default 5)")
    p.add_argument("--sections", type=int, default=8,
                   help="Max distinct sections to resolve verbatim (default 8)")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args()

    if not args.term.strip():
        print("Usage: index_lookup.py <term>", file=sys.stderr)
        return 1
    if args.entries < 1 or args.sections < 1:
        print("--entries and --sections must be >= 1", file=sys.stderr)
        return 1

    conn = connect_db()
    try:
        entry_rows = conn.execute(
            """SELECT citation, title, body_md
               FROM sections_fts
               WHERE sections_fts MATCH ? AND kind = 'index_entry'
               ORDER BY bm25(sections_fts, 4.0, 4.0, 1.0)
               LIMIT ?""",
            (args.term, args.entries),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        print(f"FTS5 query syntax error: {exc}. See references/search_syntax.md.",
              file=sys.stderr)
        return 1

    if not entry_rows:
        if args.json:
            print(json.dumps({"term": args.term, "entries": [], "sections": []}, indent=2))
        else:
            print(f"No Subject Matter Index entry matched {args.term!r} "
                  f"(query parsed cleanly). Try search.py for a full-text search.")
        return 0

    # Collect resolved section slugs across the matched entries, in rank order,
    # deduped, capped by --sections.
    entries_out = []
    resolved_slugs: list[str] = []
    seen: set[str] = set()
    for citation, title, body_md in entry_rows:
        ref_slugs = _extract_ref_slugs(body_md)
        entries_out.append({
            "citation": citation,
            "title": title,
            "ref_slugs": ref_slugs,
            "see_also": _parse_see_also(body_md),
        })
        for slug in ref_slugs:
            if slug not in seen:
                seen.add(slug)
                resolved_slugs.append(slug)

    sections_out = []
    for slug in resolved_slugs[: args.sections]:
        row = conn.execute(
            f"""SELECT {', '.join(_SECTION_COLS)}
                FROM sections WHERE citation_normalized = ?""",
            (slug,),
        ).fetchone()
        if row is None:
            sections_out.append({"citation_normalized": slug, "hit": False})
        else:
            rec = {k: row[i] for i, k in enumerate(_SECTION_COLS)}
            rec["hit"] = True
            sections_out.append(rec)

    if args.json:
        print(json.dumps({"term": args.term, "entries": entries_out,
                          "sections": sections_out}, indent=2))
        return 0

    print(f"Subject Matter Index routing for {args.term!r}:\n")
    for e in entries_out:
        print(f"  [{e['title']}]")
        if e["ref_slugs"]:
            print(f"     sections: {', '.join(e['ref_slugs'])}")
        if e["see_also"]:
            print(f"     see also (index): {'; '.join(e['see_also'])}")
    print("\n--- verbatim sections (resolved from the index references) ---")
    for s in sections_out:
        if not s.get("hit"):
            print(f"\n[not in corpus: {s['citation_normalized']}]")
            continue
        print("\n" + format_record(s, as_json=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
