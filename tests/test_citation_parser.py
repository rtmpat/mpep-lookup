"""Unit tests for citation parsing.

Tests cover the variety of citation strings a patent practitioner actually
writes, copy-pasted from real OA responses, briefs, and emails. This is a
living test suite - add new failing strings as you encounter them.
"""

import pathlib
import sys

import pytest

# Make skill/scripts importable
SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "skill" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _common import normalize, parse_citation  # noqa: E402


# (input, expected_kind, expected_citation, expected_normalized)
CASES_USER_INPUT = [
    # MPEP top-level
    ("MPEP 2141", "mpep_section", "MPEP 2141", "mpep_2141"),
    ("MPEP  2141", "mpep_section", "MPEP 2141", "mpep_2141"),
    ("M.P.E.P. 2141", "mpep_section", "MPEP 2141", "mpep_2141"),
    ("mpep 2141", "mpep_section", "MPEP 2141", "mpep_2141"),
    ("2141", "mpep_section", "MPEP 2141", "mpep_2141"),
    # MPEP subsection with paren-letter
    ("MPEP 2141.01(a)", "mpep_section", "MPEP 2141.01(a)", "mpep_2141_01_a"),
    ("MPEP 2141.01 (a)", "mpep_section", "MPEP 2141.01(a)", "mpep_2141_01_a"),
    ("M.P.E.P. 2141.01(a)", "mpep_section", "MPEP 2141.01(a)", "mpep_2141_01_a"),
    ("MPEP s. 2141.01(a)", "mpep_section", "MPEP 2141.01(a)", "mpep_2141_01_a"),
    ("MPEP § 2141.01(a)", "mpep_section", "MPEP 2141.01(a)", "mpep_2141_01_a"),
    ("MPEP Section 2141.01 sub a", "mpep_section", "MPEP 2141.01(a)", "mpep_2141_01_a"),
    ("mpep 2141.01(A)", "mpep_section", "MPEP 2141.01(a)", "mpep_2141_01_a"),
    # 35 USC
    ("35 USC 102", "statute", "35 USC 102", "35_usc_102"),
    ("35 U.S.C. 102", "statute", "35 USC 102", "35_usc_102"),
    ("35 U.S.C. § 102", "statute", "35 USC 102", "35_usc_102"),
    ("35 USC §102", "statute", "35 USC 102", "35_usc_102"),
    ("35 usc 102", "statute", "35 USC 102", "35_usc_102"),
    # 35 USC pre-AIA
    ("35 USC 102 (pre-AIA)", "statute", "35 USC 102 (pre-AIA)", "35_usc_102_pre_aia"),
    ("35 U.S.C. 102 (pre-AIA)", "statute", "35 USC 102 (pre-AIA)", "35_usc_102_pre_aia"),
    ("35 USC 102 (pre AIA)", "statute", "35 USC 102 (pre-AIA)", "35_usc_102_pre_aia"),
    # 37 CFR
    ("37 CFR 1.131", "cfr_rule", "37 CFR 1.131", "37_cfr_1_131"),
    ("37 C.F.R. 1.131", "cfr_rule", "37 CFR 1.131", "37_cfr_1_131"),
    ("37 C.F.R. § 1.131", "cfr_rule", "37 CFR 1.131", "37_cfr_1_131"),
    ("37 cfr 1.131", "cfr_rule", "37 CFR 1.131", "37_cfr_1_131"),
    # Form Paragraph
    ("Form Paragraph 7.05", "form_paragraph", "Form Paragraph 7.05", "fp_7_05"),
    ("FP 7.05", "form_paragraph", "Form Paragraph 7.05", "fp_7_05"),
    ("¶ 7.05", "form_paragraph", "Form Paragraph 7.05", "fp_7_05"),
    ("Form Paragraph 7.05.aia", "form_paragraph", "Form Paragraph 7.05.aia", "fp_7_05_aia"),
    # USC variants beyond pre-AIA
    ("35 USC 100 (note)", "statute", "35 USC 100 (note)", "35_usc_100_note"),
    ("35 USC 100 (transitional)", "statute", "35 USC 100 (transitional)", "35_usc_100_transitional"),
    # CFR variants - the bug we just fixed
    ("37 CFR 1.14 (pre-AIA)", "cfr_rule", "37 CFR 1.14 (pre-AIA)", "37_cfr_1_14_pre_aia"),
    ("37 CFR 1.53 (pre-PLT)", "cfr_rule", "37 CFR 1.53 (pre-PLT)", "37_cfr_1_53_pre_plt"),
    ("37 CFR 1.53 (pre-PLT (AIA))", "cfr_rule",
     "37 CFR 1.53 (pre-PLT (AIA))", "37_cfr_1_53_pre_plt_aia"),
]


@pytest.mark.parametrize("text,kind,citation,norm", CASES_USER_INPUT)
def test_parse_user_input(text: str, kind: str, citation: str, norm: str) -> None:
    result = parse_citation(text)
    assert result["kind"] == kind, f"{text!r} -> wrong kind"
    assert result["citation"] == citation, f"{text!r} -> wrong citation"
    assert result["citation_normalized"] == norm, f"{text!r} -> wrong norm"


