"""Build-phase parser tests.

Tests the dedup logic and the per-kind heading detection. Does NOT run
parsers against real HTML (those are integration tests covered by the
build pipeline + e2e suite).

Run from repo root: pytest tests/test_parsers.py
"""

import pathlib
import sys


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skill" / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "build"))


def test_dedup_keeps_longer_body() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "parser", pathlib.Path(__file__).resolve().parent.parent / "build" / "02_parse_to_markdown.py"
    )
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)

    records = [
        {"citation": "MPEP 100", "citation_normalized": "mpep_100",
         "body_md": "short body"},
        {"citation": "MPEP 100", "citation_normalized": "mpep_100",
         "body_md": "much longer body with substantially more text " * 10},
        {"citation": "MPEP 200", "citation_normalized": "mpep_200",
         "body_md": "only one record"},
    ]
    kept, dropped = parser._dedup_records(records)
    assert len(kept) == 2
    assert len(dropped) == 1
    # The longer-body MPEP 100 should be kept
    kept_mpep_100 = [r for r in kept if r["citation_normalized"] == "mpep_100"][0]
    assert "longer body" in kept_mpep_100["body_md"]


def test_dedup_no_duplicates_returns_all() -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "parser", pathlib.Path(__file__).resolve().parent.parent / "build" / "02_parse_to_markdown.py"
    )
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)

    records = [
        {"citation": "MPEP 100", "citation_normalized": "mpep_100", "body_md": "a"},
        {"citation": "MPEP 200", "citation_normalized": "mpep_200", "body_md": "b"},
        {"citation": "MPEP 300", "citation_normalized": "mpep_300", "body_md": "c"},
    ]
    kept, dropped = parser._dedup_records(records)
    assert len(kept) == 3
    assert len(dropped) == 0


def test_ascii_safe_drops_non_ascii() -> None:
    from _common import ascii_safe
    # Section symbol -> "Section "
    assert ascii_safe("MPEP § 2141") == "MPEP Section  2141"
    # Smart quotes
    assert ascii_safe("“hello”") == '"hello"'
    # Em-dash and en-dash
    assert ascii_safe("a—b–c") == "a--b-c"
    # Non-breaking hyphen
    assert ascii_safe("pre‑AIA") == "pre-AIA"
    # Defensive: unknown non-ASCII char is stripped
    assert ascii_safe("xÿy") == "xy"


def test_collapse_inline_handles_html_indentation() -> None:
    from _common import collapse_inline
    raw = "\n                                 For applications\n                                 subject to..."
    cleaned = collapse_inline(raw)
    assert cleaned == " For applications subject to..."


def test_extract_changelog_section() -> None:
    import extract_changelog
    text = (pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    body = extract_changelog.section_body("1.0.0", text)
    assert body and "Initial release" in body
    assert extract_changelog.section_body("9.9.9", text) is None  # absent version


def test_release_version_has_changelog_section() -> None:
    """The skill version named in SKILL.md must have release notes to publish."""
    import extract_changelog
    import print_meta
    v = print_meta.skill_version()
    assert v is not None, "SKILL.md has no version: field"
    text = (pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert extract_changelog.section_body(v, text) is not None, \
        f"CHANGELOG.md has no section for the current skill version v{v}"


def test_skill_version_consistent_across_files() -> None:
    """SKILL.md frontmatter version, the printed footer, and the top CHANGELOG
    entry must all name the same version (they are updated together)."""
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    skill_md = (root / "skill" / "SKILL.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    fm = re.search(r"^version:\s*(\d+\.\d+\.\d+)\s*$", skill_md, re.MULTILINE)
    footer = re.search(r"MPEP Lookup v(\d+\.\d+\.\d+)", skill_md)
    top = re.search(r"^##\s*v(\d+\.\d+\.\d+)\s*$", changelog, re.MULTILINE)

    assert fm, "SKILL.md frontmatter is missing a `version:` field"
    assert footer, "SKILL.md is missing the printed `MPEP Lookup vX.Y.Z` footer"
    assert top, "CHANGELOG.md is missing a top `## vX.Y.Z` entry"
    assert fm.group(1) == footer.group(1) == top.group(1), (
        f"version mismatch: frontmatter={fm.group(1)} footer={footer.group(1)} "
        f"changelog={top.group(1)}")


def test_collapse_whitespace_preserves_paragraphs() -> None:
    from _common import collapse_whitespace
    raw = "para 1\n\n\n\npara 2\n\n  \n  \npara 3   "
    cleaned = collapse_whitespace(raw)
    assert "para 1\n\npara 2\n\npara 3" in cleaned


def _load_parser():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "parser", pathlib.Path(__file__).resolve().parent.parent / "build" / "02_parse_to_markdown.py"
    )
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    return parser


# A small fixture mirroring the real Subject Matter Index markup: nested
# <ul>/<li>, a "See also" cross-reference (link to another mpep-index anchor),
# section refs (links to sNNN.html), and three levels of nesting.
_INDEX_HTML = """
<html><body>
<ul>
  <li id="mpep-index--abandoned_application--2011-05-27">Abandoned application
      (<i>See also </i><a href="mpep-index-a.html#mpep-index--abandonment--2011-05-27">Abandonment</a>)
    <ul>
      <li id="mpep-index--abandoned_application.definition--2011-05-27">Definition&mdash;
          <b><a href="s203.html#d0e1">203.05</a></b></li>
      <li id="mpep-index--abandoned_application.revival-2013-11">Revival&mdash;
          <b><a href="s711.html#d0e2">711.03(c)</a></b>;
          <b><a href="s1893.html#d0e3">1893.02</a></b></li>
    </ul>
  </li>
  <li id="mpep-index--application_publication--2011-05-27">Application publication&mdash;
      <b><a href="s901.html#d0e4">901.03</a></b></li>
</ul>
</body></html>
"""


def test_parse_index_hierarchy_refs_and_see_also() -> None:
    parser = _load_parser()
    recs = list(parser.parse_index_file(_INDEX_HTML, "https://example/mpep-index-a.html"))

    by_title = {r["title"]: r for r in recs}
    # Top-level term + two sub-entries + a separate top-level term = 4 records.
    assert len(recs) == 4
    assert set(by_title) == {
        "Abandoned application",
        "Abandoned application > Definition",
        "Abandoned application > Revival",
        "Application publication",
    }

    # Every record is an index_entry; all slugs are unique.
    assert all(r["kind"] == "index_entry" for r in recs)
    slugs = [r["citation_normalized"] for r in recs]
    assert len(set(slugs)) == len(slugs)

    # Hierarchy: sub-entries carry the parent term as parent_citation.
    definition = by_title["Abandoned application > Definition"]
    assert definition["parent_citation"] == "Index: Abandoned application"
    assert definition["citation_normalized"] == "index_abandoned_application__definition"
    assert "MPEP 203.05" in definition["body_md"]

    # Multiple refs are preserved (semicolon list in the source).
    revival = by_title["Abandoned application > Revival"]
    assert "MPEP 711.03(c)" in revival["body_md"]
    assert "MPEP 1893.02" in revival["body_md"]

    # See-also points to another index term, not a section; the top term has
    # no own refs but records the cross-reference.
    top = by_title["Abandoned application"]
    assert top["parent_citation"] is None
    assert "See also (index): Abandonment" in top["body_md"]
    assert "Referenced sections" not in top["body_md"]

    # Output is ASCII-safe.
    for r in recs:
        blob = r["citation"] + r["title"] + r["body_md"]
        assert all(ord(c) < 128 for c in blob)
