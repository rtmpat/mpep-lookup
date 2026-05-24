"""Returns verbatim FTS5 search hits over the MPEP corpus.

By default the search is index-aware: it fuses keyword (FTS5) relevance with
the USPTO Subject Matter Index. Result sections are tiered:

  1. "index+keyword" - matched by the keyword query AND referenced by a
     matching Subject Matter Index entry (highest confidence on-topic),
  2. "index"         - referenced by a matching index entry,
  3. "keyword"       - keyword-only hit with no index corroboration.

Statutes and CFR rules are not referenced by the index but stay
keyword-competitive: they rank by keyword relevance in the middle band rather
than sinking below index-referenced sections. Form Paragraphs are templates,
not authority, so keyword-only Form Paragraph hits are ranked last (tier 3).
Index entries themselves are never returned as results - they are a ranking
signal only.

One ranking refinement applies in every mode (it improves "find the right
authority", independent of index fusion): a modest, log-scaled authority bump
favors records cited by many others (inbound_refs) - the rule the rest of the
MPEP keeps citing ranks higher. So 35 USC 102 (cited 387x) ranks at the top
for "novelty", and 37 CFR 1.131 (cited ~110x) climbs out of the long-document
BM25 penalty into the affidavit-rule cluster. Kept modest (alpha 0.5) so it
breaks ties without overriding keyword relevance (e.g. MPEP 2141 still leads
"obviousness").

Flags:
  --keyword-only   plain FTS5 search, no index fusion (index entries excluded)
  --include-index  plain FTS5 search that DOES surface index entries as hits
  --kind KIND      scope to one record type (no fusion)
See index_lookup.py to route a topic explicitly through the index.

SKILL.md's Verification Pass enforces no-paraphrasing; do not paraphrase
script output before quoting to user.

Usage:
    python scripts/search.py "obviousness"
    python scripts/search.py "affidavit" --kind cfr_rule
    python scripts/search.py --keyword-only '"reasonable expectation of success"'
    python scripts/search.py --json "analogous art"
"""

import argparse
import json
import math
import re
import sqlite3
import sys

from _common import connect_db, parse_citation

# bm25 weights for the 3 indexed FTS columns (citation, title, body_md).
# citation+title are boosted 4x over body so topic/section matches beat raw
# body keyword density. (Boosting title higher over-rewards short titles that
# merely stem-match the query, e.g. "Obviously informal application" for
# "obviousness", so the authority bump below does the heavier lifting instead.)
_BM25 = "bm25(sections_fts, 4.0, 4.0, 1.0)"

# Modest, log-scaled authority bump. inbound_refs is the number of other records
# that cite this one (built in 03_build_database.py). bm25 is negative (more
# negative = more relevant); subtracting a positive bonus ranks a well-cited
# record higher. log1p keeps a 100-vs-1 gap moderate, not a landslide.
_REF_ALPHA = 0.5

# Index entry bodies store referenced sections as "MPEP <num>" tokens; see
# build/02_parse_to_markdown.py:_index_body_md.
_REF_RE = re.compile(r"MPEP\s+\d[0-9A-Za-z.()\-]*")

# Pre-fusion / pre-rerank candidate pools (final output is capped by --limit).
_KW_POOL = 60
_IDX_ENTRY_POOL = 12


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FTS5 search over the MPEP corpus (Subject Matter Index-aware by default).")
    p.add_argument("query", help='FTS5 query, e.g. \'"reasonable expectation"\'')
    p.add_argument("--limit", type=int, default=10, help="Max results (default 10, hard cap 100)")
    p.add_argument("--kind", default=None,
                   help="Filter by kind: mpep_section / statute / cfr_rule / form_paragraph")
    p.add_argument("--chapter", default=None, help="Filter by MPEP chapter (e.g. 2100)")
    p.add_argument("--keyword-only", action="store_true",
                   help="Plain FTS5 search without Subject Matter Index fusion")
    p.add_argument("--include-index", action="store_true",
                   help="Plain FTS5 search that surfaces index entries as hits")
    p.add_argument("--snippet-words", type=int, default=20,
                   help="Words around match in snippet (default 20)")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    return p


def _fts_error(exc: sqlite3.OperationalError) -> int:
    print(f"FTS5 query syntax error: {exc}. See references/search_syntax.md.", file=sys.stderr)
    return 1


def _ref_score(bm25: float, inbound_refs: int) -> float:
    """Blend keyword relevance with the inbound-reference authority bump."""
    return bm25 - _REF_ALPHA * math.log1p(inbound_refs or 0)