def test_parse_unrecognized_raises() -> None:
    with pytest.raises(ValueError):
        parse_citation("garbled nonsense")
    with pytest.raises(ValueError):
        parse_citation("")


def test_normalize_idempotent() -> None:
    """normalize(normalize(x)) == normalize(x) for valid citations."""
    for text, _, citation, _ in CASES_USER_INPUT:
        n1 = normalize(citation)
        n2 = normalize(n1)
        assert n1 == n2, f"normalize not idempotent for {citation!r}"


def test_heading_parse_mpep_section() -> None:
    """Build-phase: parse MPEP section heading text."""
    h = "2141 Examination Guidelines for Determining Obviousness Under 35 U.S.C. 103 [R-01.2024]"
    r = parse_citation(h, kind_hint="mpep_section")
    assert r["kind"] == "mpep_section"
    assert r["citation"] == "MPEP 2141"
    assert r["citation_normalized"] == "mpep_2141"
    assert r["title"].startswith("Examination Guidelines")
    assert r["revision"] == "R-01.2024"


def test_heading_parse_mpep_subsection() -> None:
    h = "2141.01(a) Analogous and Nonanalogous Art [R-01.2024]"
    r = parse_citation(h, kind_hint="mpep_section")
    assert r["citation"] == "MPEP 2141.01(a)"
    assert r["citation_normalized"] == "mpep_2141_01_a"
    assert r["title"] == "Analogous and Nonanalogous Art"
    assert r["revision"] == "R-01.2024"


def test_heading_parse_statute_aia() -> None:
    h = "35 U.S.C. 6 Patent Trial and Appeal Board."
    r = parse_citation(h, kind_hint="statute")
    assert r["kind"] == "statute"
    assert r["citation"] == "35 USC 6"
    assert r["citation_normalized"] == "35_usc_6"
    assert r["title"] == "Patent Trial and Appeal Board"


def test_heading_parse_statute_preaia_unicode() -> None:
    """Pre-AIA marker uses U+2011 NON-BREAKING HYPHEN in actual MPEP HTML."""
    h = "35 U.S.C. 6 (pre‑AIA) Board of Patent Appeals and Interferences."
    r = parse_citation(h, kind_hint="statute")
    assert r["kind"] == "statute"
    assert r["citation"] == "35 USC 6 (pre-AIA)"
    assert r["citation_normalized"] == "35_usc_6_pre_aia"


def test_heading_parse_cfr_rule() -> None:
    h = "1.131 Affidavit or declaration of prior invention or derivation."
    r = parse_citation(h, kind_hint="cfr_rule")
    assert r["kind"] == "cfr_rule"
    assert r["citation"] == "37 CFR 1.131"
    assert r["citation_normalized"] == "37_cfr_1_131"
    assert "Affidavit" in r["title"]


def test_heading_parse_form_paragraph() -> None:
    h = "2.01 Possible Status as Divisional"
    r = parse_citation(h, kind_hint="form_paragraph")
    assert r["kind"] == "form_paragraph"
    assert r["citation"] == "Form Paragraph 2.01"
    assert r["citation_normalized"] == "fp_2_01"
    assert r["title"] == "Possible Status as Divisional"


def test_heading_parse_cfr_pre_aia() -> None:
    """Regression: CFR (pre-AIA) was collapsing with current. Now fixed."""
    h = "1.14 (pre‑AIA) Patent applications preserved in confidence."
    r = parse_citation(h, kind_hint="cfr_rule")
    assert r["citation"] == "37 CFR 1.14 (pre-AIA)"
    assert r["citation_normalized"] == "37_cfr_1_14_pre_aia"
    assert r["title"] == "Patent applications preserved in confidence"


def test_heading_parse_cfr_nested_paren_variant() -> None:
    """Some CFR rules have nested parens: (pre-PLT (AIA))."""
    h = "1.53 (pre‑PLT (AIA)) Application number, filing date, and completion of application."
    r = parse_citation(h, kind_hint="cfr_rule")
    assert r["citation"] == "37 CFR 1.53 (pre-PLT (AIA))"
    assert r["citation_normalized"] == "37_cfr_1_53_pre_plt_aia"


def test_heading_parse_cfr_date_range_variant() -> None:
    """Some CFR rules have date-range variants from AIA transition periods."""
    h = "1.14 (2012‑09‑16 thru 2013‑12‑17) Some title."
    r = parse_citation(h, kind_hint="cfr_rule")
    assert r["citation"] == "37 CFR 1.14 (2012-09-16 thru 2013-12-17)"
    assert r["citation_normalized"] == "37_cfr_1_14_2012_09_16_thru_2013_12_17"


def test_heading_parse_statute_note() -> None:
    """Regression: USC (note) was collapsing with current statute. Now fixed."""
    h = "35 U.S.C. 100 (note) AIA First inventor to file provisions."
    r = parse_citation(h, kind_hint="statute")
    assert r["citation"] == "35 USC 100 (note)"
    assert r["citation_normalized"] == "35_usc_100_note"


def test_heading_parse_statute_transitional() -> None:
    h = "35 U.S.C. 102 (transitional) Some transitional rule."
    r = parse_citation(h, kind_hint="statute")
    assert r["citation"] == "35 USC 102 (transitional)"
    assert r["citation_normalized"] == "35_usc_102_transitional"
