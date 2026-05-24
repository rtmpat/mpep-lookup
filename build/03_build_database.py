"""Build skill/data/mpep.db from intermediate/*.md.

Schema is the canonical corpus schema. Triggers keep sections_fts
in sync with sections on insert/update/delete. After all inserts run
'optimize' on the FTS index, then VACUUM and ANALYZE.

Usage: python build/03_build_database.py
"""

import json
import pathlib
import re
import sqlite3
import sys
from datetime import datetime, timezone


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "skill" / "data" / "mpep.db"

# Import the citation parser from skill/scripts (single source of truth) so the
# inbound-reference graph normalizes citations the same way records were keyed.
sys.path.insert(0, str(REPO_ROOT / "skill" / "scripts"))
from _common import parse_citation  # noqa: E402

SCHEMA_SQL = """
CREATE TABLE sections (
  id                   INTEGER PRIMARY KEY,
  citation             TEXT NOT NULL,
  citation_normalized  TEXT NOT NULL UNIQUE,
  title                TEXT NOT NULL,
  kind                 TEXT NOT NULL CHECK (kind IN (
                          'mpep_section', 'statute', 'cfr_rule',
                          'form_paragraph', 'index_entry', 'appendix'
                       )),
  chapter              TEXT,
  parent_citation      TEXT,
  revision             TEXT,
  body_md              TEXT NOT NULL,
  source_url           TEXT NOT NULL,
  word_count           INTEGER NOT NULL,
  inbound_refs         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_sections_citation_normalized ON sections(citation_normalized);
CREATE INDEX idx_sections_kind                ON sections(kind);
CREATE INDEX idx_sections_chapter             ON sections(chapter);
CREATE INDEX idx_sections_parent              ON sections(parent_citation);

CREATE VIRTUAL TABLE sections_fts USING fts5(
  citation, title, body_md,
  kind UNINDEXED, chapter UNINDEXED,
  content='sections', content_rowid='id',
  tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TRIGGER sections_ai AFTER INSERT ON sections BEGIN
  INSERT INTO sections_fts(rowid, citation, title, body_md, kind, chapter)
  VALUES (new.id, new.citation, new.title, new.body_md, new.kind, new.chapter);
END;

CREATE TRIGGER sections_ad AFTER DELETE ON sections BEGIN
  INSERT INTO sections_fts(sections_fts, rowid, citation, title, body_md, kind, chapter)
  VALUES('delete', old.id, old.citation, old.title, old.body_md, old.kind, old.chapter);
END;

CREATE TRIGGER sections_au AFTER UPDATE ON sections BEGIN
  INSERT INTO sections_fts(sections_fts, rowid, citation, title, body_md, kind, chapter)
  VALUES('delete', old.id, old.citation, old.title, old.body_md, old.kind, old.chapter);
  INSERT INTO sections_fts(rowid, citation, title, body_md, kind, chapter)
  VALUES (new.id, new.citation, new.title, new.body_md, new.kind, new.chapter);
END;

CREATE TABLE metadata (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-like frontmatter (limited to our simple format)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("No frontmatter found")
    fm_text = m.group(1)
    body = m.group(2).lstrip("\n")
    fm: dict = {}
    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "null":
            fm[key] = None
        elif val.startswith('"') and val.endswith('"'):
            fm[key] = val[1:-1].replace('\\"', '"')
        else:
            fm[key] = val
    return fm, body


# --- inbound reference graph -------------------------------------------------

# In body_md the section symbol has been ascii-folded to "Section ", so MPEP
# cross-references read "MPEP Section 2144" or "MPEP 2143.02"; statutes/rules
# read "35 U.S.C. 102" / "37 CFR 1.131". Capture the number, rebuild a
# canonical citation, and normalize it through parse_citation (the SSOT).
_RE_REF_MPEP = re.compile(
    r"MPEP\s+(?:Section\s+|Sec\.?\s+)?(\d{2,4}(?:\.\d+)?(?:\([a-z0-9]+\))*)", re.IGNORECASE)
_RE_REF_USC = re.compile(r"35\s+U\.?\s*S\.?\s*C\.?\s+(\d{1,3})", re.IGNORECASE)
_RE_REF_CFR = re.compile(r"37\s+C\.?\s*F\.?\s*R\.?\s+(\d{1,3}\.\d+)", re.IGNORECASE)


def _ref_slug(text: str) -> str | None:
    """Normalize a reconstructed citation to its slug, or None if unparseable."""
    try:
        return parse_citation(text)["citation_normalized"]
    except ValueError:
        return None


def _referenced_slugs(body: str) -> set[str]:
    """Citation slugs referenced in a body (MPEP / 35 USC / 37 CFR)."""
    refs: set[str] = set()
    for m in _RE_REF_MPEP.finditer(body):
        s = _ref_slug("MPEP " + m.group(1))
        if s:
            refs.add(s)
    for m in _RE_REF_USC.finditer(body):
        s = _ref_slug("35 USC " + m.group(1))
        if s:
            refs.add(s)
    for m in _RE_REF_CFR.finditer(body):
        s = _ref_slug("37 CFR " + m.group(1))
        if s:
            refs.add(s)
    return refs


def _compute_inbound_refs(records: list[tuple[dict, str]]) -> dict[str, int]:
    """Inbound reference count per citation_normalized across the corpus.

    For each record, count how many OTHER records cite it (deduped per citing
    record). Only targets that exist in the corpus are counted. Used as a modest
    authority signal in search ranking (search.py).
    """
    valid = {fm["citation_normalized"] for fm, _ in records}
    inbound = {slug: 0 for slug in valid}
    for fm, body in records:
        if not body:
            continue
        self_slug = fm["citation_normalized"]
        for slug in _referenced_slugs(body):
            if slug in inbound and slug != self_slug:
                inbound[slug] += 1
    return inbound


def main() -> int:
    if DB_PATH.exists():
        DB_PATH.unlink()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA_SQL)

    intermediate = REPO_ROOT / "intermediate"
    md_files = sorted(intermediate.rglob("*.md"))
    if not md_files:
        print("No markdown files found under intermediate/", file=sys.stderr)
        return 1

    # Pass 1: parse all records so the inbound-reference graph can be computed
    # before inserting (avoids per-row FTS-trigger churn that UPDATEs would cause).
    records: list[tuple[dict, str]] = []
    failures: list[tuple[str, str]] = []
    for md_path in md_files:
        text = md_path.read_text(encoding="utf-8")
        try:
            fm, body = _parse_frontmatter(text)
        except ValueError as exc:
            failures.append((str(md_path), str(exc)))
            continue
        records.append((fm, body))

    inbound = _compute_inbound_refs(records)

    # Pass 2: insert with the precomputed inbound_refs.
    counts_by_kind: dict[str, int] = {}
    for fm, body in records:
        try:
            conn.execute(
                """INSERT INTO sections
                   (citation, citation_normalized, title, kind, chapter,
                    parent_citation, revision, body_md, source_url, word_count,
                    inbound_refs)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fm["citation"],
                    fm["citation_normalized"],
                    fm["title"],
                    fm["kind"],
                    fm.get("chapter"),
                    fm.get("parent_citation"),
                    fm.get("revision"),
                    body,
                    fm["source_url"],
                    len(body.split()),
                    inbound.get(fm["citation_normalized"], 0),
                ),
            )
        except (sqlite3.IntegrityError, KeyError) as exc:
            failures.append((fm.get("citation_normalized", str(fm)), f"INSERT failed: {exc}"))
            continue
        counts_by_kind[fm["kind"]] = counts_by_kind.get(fm["kind"], 0) + 1

    if failures:
        print(f"\n{len(failures)} insert failures:", file=sys.stderr)
        for path, msg in failures[:10]:
            print(f"  {path}: {msg}", file=sys.stderr)
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more", file=sys.stderr)

    conn.commit()

    # Optimize FTS
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('optimize')")
    conn.commit()

    # Populate metadata
    total = sum(counts_by_kind.values())
    metadata = {
        "source_revision": "R-01.2024",  # detected from any record's frontmatter
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_records": str(total),
        "record_counts_by_kind": json.dumps(counts_by_kind, sort_keys=True),
        "builder_version": "1.0.0",
        "corpus_source": "https://www.uspto.gov/web/offices/pac/mpep/",
    }
    # source_revision = the edition's revision. Each MPEP section heading
    # carries the revision it was last changed in (older, unchanged sections
    # keep older markers), so the edition is the NEWEST revision present:
    # the max by (year, revision-number). Malformed markers sort low and lose.
    def _rev_key(rev: str) -> tuple[int, int]:
        m = re.match(r"R-(\d+)\.(\d{4})$", rev or "")
        return (int(m.group(2)), int(m.group(1))) if m else (-1, -1)

    revisions = [r[0] for r in conn.execute(
        "SELECT DISTINCT revision FROM sections "
        "WHERE kind = 'mpep_section' AND revision IS NOT NULL")]
    valid = [r for r in revisions if _rev_key(r) != (-1, -1)]
    if valid:
        metadata["source_revision"] = max(valid, key=_rev_key)

    for k, v in metadata.items():
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.execute("VACUUM")
    conn.execute("ANALYZE")
    conn.close()

    print(f"\nBuilt {DB_PATH}")
    print(f"Total records: {total}")
    print("Counts by kind:")
    for k, n in sorted(counts_by_kind.items()):
        print(f"  {k:20} {n:>5}")
    print(f"DB size: {DB_PATH.stat().st_size:,} bytes")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
