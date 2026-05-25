"""Supersession loader + runtime tests.

Two layers:
1. Offline, fixture-based fail-loud tests for build/supersessions_loader.py.
   These need no network and no built DB - they assert that a malformed or
   drifted supersession source ALWAYS raises SupersessionError (silent partial
   loads are unacceptable during a build).
2. DB-gated end-to-end tests for lookup.py auto-surface and supersessions.py.
   These skip if skill/data/mpep.db has not been built.

Run from repo root: pytest tests/test_supersessions.py
"""

import hashlib
import json
import pathlib
import sqlite3
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "skill" / "data" / "mpep.db"
LOOKUP = REPO_ROOT / "skill" / "scripts" / "lookup.py"
SUPERS = REPO_ROOT / "skill" / "scripts" / "supersessions.py"
SEARCH = REPO_ROOT / "skill" / "scripts" / "search.py"

sys.path.insert(0, str(REPO_ROOT / "build"))
sys.path.insert(0, str(REPO_ROOT / "skill" / "scripts"))
import supersessions_loader as ssl  # noqa: E402
from _common import ascii_safe  # noqa: E402


# --- helpers ----------------------------------------------------------------

def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable] + args, cwd=str(REPO_ROOT),
                          capture_output=True, text=True)


_GOOD_PROVISION = """---
affected_citation: "MPEP 9001"
kind_affected: "mpep_section"
status: "revised"
---

## Summary

A test supersession summary.

## Revised text

Some revised text for MPEP 9001.
"""


def _make_env(tmp_path: pathlib.Path, provision_files: dict[str, str], *,
              section_slugs=("mpep_9001",), record_count=None,
              memo_overrides=None, pdf_bytes=b"%PDF-1.4\ntest pdf\n",
              manifest_sha256=None, manifest_bytes=None):
    """Build a temp supersession source tree + a DB with a sections table.

    Returns (conn, supersessions_dir). The single memo has slug 'test'.
    """
    base = tmp_path / "supersessions"
    (base / "pdf").mkdir(parents=True)
    (base / "test").mkdir(parents=True)
    (base / "pdf" / "test.pdf").write_bytes(pdf_bytes)
    for name, text in provision_files.items():
        (base / "test" / name).write_text(text, encoding="utf-8")

    memo = {
        "slug": "test",
        "title": "Test memo",
        "memo_date": "2025-10-24",
        "effective_date": "2025-10-24",
        "legal_trigger": "Test trigger",
        "class": "section_anc",
        "source_pdf_url": "https://example.invalid/test.pdf",
        "pdf_sha256": manifest_sha256 or hashlib.sha256(pdf_bytes).hexdigest(),
        "pdf_bytes": manifest_bytes if manifest_bytes is not None else len(pdf_bytes),
        "source_dir": "test",
        "record_count": record_count if record_count is not None else len(provision_files),
    }
    if memo_overrides:
        memo.update(memo_overrides)
    manifest = {"supersessions_through": "2025-10-24", "memos": [memo]}
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    conn.execute("CREATE TABLE sections (citation_normalized TEXT)")
    conn.executemany("INSERT INTO sections VALUES (?)", [(s,) for s in section_slugs])
    ssl.create_schema(conn)
    return conn, base


# --- pure-function fail-loud tests ------------------------------------------

def test_redline_balanced_ok():
    ssl._validate_redline("a [[ins:b]] c [[del:d]] e", "x")  # no raise


@pytest.mark.parametrize("bad", [
    "a [[ins:b c",        # unclosed ins
    "a b]] c",            # stray close
    "[[del:x]] [[ins:y",  # second unclosed
])
def test_redline_unbalanced_raises(bad):
    with pytest.raises(ssl.SupersessionError):
        ssl._validate_redline(bad, "x")


def test_budget_smallest_first_under_cap():
    memos = [
        {"slug": "big", "pdf_bytes": 2_000_000},
        {"slug": "small", "pdf_bytes": 500_000},
        {"slug": "mid", "pdf_bytes": 800_000},
    ]
    # cap fits small (0.5M) + mid (0.8M) = 1.3M, but not big (would be 3.3M).
    alloc = ssl.allocate_pdf_budget(memos, cap_bytes=1_500_000)
    assert alloc == {"small": True, "mid": True, "big": False}


def test_budget_all_fit():
    memos = [{"slug": "a", "pdf_bytes": 10}, {"slug": "b", "pdf_bytes": 20}]
    assert ssl.allocate_pdf_budget(memos, cap_bytes=3 * 1024 * 1024) == {"a": True, "b": True}