def _norm_mpep_ref(ref: str) -> str | None:
    """Normalize an index body ref ('MPEP 711.04(a)') to its section slug."""
    try:
        return parse_citation(ref)["citation_normalized"]
    except ValueError:
        return None


def _refs_by_rowid(conn: sqlite3.Connection, rowids: list[int]) -> dict[int, int]:
    if not rowids:
        return {}
    qmarks = ",".join("?" * len(rowids))
    return {i: n for i, n in conn.execute(
        f"SELECT id, inbound_refs FROM sections WHERE id IN ({qmarks})", rowids)}


def _emit(hits: list[dict], args: argparse.Namespace, total_records: int) -> None:
    if args.json:
        print(json.dumps({
            "hits": hits,
            "fts_query": args.query,
            "fts_query_parsed_ok": True,
            "total_records_searched": total_records,
            "filters": {"kind": args.kind, "chapter": args.chapter},
        }, indent=2))
        return
    if not hits:
        print(f"No matches for FTS5 query {args.query!r} across {total_records} "
              f"records (query parsed cleanly).")
        return
    for h in hits:
        rank = h.get("rank")
        rankstr = f"rank {rank:.2f}" if rank is not None else "via index"
        refs = h.get("inbound_refs")
        refstr = f", refs {refs}" if refs else ""
        print(f"\n[{h['kind']:14}] {h['citation']:30} ({h['match']}, {rankstr}{refstr})")
        print(f"  Title: {h['title']}")
        if h.get("via_index_term"):
            print(f"  Index term: {h['via_index_term']}")
        print(f"  Snippet: {h['snippet']}")


def _keyword_search(conn: sqlite3.Connection, args: argparse.Namespace,
                    total_records: int) -> int:
    """Plain FTS5 search (no index fusion) with title boost + authority bump.

    Used for --kind / --keyword-only / --include-index.
    """
    where = ["sections_fts MATCH ?"]
    params: list = [args.query]
    if args.kind:
        where.append("kind = ?")
        params.append(args.kind)
    elif not args.include_index:
        where.append("kind != 'index_entry'")
    if args.chapter:
        where.append("chapter = ?")
        params.append(args.chapter)
    # Fetch a pool (not just --limit) so the authority bump can pull a
    # well-cited record up from deeper in the keyword ranking.
    pool = max(args.limit, _KW_POOL)
    sql = f"""SELECT rowid, citation, title, kind, chapter,
        snippet(sections_fts, 2, '<b>', '</b>', '...', ?) AS snippet,
        {_BM25} AS rank
        FROM sections_fts
        WHERE {' AND '.join(where)}
        ORDER BY rank
        LIMIT ?"""
    try:
        rows = conn.execute(sql, [args.snippet_words] + params + [pool]).fetchall()
    except sqlite3.OperationalError as exc:
        return _fts_error(exc)
    refs = _refs_by_rowid(conn, [r[0] for r in rows])
    scored = []
    for r in rows:
        n = refs.get(r[0], 0)
        hit = {"rowid": r[0], "citation": r[1], "title": r[2], "kind": r[3],
               "chapter": r[4], "snippet": r[5], "rank": r[6], "match": "keyword",
               "inbound_refs": n}
        scored.append((_ref_score(r[6], n), hit))
    scored.sort(key=lambda x: x[0])
    _emit([h for _, h in scored[: args.limit]], args, total_records)
    return 0


