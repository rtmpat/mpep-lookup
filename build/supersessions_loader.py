"""Load post-revision supersessions into skill/data/mpep.db (sidecar table).

A "supersession" is a USPTO memorandum that overrides part of the current
published MPEP edition before the next full revision folds it in. These memos
are unstructured PDFs, so their content is hand-transcribed into curated source
files under build/supersessions/ (the durable source of truth; the DB is
regenerable from them). This module is the single place that validates and
loads that source.

Design invariants (see docs/dev-history/LESSONS.md):
- Sidecar `supersessions` table; the `sections.kind` CHECK is untouched.
- FAIL LOUD. A new or changed memo that the architecture cannot fully account
  for must stop the build with a SupersessionError - never a silent partial
  load. Consistency is enforced four ways: live USPTO supersede-list (L),
  manifest (M), source dirs (S), and loaded records (R).
- Verbatim discipline. revised_text_md is the memo's verbatim "revised to read"
  text; FP memos that carry no verbatim text are notice-only (revised_text_md
  NULL). Nothing is fabricated here.
- Source PDFs are bundled smallest-first under a size cap; overflow falls back
  to a link only. The cap governs the binary archive ONLY - the transcription
  is always in the DB, so retrieval is never affected.

ASCII-only source (per project constraints).
"""

import hashlib
import json
import pathlib
import re
import sys

# Import the citation/ASCII helpers from the single source of truth. Mirror
# 03_build_database.py's sys.path insertion so build and runtime agree.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "skill" / "scripts"))
from _common import ascii_safe, normalize  # noqa: E402

SUPERSESSIONS_DIR = _REPO_ROOT / "build" / "supersessions"
# Where bundled PDFs are placed for the local/symlinked install and the package.
BUNDLE_DIR = _REPO_ROOT / "skill" / "references" / "supersessions_pdf"
# Path stored in the DB / shown to users is relative to the skill root.
BUNDLE_REL = "references/supersessions_pdf"

DEFAULT_PDF_BUNDLE_CAP_BYTES = 3 * 1024 * 1024  # 3 MiB

_VALID_STATUS = {"new", "revised", "removed", "added"}
_VALID_KIND = {"mpep_section", "statute", "cfr_rule", "form_paragraph"}
_VALID_CLASS = {"section_anc", "fp_memo"}


class SupersessionError(Exception):
    """Raised when supersession source/manifest cannot be fully handled.

    Always fatal to a build: silent partial loads are not acceptable.
    """


SCHEMA_SQL = """
CREATE TABLE supersessions (
  id                            INTEGER PRIMARY KEY,
  affected_citation             TEXT NOT NULL,
  affected_citation_normalized  TEXT,
  kind_affected                 TEXT NOT NULL CHECK (kind_affected IN (
                                   'mpep_section', 'statute', 'cfr_rule',
                                   'form_paragraph'
                                )),
  status                        TEXT NOT NULL CHECK (status IN (
                                   'new', 'revised', 'removed', 'added'
                                )),
  change_detail                 TEXT,
  source_memo                   TEXT NOT NULL,
  memo_slug                     TEXT NOT NULL,
  memo_date                     TEXT NOT NULL,
  effective_date                TEXT NOT NULL,
  legal_trigger                 TEXT,
  source_pdf_url                TEXT NOT NULL,
  pdf_bundled                   INTEGER NOT NULL DEFAULT 0,
  pdf_local_path                TEXT,
  in_corpus                     INTEGER NOT NULL,
  revised_text_md               TEXT,
  redline_md                    TEXT,
  summary                       TEXT NOT NULL
);

CREATE INDEX idx_super_affected ON supersessions(affected_citation_normalized);
CREATE INDEX idx_super_memo      ON supersessions(memo_slug);

CREATE VIRTUAL TABLE supersessions_fts USING fts5(
  affected_citation, source_memo, summary, revised_text_md,
  memo_slug UNINDEXED, status UNINDEXED,
  content='supersessions', content_rowid='id',
  tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TRIGGER supersessions_ai AFTER INSERT ON supersessions BEGIN
  INSERT INTO supersessions_fts(rowid, affected_citation, source_memo, summary,
                                revised_text_md, memo_slug, status)
  VALUES (new.id, new.affected_citation, new.source_memo, new.summary,
          new.revised_text_md, new.memo_slug, new.status);
END;

CREATE TRIGGER supersessions_ad AFTER DELETE ON supersessions BEGIN
  INSERT INTO supersessions_fts(supersessions_fts, rowid, affected_citation,
                                source_memo, summary, revised_text_md, memo_slug, status)
  VALUES('delete', old.id, old.affected_citation, old.source_memo, old.summary,
         old.revised_text_md, old.memo_slug, old.status);
END;

CREATE TRIGGER supersessions_au AFTER UPDATE ON supersessions BEGIN
  INSERT INTO supersessions_fts(supersessions_fts, rowid, affected_citation,
                                source_memo, summary, revised_text_md, memo_slug, status)
  VALUES('delete', old.id, old.affected_citation, old.source_memo, old.summary,
         old.revised_text_md, old.memo_slug, old.status);
  INSERT INTO supersessions_fts(rowid, affected_citation, source_memo, summary,
                                revised_text_md, memo_slug, status)
  VALUES (new.id, new.affected_citation, new.source_memo, new.summary,
          new.revised_text_md, new.memo_slug, new.status);
END;
"""


