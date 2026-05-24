"""End-to-end smoke test.

Friday-night-2am test: mpep.db exists, lookup.py returns the expected
record verbatim, fail-loud paths actually fail loud. If this test ever
breaks, the skill is broken end-to-end.

Run from repo root: pytest tests/test_e2e.py
"""

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "skill" / "data" / "mpep.db"
LOOKUP = REPO_ROOT / "skill" / "scripts" / "lookup.py"
SEARCH = REPO_ROOT / "skill" / "scripts" / "search.py"
INDEX_LOOKUP = REPO_ROOT / "skill" / "scripts" / "index_lookup.py"


def _run(args: list[str], cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable] + args,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module", autouse=True)
def db_must_exist() -> None:
    """Skip e2e tests if the DB hasn't been built yet."""
    if not DB_PATH.exists():
        pytest.skip(f"DB not built at {DB_PATH}; run build/03_build_database.py")


# --- Lookup smoke tests -----------------------------------------------------


def test_lookup_mpep_2141_returns_obviousness() -> None:
    """The canonical Friday-night-2am test."""
    r = _run([str(LOOKUP), "MPEP 2141"])
    assert r.returncode == 0, f"non-zero exit: stderr={r.stderr}"
    assert "obviousness" in r.stdout.lower()
    assert "MPEP 2141" in r.stdout
    assert "Examination Guidelines" in r.stdout
    assert r.stdout.startswith("Citation:")


def test_lookup_mpep_2141_01_a_subsection() -> None:
    r = _run([str(LOOKUP), "MPEP 2141.01(a)"])
    assert r.returncode == 0
    assert "Analogous and Nonanalogous Art" in r.stdout
    assert "Parent: MPEP 2141.01" in r.stdout


def test_lookup_35_usc_102_aia_default() -> None:
    r = _run([str(LOOKUP), "35 USC 102"])
    assert r.returncode == 0
    assert "Citation: 35 USC 102\n" in r.stdout, "should be AIA, not pre-AIA"
    assert "novelty" in r.stdout.lower()


def test_lookup_35_usc_102_pre_aia_explicit() -> None:
    r = _run([str(LOOKUP), "35 USC 102 (pre-AIA)"])
    assert r.returncode == 0
    assert "Citation: 35 USC 102 (pre-AIA)" in r.stdout


def test_lookup_37_cfr_1_131() -> None:
    r = _run([str(LOOKUP), "37 CFR 1.131"])
    assert r.returncode == 0
    assert "Citation: 37 CFR 1.131" in r.stdout
    assert "affidavit" in r.stdout.lower() or "declaration" in r.stdout.lower()


def test_lookup_form_paragraph() -> None:
    r = _run([str(LOOKUP), "Form Paragraph 7.05"])
    assert r.returncode == 0
    assert "Citation: Form Paragraph 7.05" in r.stdout


# --- Subject Matter Index routing -------------------------------------------


def _index_entry_count() -> int:
    import sqlite3
    if not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sections WHERE kind = 'index_entry'"
        ).fetchone()[0]
    finally:
        conn.close()