def test_manifest_missing_field_raises(tmp_path):
    bad = {"memos": [{"slug": "x"}]}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ssl.SupersessionError):
        ssl.load_manifest(p)


def test_manifest_bad_class_raises(tmp_path):
    memo = {"slug": "x", "title": "t", "memo_date": "d", "effective_date": "d",
            "class": "bogus", "source_pdf_url": "u", "pdf_sha256": "h",
            "pdf_bytes": 1, "source_dir": "x", "record_count": 0}
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"memos": [memo]}))
    with pytest.raises(ssl.SupersessionError):
        ssl.load_manifest(p)


# --- live reconciliation (L vs M) -------------------------------------------

def _manifest_one(url="https://u/a.pdf", sha="abc"):
    return {"memos": [{"slug": "a", "source_pdf_url": url, "pdf_sha256": sha}]}


def test_reconcile_live_ok():
    m = _manifest_one()
    ssl.reconcile_live(m, lambda: ["https://u/a.pdf"], lambda url: "abc")  # no raise


def test_reconcile_live_new_doc_on_page_raises():
    m = _manifest_one()
    with pytest.raises(ssl.SupersessionError, match="not yet incorporated"):
        ssl.reconcile_live(m, lambda: ["https://u/a.pdf", "https://u/NEW.pdf"],
                           lambda url: "abc")


def test_reconcile_live_doc_gone_from_page_raises():
    m = _manifest_one()
    with pytest.raises(ssl.SupersessionError, match="no longer on the USPTO"):
        ssl.reconcile_live(m, lambda: [], lambda url: "abc")


def test_reconcile_live_hash_changed_raises():
    m = _manifest_one(sha="abc")
    with pytest.raises(ssl.SupersessionError, match="changed since transcription"):
        ssl.reconcile_live(m, lambda: ["https://u/a.pdf"], lambda url: "DIFFERENT")


# --- strict offline loader (M vs S vs R) ------------------------------------

def test_loader_happy_path(tmp_path):
    conn, base = _make_env(tmp_path, {"01.md": _GOOD_PROVISION})
    summary = ssl.validate_and_load(conn, base)
    assert summary["supersession_count"] == 1
    row = conn.execute(
        "SELECT affected_citation, status, in_corpus, revised_text_md "
        "FROM supersessions").fetchone()
    assert row[0] == "MPEP 9001" and row[1] == "revised" and row[2] == 1
    assert "revised text for MPEP 9001" in row[3]


def test_loader_unknown_status_raises(tmp_path):
    bad = _GOOD_PROVISION.replace('status: "revised"', 'status: "bogus"')
    conn, base = _make_env(tmp_path, {"01.md": bad})
    with pytest.raises(ssl.SupersessionError, match="unknown status"):
        ssl.validate_and_load(conn, base)


def test_loader_unhandled_kind_raises(tmp_path):
    bad = _GOOD_PROVISION.replace('kind_affected: "mpep_section"', 'kind_affected: "figure"')
    conn, base = _make_env(tmp_path, {"01.md": bad})
    with pytest.raises(ssl.SupersessionError, match="unhandled kind"):
        ssl.validate_and_load(conn, base)


def test_loader_unresolved_linkage_raises(tmp_path):
    # affected_citation normalizes to a slug not in sections, and no links_to /
    # in_corpus:false declared -> must be a loud error, never a silent NULL.
    bad = _GOOD_PROVISION.replace('"MPEP 9001"', '"MPEP 9999"')
    conn, base = _make_env(tmp_path, {"01.md": bad}, section_slugs=("mpep_9001",))
    with pytest.raises(ssl.SupersessionError, match="not in the corpus"):
        ssl.validate_and_load(conn, base)


def test_loader_links_to_parent_resolves(tmp_path):
    # A subsection with no own record links to its parent (which exists). The
    # join key becomes the parent slug, but in_corpus is False because the
    # subsection itself is not an independently retrievable record.
    prov = (_GOOD_PROVISION
            .replace('"MPEP 9001"', '"MPEP 9001(a)(1)"')
            .replace('status: "revised"',
                     'status: "revised"\nlinks_to: "MPEP 9001"'))
    conn, base = _make_env(tmp_path, {"01.md": prov}, section_slugs=("mpep_9001",))
    ssl.validate_and_load(conn, base)
    row = conn.execute(
        "SELECT affected_citation, affected_citation_normalized, in_corpus "
        "FROM supersessions").fetchone()
    assert row[0] == "MPEP 9001(a)(1)" and row[1] == "mpep_9001" and row[2] == 0