def _fused_search(conn: sqlite3.Connection, args: argparse.Namespace,
                  total_records: int) -> int:
    """Index-aware default: fuse keyword relevance, the Subject Matter Index,
    and the inbound-reference authority bump."""
    sw = args.snippet_words
    try:
        kw_rows = conn.execute(
            f"""SELECT rowid, citation, title, kind, chapter,
                   snippet(sections_fts, 2, '<b>', '</b>', '...', ?) AS snippet,
                   {_BM25} AS rank
                FROM sections_fts
                WHERE sections_fts MATCH ? AND kind != 'index_entry'
                ORDER BY rank LIMIT ?""",
            (sw, args.query, _KW_POOL),
        ).fetchall()
        idx_rows = conn.execute(
            f"""SELECT title, body_md, {_BM25} AS rank
                FROM sections_fts
                WHERE sections_fts MATCH ? AND kind = 'index_entry'
                ORDER BY rank LIMIT ?""",
            (args.query, _IDX_ENTRY_POOL),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return _fts_error(exc)

    # Map keyword-hit rowids -> (citation_normalized, inbound_refs).
    meta_by_rowid: dict[int, tuple[str, int]] = {}
    rowids = [r[0] for r in kw_rows]
    if rowids:
        qmarks = ",".join("?" * len(rowids))
        for _id, slug, refs in conn.execute(
            f"SELECT id, citation_normalized, inbound_refs FROM sections WHERE id IN ({qmarks})",
            rowids,
        ):
            meta_by_rowid[_id] = (slug, refs)
    kw_by_slug: dict[str, dict] = {}
    for r in kw_rows:
        meta = meta_by_rowid.get(r[0])
        if meta is None or meta[0] in kw_by_slug:
            continue
        slug, refs = meta
        kw_by_slug[slug] = {"rowid": r[0], "citation": r[1], "title": r[2], "kind": r[3],
                            "chapter": r[4], "snippet": r[5], "rank": r[6], "refs": refs}

    # Resolve matching index entries -> referenced section slugs.
    idx_by_slug: dict[str, dict] = {}
    for title, body_md, rank in idx_rows:
        for m in _REF_RE.finditer(body_md or ""):
            slug = _norm_mpep_ref(m.group(0))
            if slug is None:
                continue
            cur = idx_by_slug.get(slug)
            if cur is None or rank < cur["rank"]:
                idx_by_slug[slug] = {"rank": rank, "term": title}

    # Fetch records for index-only slugs (not already in the keyword pool).
    rec_by_slug: dict[str, dict] = {}
    idx_only = [s for s in idx_by_slug if s not in kw_by_slug]
    if idx_only:
        qmarks = ",".join("?" * len(idx_only))
        for slug, citation, title, kind, chapter, body_md, refs in conn.execute(
            f"""SELECT citation_normalized, citation, title, kind, chapter, body_md, inbound_refs
                FROM sections WHERE citation_normalized IN ({qmarks})""", idx_only
        ):
            rec_by_slug[slug] = {
                "rowid": None, "citation": citation, "title": title, "kind": kind,
                "chapter": chapter, "rank": None, "refs": refs,
                "snippet": " ".join((body_md or "").split()[:sw]),  # leading excerpt
            }

    # Tier + score each candidate, then sort (tier asc, blended score asc).
    candidates: list[tuple[int, float, dict]] = []
    for slug in set(kw_by_slug) | set(idx_by_slug):
        in_kw = slug in kw_by_slug
        in_idx = slug in idx_by_slug
        base = kw_by_slug.get(slug) or rec_by_slug.get(slug)
        if base is None:
            continue  # index ref that does not resolve to a stored section
        refs = base["refs"]
        if in_kw and in_idx:
            tier, bm25, match = 0, base["rank"], "index+keyword"
        elif in_idx and not in_kw:
            tier, bm25, match = 1, idx_by_slug[slug]["rank"], "index"
        elif base["kind"] == "form_paragraph":
            tier, bm25, match = 3, base["rank"], "keyword"  # templates, not authority: last
        elif base["kind"] != "mpep_section":
            tier, bm25, match = 1, base["rank"], "keyword"  # statutes / CFR rules stay competitive
        else:
            tier, bm25, match = 2, base["rank"], "keyword"  # MPEP keyword-only
        hit = {"rowid": base["rowid"], "citation": base["citation"], "title": base["title"],
               "kind": base["kind"], "chapter": base["chapter"], "snippet": base["snippet"],
               "rank": base["rank"], "match": match, "tier": tier, "inbound_refs": refs}
        if in_idx:
            hit["via_index_term"] = idx_by_slug[slug]["term"]
        candidates.append((tier, _ref_score(bm25, refs), hit))

    candidates.sort(key=lambda c: (c[0], c[1]))
    _emit([c[2] for c in candidates[: args.limit]], args, total_records)
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    if not args.query.strip():
        print("Usage: search.py <fts5_query>", file=sys.stderr)
        return 1
    if args.limit > 100:
        print(f"--limit {args.limit} exceeds hard cap 100", file=sys.stderr)
        return 1
    if args.limit < 1:
        print("--limit must be >= 1", file=sys.stderr)
        return 1

    conn = connect_db()
    total_records = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    if args.kind or args.keyword_only or args.include_index:
        return _keyword_search(conn, args, total_records)
    return _fused_search(conn, args, total_records)


if __name__ == "__main__":
    sys.exit(main())