def create_schema(conn) -> None:
    """Create the supersessions table, FTS mirror, and sync triggers."""
    conn.executescript(SCHEMA_SQL)


# --- source-file parsing -----------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise SupersessionError("missing frontmatter")
    fm: dict = {}
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip()
        if val == "null":
            fm[key] = None
        elif val.startswith('"') and val.endswith('"'):
            fm[key] = val[1:-1].replace('\\"', '"')
        else:
            fm[key] = val
    return fm, m.group(2)


def _split_sections(body: str) -> dict[str, str]:
    """Split a markdown body into '## Heading' -> text blocks (case-folded keys)."""
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for i, mt in enumerate(matches):
        start = mt.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[mt.group(1).strip().lower()] = body[start:end].strip()
    return sections


_REDLINE_TOKEN_RE = re.compile(r"\[\[(?:ins|del):.*?\]\]", re.DOTALL)
_DEL_RE = re.compile(r"\[\[del:.*?\]\]", re.DOTALL)
_INS_RE = re.compile(r"\[\[ins:(.*?)\]\]", re.DOTALL)


def _validate_redline(text: str, where: str) -> None:
    """Reject malformed redline markup. Every [[ins:/[[del: must close with ]]."""
    stripped = _REDLINE_TOKEN_RE.sub("", text)
    if "[[" in stripped or "]]" in stripped:
        raise SupersessionError(
            f"{where}: malformed redline markup (unbalanced [[ins:]]/[[del:]])")


def _accept_all_redline(redline: str) -> str:
    """Apply the redline: drop [[del:...]], unwrap [[ins:...]] -> final text."""
    return _INS_RE.sub(r"\1", _DEL_RE.sub("", redline))


def _check_redline_matches_revised(redline: str, revised: str, where: str) -> None:
    """The redline's accept-all form must equal the revised text (whitespace
    aside). This catches transcription drift between the two fields - a redline
    you can't accept-all back to the verbatim revised text is wrong."""
    if " ".join(_accept_all_redline(redline).split()) != " ".join(revised.split()):
        raise SupersessionError(
            f"{where}: redline accept-all does not match the '## Revised text' "
            "block (transcription inconsistency). Fix the markers or the text.")


# --- PDF helpers -------------------------------------------------------------

def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def allocate_pdf_budget(memos: list[dict], cap_bytes: int) -> dict[str, bool]:
    """Greedy smallest-first: bundle PDFs while cumulative size <= cap_bytes.

    Returns {memo_slug: bundled?}. Overflow memos fall back to link-only.
    Deterministic: ties broken by slug.
    """
    order = sorted(memos, key=lambda m: (int(m["pdf_bytes"]), m["slug"]))
    bundled: dict[str, bool] = {}
    used = 0
    for m in order:
        size = int(m["pdf_bytes"])
        if used + size <= cap_bytes:
            bundled[m["slug"]] = True
            used += size
        else:
            bundled[m["slug"]] = False
    return bundled


# --- manifest ----------------------------------------------------------------

