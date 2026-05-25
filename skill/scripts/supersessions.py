"""Search post-revision supersessions directly.

Supersessions are USPTO memos that override part of the published MPEP edition
before the next revision folds them in. lookup.py surfaces them automatically
when you fetch an affected provision; this script searches them on their own.

SKILL.md's Verification Pass enforces no-paraphrasing; revised_text_md is the
memo's verbatim text - quote it, do not paraphrase, and pin-cite the memo.

Usage:
    python scripts/supersessions.py --list                 # all, grouped by memo
    python scripts/supersessions.py "MPEP 2106.05(a)"       # affecting a provision
    python scripts/supersessions.py --memo desjardins       # one memo's changes
    python scripts/supersessions.py --search "machine learning"   # FTS over text
    python scripts/supersessions.py --json --list
"""

import argparse
import json
import sqlite3
import sys

from _common import connect_db, normalize

_COLS = (
    "affected_citation", "affected_citation_normalized", "kind_affected", "status",
    "change_detail", "source_memo", "memo_slug", "memo_date", "effective_date",
    "legal_trigger", "source_pdf_url", "pdf_bundled", "pdf_local_path", "in_corpus",
    "revised_text_md", "redline_md", "summary",
)


def _require_table(conn: sqlite3.Connection) -> None:
    """Fail loud if the supersessions table is absent (a pre-1.1.0 DB)."""
    try:
        conn.execute("SELECT 1 FROM supersessions LIMIT 1")
    except sqlite3.OperationalError:
        print("No supersessions table in this DB (pre-1.1.0 build). "
              "Rebuild with build/03_build_database.py.", file=sys.stderr)
        sys.exit(3)


def _rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[dict]:
    sql = f"SELECT {', '.join(_COLS)} FROM supersessions"
    if where:
        sql += f" WHERE {where}"
    sql += " ORDER BY memo_date, affected_citation_normalized, id"
    return [dict(zip(_COLS, r)) for r in conn.execute(sql, params).fetchall()]


def _print_compact(rows: list[dict]) -> None:
    """One line per supersession, grouped by memo."""
    by_memo: dict[str, list[dict]] = {}
    for r in rows:
        by_memo.setdefault(r["source_memo"], []).append(r)
    for memo, items in by_memo.items():
        s0 = items[0]
        print(f"\n{memo} ({s0['memo_date']})")
        print(f"  Source PDF: {s0['source_pdf_url']}"
              + (f"  (bundled: {s0['pdf_local_path']})" if s0["pdf_bundled"] else ""))
        for r in items:
            if r["in_corpus"]:
                marker = ""
            elif r["affected_citation_normalized"]:
                marker = "  [no standalone record; surfaced via parent]"
            else:
                marker = "  [not in corpus]"
            print(f"  [{r['status']:7}] {r['affected_citation']}{marker}")
            print(f"            {r['summary']}")


def _print_full(rows: list[dict]) -> None:
    """Full detail incl. verbatim revised text and redline."""
    for i, r in enumerate(rows, 1):
        print(f"\n{'=' * 72}")
        print(f"[{i}] {r['affected_citation']} -- {r['status'].upper()} "
              f"({r['kind_affected']})")
        print(f"    Memo: {r['source_memo']} ({r['memo_date']})")
        if r["legal_trigger"]:
            print(f"    Trigger: {r['legal_trigger']}")
        print(f"    Effective: {r['effective_date']}")
        pdf = f"    Source PDF: {r['source_pdf_url']}"
        if r["pdf_bundled"] and r["pdf_local_path"]:
            pdf += f"  (bundled: {r['pdf_local_path']})"
        print(pdf)
        print(f"    Summary: {r['summary']}")
        if r["change_detail"]:
            print(f"    Change: {r['change_detail']}")
        if r["revised_text_md"]:
            print("\n    Revised text (verbatim from the memo):")
            for ln in r["revised_text_md"].splitlines():
                print(f"      {ln}" if ln else "")
        if r["redline_md"]:
            print("\n    Redline ([[ins:...]] added, [[del:...]] removed):")
            for ln in r["redline_md"].splitlines():
                print(f"      {ln}" if ln else "")


def main() -> int:
    p = argparse.ArgumentParser(description="Search MPEP supersessions directly.")
    p.add_argument("query", nargs="?", default=None,
                   help="A citation (default) or, with --search, an FTS5 query")
    p.add_argument("--list", action="store_true", help="List all supersessions")
    p.add_argument("--memo", default=None, help="Filter to one memo slug")
    p.add_argument("--search", action="store_true",
                   help="Treat the query as an FTS5 search over supersession text")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    args = p.parse_args()

    conn = connect_db()
    _require_table(conn)

    if args.list:
        rows = _rows(conn, "", ())
    elif args.memo:
        rows = _rows(conn, "memo_slug = ?", (args.memo,))
        if not rows:
            print(f"No memo with slug {args.memo!r}. Known: "
                  + ", ".join(s[0] for s in conn.execute(
                      "SELECT DISTINCT memo_slug FROM supersessions ORDER BY 1")),
                  file=sys.stderr)
            return 2
    elif args.search:
        if not args.query or not args.query.strip():
            print("Usage: supersessions.py --search <fts5_query>", file=sys.stderr)
            return 1
        cols = ", ".join(f"s.{c}" for c in _COLS)
        try:
            rows = [dict(zip(_COLS, r)) for r in conn.execute(
                f"SELECT {cols} FROM supersessions_fts f "
                "JOIN supersessions s ON s.id = f.rowid "
                "WHERE supersessions_fts MATCH ? ORDER BY rank", (args.query,)).fetchall()]
        except sqlite3.OperationalError as exc:
            print(f"FTS5 query syntax error: {exc}", file=sys.stderr)
            return 1
    elif args.query:
        norm = normalize(args.query)
        rows = _rows(conn, "affected_citation_normalized = ? OR affected_citation LIKE ?",
                     (norm, f"%{args.query}%"))
    else:
        p.print_usage(sys.stderr)
        print("Provide a citation, or use --list / --memo / --search.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"supersessions": rows, "count": len(rows)},
                         ensure_ascii=True, indent=2))
        return 0

    if not rows:
        target = args.query or args.memo or "all"
        print(f"No supersessions match {target!r}.")
        return 0

    if args.list:
        _print_compact(rows)
    else:
        _print_full(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