_REDLINE_PROVISION = """---
affected_citation: "MPEP 9001"
kind_affected: "mpep_section"
status: "revised"
---

## Summary

A test with redline.

## Revised text

The claim improves technology or a technical field.

## Redline

The claim improves technology[[ins: or a technical field]].
"""


def test_loader_redline_accept_all_matches(tmp_path):
    conn, base = _make_env(tmp_path, {"01.md": _REDLINE_PROVISION})
    ssl.validate_and_load(conn, base)  # accept-all == revised, no raise
    row = conn.execute("SELECT redline_md FROM supersessions").fetchone()
    assert "[[ins:" in row[0]


def test_loader_redline_accept_all_mismatch_raises(tmp_path):
    # Redline accept-all yields "...technology or a different field", which does
    # NOT match the revised text -> transcription drift must fail loud.
    bad = _REDLINE_PROVISION.replace("[[ins: or a technical field]]",
                                     "[[ins: or a different field]]")
    conn, base = _make_env(tmp_path, {"01.md": bad})
    with pytest.raises(ssl.SupersessionError, match="accept-all does not match"):
        ssl.validate_and_load(conn, base)


def test_loader_in_corpus_false_allowed(tmp_path):
    prov = (_GOOD_PROVISION
            .replace('"MPEP 9001"', '"Form Paragraph 6.49.11"')
            .replace('kind_affected: "mpep_section"', 'kind_affected: "form_paragraph"')
            .replace('status: "revised"',
                     'status: "new"\nin_corpus: false'))
    conn, base = _make_env(tmp_path, {"01.md": prov}, section_slugs=())
    ssl.validate_and_load(conn, base)
    row = conn.execute(
        "SELECT affected_citation_normalized, in_corpus FROM supersessions").fetchone()
    assert row[0] is None and row[1] == 0


def test_loader_count_mismatch_raises(tmp_path):
    conn, base = _make_env(tmp_path, {"01.md": _GOOD_PROVISION}, record_count=5)
    with pytest.raises(ssl.SupersessionError, match="silent drop guard"):
        ssl.validate_and_load(conn, base)


def test_loader_manifest_source_mismatch_raises(tmp_path):
    # Manifest declares slug 'test'/source_dir 'test' but the on-disk dir is renamed.
    conn, base = _make_env(tmp_path, {"01.md": _GOOD_PROVISION})
    (base / "test").rename(base / "renamed")
    with pytest.raises(ssl.SupersessionError, match="manifest/source-dir mismatch"):
        ssl.validate_and_load(conn, base)


def test_loader_pdf_hash_mismatch_raises(tmp_path):
    conn, base = _make_env(tmp_path, {"01.md": _GOOD_PROVISION},
                           manifest_sha256="0" * 64)
    with pytest.raises(ssl.SupersessionError, match="sha256 mismatch"):
        ssl.validate_and_load(conn, base)


def test_loader_missing_revised_text_raises(tmp_path):
    # A section ANC revision must carry verbatim revised text.
    bad = _GOOD_PROVISION.split("## Revised text")[0]
    conn, base = _make_env(tmp_path, {"01.md": bad})
    with pytest.raises(ssl.SupersessionError, match="Revised text"):
        ssl.validate_and_load(conn, base)


def test_loader_bad_redline_raises(tmp_path):
    bad = _GOOD_PROVISION + "\n## Redline\n\nSome [[ins:unclosed redline.\n"
    conn, base = _make_env(tmp_path, {"01.md": bad})
    with pytest.raises(ssl.SupersessionError, match="redline"):
        ssl.validate_and_load(conn, base)


# --- Redline vs PDF strike/underline marks (catches missed deletions) --------

def test_redline_marks_match_pdf():
    """Every word struck in the source PDF must be marked [[del:]] in the
    transcription - otherwise a deleted word silently leaks into the revised
    text (the bug that left 'is,' in MPEP 2106.04(d)(1)). Skips without
    pdfplumber or the provenance PDF."""
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        pytest.skip("pdfplumber not installed (requirements-dev.txt)")
    import verify_redlines
    base = REPO_ROOT / "build" / "supersessions"
    if not (base / "pdf" / "desjardins.pdf").exists():
        pytest.skip("provenance PDF absent")
    manifest = verify_redlines.load_manifest(base / "manifest.json")
    memo = next(m for m in manifest["memos"] if m["slug"] == "desjardins")
    failures = verify_redlines.verify_memo(memo, base)
    assert not failures, f"redline discrepancies vs PDF marks: {failures}"