def test_index_lookup_routes_to_verbatim_sections() -> None:
    """index_lookup.py matches an index term and resolves its refs verbatim."""
    import json as _json
    import re
    import sqlite3

    if _index_entry_count() == 0:
        pytest.skip("no index_entry records; rebuild with index pages to enable")

    # Derive a query word from a real index entry that references a section.
    conn = sqlite3.connect(str(DB_PATH))
    try:
        title, _ = conn.execute(
            "SELECT title, body_md FROM sections "
            "WHERE kind = 'index_entry' AND body_md LIKE '%Referenced sections%' "
            "ORDER BY LENGTH(title) LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    words = re.findall(r"[A-Za-z]{4,}", title)
    term = words[-1] if words else "patent"

    r = _run([str(INDEX_LOOKUP), "--json", term])
    assert r.returncode == 0, f"non-zero exit: stderr={r.stderr}"
    data = _json.loads(r.stdout)
    assert data["entries"], f"no index entry matched {term!r}"
    resolved = [s for s in data["sections"] if s.get("hit")]
    assert resolved, "index references should resolve to section records"
    assert any(s.get("body_md") for s in resolved), "resolved sections must be verbatim"


def test_search_excludes_index_entries_by_default() -> None:
    """search.py hides index entries by default; --kind index_entry surfaces them."""
    import json as _json

    if _index_entry_count() == 0:
        pytest.skip("no index_entry records; rebuild with index pages to enable")

    r = _run([str(SEARCH), "--json", "patent"])
    assert r.returncode == 0
    default_hits = _json.loads(r.stdout)["hits"]
    assert all(h["kind"] != "index_entry" for h in default_hits), \
        "index entries leaked into default search results"

    r2 = _run([str(SEARCH), "--json", "--kind", "index_entry", "patent"])
    assert r2.returncode == 0
    index_hits = _json.loads(r2.stdout)["hits"]
    assert all(h["kind"] == "index_entry" for h in index_hits)


# --- SKILL.md documented examples (keep docs honest) -----------------------
# These pin the concrete examples shown in skill/SKILL.md so the documentation
# cannot silently drift from actual behavior.


def test_skill_lookup_accepted_format_variants() -> None:
    """The citation forms listed under 'Accepts' in SKILL.md all resolve."""
    cases = [
        ("35 U.S.C. 102", "Citation: 35 USC 102"),
        ("37 C.F.R. 1.131", "Citation: 37 CFR 1.131"),
        ("FP 7.05", "Citation: Form Paragraph 7.05"),
    ]
    for citation, expected in cases:
        r = _run([str(LOOKUP), citation])
        assert r.returncode == 0, f"{citation!r} failed: {r.stderr}"
        assert expected in r.stdout, f"{citation!r} did not yield {expected!r}"


def test_skill_lookup_2141_documented_sample() -> None:
    """The MPEP 2141 sample output block in SKILL.md matches reality."""
    r = _run([str(LOOKUP), "MPEP 2141"])
    assert r.returncode == 0
    assert "Citation: MPEP 2141" in r.stdout
    assert ("Title: Examination Guidelines for Determining Obviousness "
            "Under 35 U.S.C. 103") in r.stdout
    assert "Kind: mpep_section" in r.stdout
    assert "Chapter: 2100" in r.stdout
    assert "s2141.html" in r.stdout
    # Revision field is present and in the documented "R-<rev>" form (exact
    # value tracks the corpus revision, so match the format, not the number).
    assert any(ln.startswith("Revision: R-") for ln in r.stdout.splitlines())


def test_skill_search_kind_filter_examples() -> None:
    """The four `search.py ... --kind` examples in SKILL.md hold."""
    import json as _json

    def cits(query, kind):
        r = _run([str(SEARCH), "--json", "--kind", kind, query])
        assert r.returncode == 0, f"{query!r}/{kind} failed: {r.stderr}"
        return _json.loads(r.stdout)["hits"]

    novelty = [h["citation"] for h in cits("novelty", "statute")]
    assert "35 USC 102" in novelty

    affidavit = [h["citation"] for h in cits("affidavit", "cfr_rule")]
    assert "37 CFR 1.132" in affidavit

    obv = cits("obviousness", "mpep_section")
    assert obv and all(h["kind"] == "mpep_section" for h in obv)

    rej = cits("rejection", "form_paragraph")
    assert rej and all(h["kind"] == "form_paragraph" for h in rej)


def test_skill_default_search_authority_outranks_form_paragraphs() -> None:
    """SKILL.md: in the default fused ranking, Form Paragraphs rank last.

    For 'novelty', 35 USC 102 must appear before any Form Paragraph hit.
    """
    import json as _json
    r = _run([str(SEARCH), "--json", "novelty"])
    assert r.returncode == 0
    hits = _json.loads(r.stdout)["hits"]
    cits = [h["citation"] for h in hits]
    assert "35 USC 102" in cits
    i102 = cits.index("35 USC 102")
    fp_positions = [i for i, h in enumerate(hits) if h["kind"] == "form_paragraph"]
    assert all(i102 < p for p in fp_positions), \
        "statute should outrank Form Paragraphs in the fused default ranking"


def test_skill_default_search_is_index_aware() -> None:
    """SKILL.md: default search boosts index-corroborated sections to the top."""
    import json as _json
    if _index_entry_count() == 0:
        pytest.skip("no index_entry records; rebuild with index pages to enable")
    r = _run([str(SEARCH), "--json", "obviousness"])
    assert r.returncode == 0
    hits = _json.loads(r.stdout)["hits"]
    assert hits, "expected results for 'obviousness'"
    assert hits[0]["kind"] == "mpep_section"
    assert hits[0]["match"] == "index+keyword"


def test_skill_index_lookup_examples() -> None:
    """The two index_lookup.py examples in SKILL.md route to real sections."""
    import json as _json
    if _index_entry_count() == 0:
        pytest.skip("no index_entry records; rebuild with index pages to enable")

    r = _run([str(INDEX_LOOKUP), "--json", "double patenting"])
    assert r.returncode == 0, f"stderr={r.stderr}"
    sections = _json.loads(r.stdout)["sections"]
    assert any(s.get("citation") == "MPEP 804" for s in sections), \
        "'double patenting' should route to MPEP 804"

    r2 = _run([str(INDEX_LOOKUP), "--json", "--entries", "3", "--sections", "8",
               "incomplete reply"])
    assert r2.returncode == 0
    resolved = [s for s in _json.loads(r2.stdout)["sections"] if s.get("hit")]
    assert resolved, "'incomplete reply' should resolve to at least one section"


# --- Inbound-reference authority bump ---------------------------------------


def test_authority_bump_surfaces_well_cited_rule() -> None:
    """37 CFR 1.131 (cited ~110x) climbs the 'affidavit' cfr_rule results via
    the inbound-reference bump; it sat at #16 before (below 1-reference rules)
    because its long body dilutes BM25 term density."""
    import json as _json
    r = _run([str(SEARCH), "--json", "--kind", "cfr_rule", "--limit", "10", "affidavit"])
    assert r.returncode == 0
    hits = _json.loads(r.stdout)["hits"]
    cits = [h["citation"] for h in hits]
    assert "37 CFR 1.131" in cits, "authority bump should surface 1.131 for 'affidavit'"
    # The result carries the signal that lifted it.
    rule = next(h for h in hits if h["citation"] == "37 CFR 1.131")
    assert rule.get("inbound_refs", 0) > 0
    # It now outranks rules that only mention affidavits in passing.
    if "37 CFR 11.27" in cits:
        assert cits.index("37 CFR 1.131") < cits.index("37 CFR 11.27")


def test_authority_bump_keeps_canonical_section_on_top() -> None:
    """The bump is modest: MPEP 2141 still leads 'obviousness' (it is not
    displaced by higher-reference but less on-point sections)."""
    import json as _json
    if _index_entry_count() == 0:
        pytest.skip("no index_entry records; rebuild with index pages to enable")
    r = _run([str(SEARCH), "--json", "--limit", "1", "obviousness"])
    assert r.returncode == 0
    hits = _json.loads(r.stdout)["hits"]
    assert hits and hits[0]["citation"] == "MPEP 2141"


# --- Citation format tolerance ---------------------------------------------


def test_lookup_handles_format_variants() -> None:
    """Various citation forms should resolve to the same record."""
    variants = [
        "MPEP 2141.01(a)",
        "MPEP 2141.01 (a)",
        "M.P.E.P. 2141.01(a)",
        "MPEP s. 2141.01(a)",
        "MPEP § 2141.01(a)",
    ]
    titles = []
    for v in variants:
        r = _run([str(LOOKUP), v])
        assert r.returncode == 0, f"failed for variant {v!r}: {r.stderr}"
        for line in r.stdout.splitlines():
            if line.startswith("Title:"):
                titles.append(line)
                break
    # All variants should yield the same Title field
    assert len(set(titles)) == 1, f"variants resolved to different records: {titles}"


# --- Fail-loud paths --------------------------------------------------------


def test_lookup_bad_citation_format_exits_1() -> None:
    r = _run([str(LOOKUP), "this is not a citation"])
    assert r.returncode == 1
    assert "Bad citation" in r.stderr or "Unrecognized" in r.stderr


def test_lookup_not_found_exits_2_with_suggestions() -> None:
    r = _run([str(LOOKUP), "MPEP 9999"])
    assert r.returncode == 2
    assert "Not found" in r.stderr or "MPEP" in r.stderr


def test_search_malformed_fts5_exits_1() -> None:
    r = _run([str(SEARCH), 'unclosed"'])
    assert r.returncode == 1
    assert "FTS5" in r.stderr or "syntax" in r.stderr.lower()


def test_lookup_missing_db_exits_3(tmp_path: pathlib.Path) -> None:
    """Move DB away, run lookup, expect exit 3 + clear stderr."""
    backup = DB_PATH.with_suffix(".db.test_backup")
    shutil.move(str(DB_PATH), str(backup))
    try:
        r = _run([str(LOOKUP), "MPEP 2141"])
        assert r.returncode == 3
        assert "missing" in r.stderr.lower() or "unreadable" in r.stderr.lower()
    finally:
        shutil.move(str(backup), str(DB_PATH))


# --- Search smoke tests -----------------------------------------------------


def test_search_analogous_art_returns_2141_01_a() -> None:
    """Top hit for 'analogous art' should be MPEP 2141.01(a)."""
    r = _run([str(SEARCH), "analogous art", "--limit", "3"])
    assert r.returncode == 0
    # MPEP 2141.01(a) should be in the first 3 results
    assert "MPEP 2141.01(a)" in r.stdout


def test_search_kind_filter_statute_novelty() -> None:
    """With --kind statute, '35 USC 102' should be top hit for 'novelty'."""
    r = _run([str(SEARCH), "novelty", "--kind", "statute", "--limit", "1"])
    assert r.returncode == 0
    assert "35 USC 102" in r.stdout


def test_search_zero_results_fail_loud() -> None:
    """Zero-result query must surface the empty contract, not silently return."""
    r = _run([str(SEARCH), "thisstringneverappears", "--json"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["hits"] == []
    assert data["fts_query_parsed_ok"] is True
    assert data["total_records_searched"] > 0


def test_search_limit_hard_cap() -> None:
    r = _run([str(SEARCH), "patent", "--limit", "200"])
    assert r.returncode == 1


# --- Verbatim discipline ----------------------------------------------------


def test_lookup_body_is_in_database_verbatim() -> None:
    """Whatever lookup.py returns must be the exact body_md from the DB."""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    db_body = conn.execute(
        "SELECT body_md FROM sections WHERE citation_normalized = ?",
        ("mpep_2141",),
    ).fetchone()[0]
    conn.close()

    r = _run([str(LOOKUP), "MPEP 2141", "--json"])
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["body_md"] == db_body, "lookup.py output diverged from DB body_md"