def load_manifest(manifest_path: pathlib.Path) -> dict:
    if not manifest_path.exists():
        raise SupersessionError(f"manifest not found: {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SupersessionError(f"manifest is not valid JSON: {exc}") from exc
    if "memos" not in data or not isinstance(data["memos"], list):
        raise SupersessionError("manifest missing a 'memos' list")
    required = {"slug", "title", "memo_date", "effective_date", "class",
                "source_pdf_url", "pdf_sha256", "pdf_bytes", "source_dir",
                "record_count"}
    seen: set[str] = set()
    for m in data["memos"]:
        missing = required - set(m)
        if missing:
            raise SupersessionError(
                f"manifest memo {m.get('slug', '?')!r} missing fields: {sorted(missing)}")
        if m["class"] not in _VALID_CLASS:
            raise SupersessionError(
                f"manifest memo {m['slug']!r} has unknown class {m['class']!r} "
                f"(handled: {sorted(_VALID_CLASS)})")
        if m["slug"] in seen:
            raise SupersessionError(f"duplicate manifest slug {m['slug']!r}")
        seen.add(m["slug"])
    return data


# --- live reconciliation (L vs M) --------------------------------------------

def reconcile_live(manifest: dict, live_pdf_urls, live_sha256) -> None:
    """Assert the live USPTO supersede-list matches the manifest exactly.

    Args:
        manifest: parsed manifest dict.
        live_pdf_urls: callable() -> iterable[str] of PDF URLs currently listed
            in the USPTO supersede section.
        live_sha256: callable(url) -> str sha256 of the currently-served PDF.

    Raises SupersessionError on ANY divergence: a new memo on the page not in
    the manifest (unincorporated), a manifest memo no longer on the page
    (folded into a new revision?), or a content hash mismatch (memo re-issued).
    Injectable callables keep this testable without network.
    """
    page = set(live_pdf_urls())
    declared = {m["source_pdf_url"]: m for m in manifest["memos"]}

    new_on_page = page - set(declared)
    if new_on_page:
        raise SupersessionError(
            "USPTO lists superseding document(s) not yet incorporated "
            f"(transcribe + add to manifest): {sorted(new_on_page)}")

    gone_from_page = set(declared) - page
    if gone_from_page:
        raise SupersessionError(
            "Manifest references document(s) no longer on the USPTO supersede "
            "list (folded into a new MPEP revision? confirm and bump "
            f"source_revision): {sorted(gone_from_page)}")

    for url, m in declared.items():
        served = live_sha256(url)
        if served != m["pdf_sha256"]:
            raise SupersessionError(
                f"Superseding document changed since transcription: {url} "
                f"(manifest sha256 {m['pdf_sha256'][:12]}..., served "
                f"{served[:12]}...). Re-verify the transcription.")


# --- strict offline loader (M vs S vs R) -------------------------------------

def _resolve_linkage(conn, affected_citation: str, links_to: str | None,
                     explicit_in_corpus: str | None, where: str) -> tuple[str | None, bool]:
    """Resolve (affected_citation_normalized, in_corpus).

    in_corpus is True only when the affected provision is itself a section
    record you can look up directly. A subsection with no own record (e.g.
    MPEP 2106.05(a) subsection I, or MPEP 2106.04(d)(1) which is not captured
    at all in R-01.2024) links to a parent via `links_to` so a lookup on the
    parent surfaces the supersession - but in_corpus is False because the
    provision itself is not independently retrievable. A provision with no
    parent either (e.g. a brand-new form paragraph) must declare
    `in_corpus: false` explicitly; an unresolved citation is a loud error,
    never a silent NULL.
    """
    own = normalize(affected_citation)
    if _slug_exists(conn, own):
        return own, True
    if links_to:
        parent = normalize(links_to)
        if not _slug_exists(conn, parent):
            raise SupersessionError(
                f"{where}: links_to {links_to!r} does not resolve to a corpus "
                f"record (normalized {parent!r})")
        return parent, False  # surfaced via parent; not its own record
    if explicit_in_corpus == "false":
        return None, False
    raise SupersessionError(
        f"{where}: affected_citation {affected_citation!r} (normalized {own!r}) "
        "is not in the corpus. Add `links_to:` for a parent record, or declare "
        "`in_corpus: false` explicitly. Unresolved linkage is not allowed.")


def _slug_exists(conn, slug: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sections WHERE citation_normalized = ? LIMIT 1", (slug,)
    ).fetchone() is not None


def _rel(path: pathlib.Path) -> str:
    """Display path for error messages; repo-relative when possible."""
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:
        return str(path)


def _load_provision_file(conn, path: pathlib.Path, memo: dict) -> dict:
    where = _rel(path)
    fm, body = _parse_frontmatter(path.read_text(encoding="utf-8"))

    for field in ("affected_citation", "kind_affected", "status"):
        if not fm.get(field):
            raise SupersessionError(f"{where}: missing required field {field!r}")
    if fm["kind_affected"] not in _VALID_KIND:
        raise SupersessionError(
            f"{where}: unhandled kind_affected {fm['kind_affected']!r} "
            f"(handled: {sorted(_VALID_KIND)}). Architecture extension required.")
    if fm["status"] not in _VALID_STATUS:
        raise SupersessionError(
            f"{where}: unknown status {fm['status']!r} (valid: {sorted(_VALID_STATUS)})")

    sections = _split_sections(body)
    summary = sections.get("summary", "").strip()
    if not summary:
        raise SupersessionError(f"{where}: missing required '## Summary' block")

    revised = sections.get("revised text") or None
    redline = sections.get("redline") or None

    # Section ANCs that revise/add/create must carry verbatim revised text;
    # FP-memo notices and removals do not.
    needs_text = memo["class"] == "section_anc" and fm["status"] in {"revised", "added", "new"}
    if needs_text and not revised:
        raise SupersessionError(
            f"{where}: status {fm['status']!r} requires a '## Revised text' block")

    if redline is not None:
        _validate_redline(redline, where)
        if revised is not None:
            _check_redline_matches_revised(redline, revised, where)

    normalized, in_corpus = _resolve_linkage(
        conn, fm["affected_citation"], fm.get("links_to"),
        fm.get("in_corpus"), where)

    return {
        "affected_citation": ascii_safe(fm["affected_citation"]),
        "affected_citation_normalized": normalized,
        "kind_affected": fm["kind_affected"],
        "status": fm["status"],
        "change_detail": ascii_safe(fm["change_detail"]) if fm.get("change_detail") else None,
        "source_memo": ascii_safe(memo["title"]),
        "memo_slug": memo["slug"],
        "memo_date": memo["memo_date"],
        "effective_date": memo["effective_date"],
        "legal_trigger": ascii_safe(memo.get("legal_trigger", "")) or None,
        "source_pdf_url": memo["source_pdf_url"],
        "in_corpus": 1 if in_corpus else 0,
        "revised_text_md": ascii_safe(revised) if revised else None,
        "redline_md": ascii_safe(redline) if redline else None,
        "summary": ascii_safe(summary),
    }


def _load_fp_memo(conn, memo_dir: pathlib.Path, memo: dict):
    """Yield notice-only records from a form-paragraph memo's form_paragraphs.tsv.

    FP memos (docs that revise/add/remove form paragraphs) carry NO verbatim FP
    text - the revised FP bodies live in PE2E-OC, not the memo. Each TSV row is
    tab-separated: fp_number<TAB>status<TAB>summary. in_corpus is computed from
    whether the FP exists in the appendix; absent FPs (e.g. new ones) are
    notice-only with affected_citation_normalized NULL (no error - expected)."""
    tsv = memo_dir / "form_paragraphs.tsv"
    if not tsv.exists():
        raise SupersessionError(f"fp_memo {memo['slug']!r}: missing {tsv}")
    where = _rel(tsv)
    for lineno, line in enumerate(tsv.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise SupersessionError(
                f"{where}:{lineno}: expected 3 tab-separated fields "
                f"(fp_number, status, summary), got {len(parts)}")
        fp_number, status, summary = (p.strip() for p in parts)
        if status not in _VALID_STATUS:
            raise SupersessionError(
                f"{where}:{lineno}: unknown status {status!r} "
                f"(valid: {sorted(_VALID_STATUS)})")
        if not summary:
            raise SupersessionError(f"{where}:{lineno}: empty summary")
        cit = f"Form Paragraph {fp_number}"
        norm = normalize(cit)
        in_corpus = _slug_exists(conn, norm)
        yield {
            "affected_citation": ascii_safe(cit),
            "affected_citation_normalized": norm if in_corpus else None,
            "kind_affected": "form_paragraph",
            "status": status,
            "change_detail": None,
            "source_memo": ascii_safe(memo["title"]),
            "memo_slug": memo["slug"],
            "memo_date": memo["memo_date"],
            "effective_date": memo["effective_date"],
            "legal_trigger": ascii_safe(memo.get("legal_trigger", "")) or None,
            "source_pdf_url": memo["source_pdf_url"],
            "in_corpus": 1 if in_corpus else 0,
            "revised_text_md": None,
            "redline_md": None,
            "summary": ascii_safe(summary),
        }


def validate_and_load(conn, supersessions_dir: pathlib.Path | None = None) -> dict:
    """Validate and load all supersessions. Fail loud on any inconsistency.

    Requires the `sections` table to be already populated so citation linkage
    can be resolved. Returns a summary dict for metadata/reporting.
    """
    base = supersessions_dir or SUPERSESSIONS_DIR
    manifest = load_manifest(base / "manifest.json")
    cap = int(manifest.get("pdf_bundle_cap_bytes", DEFAULT_PDF_BUNDLE_CAP_BYTES))

    # M vs S: manifest slugs must equal the on-disk source dirs exactly.
    manifest_slugs = {m["slug"] for m in manifest["memos"]}
    source_dirs = {
        p.name for p in base.iterdir()
        if p.is_dir() and p.name != "pdf"
    }
    if manifest_slugs != source_dirs:
        raise SupersessionError(
            "manifest/source-dir mismatch: "
            f"in manifest only={sorted(manifest_slugs - source_dirs)}, "
            f"on disk only={sorted(source_dirs - manifest_slugs)}")

    # Provenance PDF integrity: committed bytes must match manifest sha256/size.
    pdf_dir = base / "pdf"
    for m in manifest["memos"]:
        pdf_path = pdf_dir / f"{m['slug']}.pdf"
        if not pdf_path.exists():
            raise SupersessionError(f"provenance PDF missing: {pdf_path}")
        actual = sha256_file(pdf_path)
        if actual != m["pdf_sha256"]:
            raise SupersessionError(
                f"provenance PDF {pdf_path.name} sha256 mismatch "
                f"(manifest {m['pdf_sha256'][:12]}..., file {actual[:12]}...)")
        if pdf_path.stat().st_size != int(m["pdf_bytes"]):
            raise SupersessionError(
                f"provenance PDF {pdf_path.name} size mismatch "
                f"(manifest {m['pdf_bytes']}, file {pdf_path.stat().st_size})")

    bundled_map = allocate_pdf_budget(manifest["memos"], cap)

    loaded_by_memo: dict[str, int] = {}
    for m in manifest["memos"]:
        memo_dir = base / m["source_dir"]
        bundled = bundled_map[m["slug"]]
        local_path = f"{BUNDLE_REL}/{m['slug']}.pdf" if bundled else None
        # Section ANCs carry one .md per affected provision (with verbatim text);
        # FP memos are notice-only and carry a single form_paragraphs.tsv.
        if m["class"] == "fp_memo":
            rows = list(_load_fp_memo(conn, memo_dir, m))
        else:
            rows = [_load_provision_file(conn, p, m)
                    for p in sorted(memo_dir.glob("*.md"))]
        count = 0
        for row in rows:
            row["pdf_bundled"] = 1 if bundled else 0
            row["pdf_local_path"] = local_path
            conn.execute(
                """INSERT INTO supersessions
                   (affected_citation, affected_citation_normalized, kind_affected,
                    status, change_detail, source_memo, memo_slug, memo_date,
                    effective_date, legal_trigger, source_pdf_url, pdf_bundled,
                    pdf_local_path, in_corpus, revised_text_md, redline_md, summary)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row["affected_citation"], row["affected_citation_normalized"],
                 row["kind_affected"], row["status"], row["change_detail"],
                 row["source_memo"], row["memo_slug"], row["memo_date"],
                 row["effective_date"], row["legal_trigger"], row["source_pdf_url"],
                 row["pdf_bundled"], row["pdf_local_path"], row["in_corpus"],
                 row["revised_text_md"], row["redline_md"], row["summary"]),
            )
            count += 1
        # R vs M: record count must match the manifest's declaration exactly.
        if count != int(m["record_count"]):
            raise SupersessionError(
                f"memo {m['slug']!r}: loaded {count} record(s) but manifest "
                f"declares record_count={m['record_count']} (silent drop guard)")
        loaded_by_memo[m["slug"]] = count

    conn.execute("INSERT INTO supersessions_fts(supersessions_fts) VALUES('optimize')")

    return {
        "supersessions_through": manifest.get("supersessions_through"),
        "supersession_count": sum(loaded_by_memo.values()),
        "supersession_memos": sorted(loaded_by_memo),
        "pdf_bundled": sorted(s for s, b in bundled_map.items() if b),
        "pdf_link_only": sorted(s for s, b in bundled_map.items() if not b),
        "pdf_bundle_cap_bytes": cap,
    }


def bundle_pdfs(summary: dict, supersessions_dir: pathlib.Path | None = None) -> None:
    """Copy the bundled provenance PDFs into the skill for the local install.

    Overflow (link-only) memos are not copied. Idempotent: the bundle dir is
    rebuilt from the current allocation each call.
    """
    import shutil

    base = supersessions_dir or SUPERSESSIONS_DIR
    if BUNDLE_DIR.exists():
        shutil.rmtree(BUNDLE_DIR)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    for slug in summary["pdf_bundled"]:
        src = base / "pdf" / f"{slug}.pdf"
        shutil.copy2(src, BUNDLE_DIR / f"{slug}.pdf")