# --- DB-gated end-to-end (Desjardins slice) ---------------------------------

@pytest.fixture(scope="module", autouse=True)
def _db_exists():
    if not DB_PATH.exists():
        pytest.skip(f"DB not built at {DB_PATH}; run build/03_build_database.py")


def _supersessions_table_present() -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("SELECT 1 FROM supersessions LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def test_lookup_surfaces_supersession_on_affected_section():
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    r = _run([str(LOOKUP), "MPEP 2106.05(a)"])
    assert r.returncode == 0, r.stderr
    assert "SUPERSEDED IN PART" in r.stdout
    assert "Ex Parte Desjardins" in r.stdout
    assert "2025-12-05" in r.stdout


def test_lookup_body_unchanged_by_supersession():
    """The published body_md must be returned verbatim; the supersession is
    additive (a separate block / JSON key), never a mutation of body_md."""
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    conn = sqlite3.connect(str(DB_PATH))
    db_body = conn.execute(
        "SELECT body_md FROM sections WHERE citation_normalized = ?",
        ("mpep_2106_05_a",)).fetchone()[0]
    conn.close()
    r = _run([str(LOOKUP), "MPEP 2106.05(a)", "--json"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["body_md"] == db_body
    assert data["supersessions"], "expected a supersessions list in JSON output"


def test_lookup_parent_surfaces_subsection_addition():
    """MPEP 2106.04(d) surfaces the subsection III addition (no standalone
    record of its own; links to the parent)."""
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    r = _run([str(LOOKUP), "MPEP 2106.04(d)", "--json"])
    assert r.returncode == 0, r.stderr
    cites = [s["affected_citation"] for s in json.loads(r.stdout)["supersessions"]]
    assert any("subsection III" in c for c in cites)


def test_lookup_subsubsection_now_retrievable():
    """After the sub-subsection parser fix, MPEP 2106.04(d)(1) is its own record
    and its revision surfaces on a direct lookup (in_corpus)."""
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    r = _run([str(LOOKUP), "MPEP 2106.04(d)(1)", "--json"])
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["citation"] == "MPEP 2106.04(d)(1)", "the (d)(1) record must exist now"
    assert data["supersessions"], "the (d)(1) revision should surface directly"
    assert any("2106.04(d)(1)" in s["affected_citation"] for s in data["supersessions"])


def test_supersessions_list():
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    r = _run([str(SUPERS), "--json", "--list"])
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["count"] >= 5
    slugs = {s["memo_slug"] for s in data["supersessions"]}
    assert "desjardins" in slugs


def test_search_tags_superseded_results():
    """search.py flags results whose provision has a supersession (the fee memo
    revised many 'Action Is Final' form paragraphs)."""
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    r = _run([str(SEARCH), "--json", "--kind", "form_paragraph", "Action Is Final", "--limit", "5"])
    assert r.returncode == 0, r.stderr
    hits = json.loads(r.stdout)["hits"]
    assert any(h.get("superseded") for h in hits), "expected a superseded FP to be tagged"


def test_supersessions_memo_filter():
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    r = _run([str(SUPERS), "--json", "--memo", "desjardins"])
    assert r.returncode == 0
    assert json.loads(r.stdout)["count"] == 5


def test_supersessions_redline_present():
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    r = _run([str(SUPERS), "MPEP 2106.05(f)"])
    assert r.returncode == 0, r.stderr
    assert "[[ins:" in r.stdout and "[[del:" in r.stdout


def test_supersessions_fts_search():
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    r = _run([str(SUPERS), "--json", "--search", "catastrophic forgetting"])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["count"] >= 1


def test_supersession_revised_text_is_verbatim_from_source():
    """The DB revised_text_md must equal ascii_safe of the source transcription
    (no drift between the committed source file and the loaded record)."""
    if not _supersessions_table_present():
        pytest.skip("supersessions table absent; rebuild DB")
    src = (REPO_ROOT / "build" / "supersessions" / "desjardins"
           / "05_2106_05_f.md").read_text(encoding="utf-8")
    revised = src.split("## Revised text", 1)[1].split("## Redline", 1)[0].strip()
    conn = sqlite3.connect(str(DB_PATH))
    db_val = conn.execute(
        "SELECT revised_text_md FROM supersessions "
        "WHERE affected_citation = 'MPEP 2106.05(f)'").fetchone()[0]
    conn.close()
    assert db_val == ascii_safe(revised)
