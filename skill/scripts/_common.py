"""Shared helpers for mpep-lookup skill.

This module is the **single source of truth** for citation parsing,
DB connection, and output formatting. The build phase imports from here
via sys.path manipulation; runtime scripts import directly.

Owns:
- parse_citation(text, kind_hint=None) -> dict
- normalize(citation) -> str
- connect_db() -> sqlite3.Connection (with fail-loud diagnostics)
- format_record(row, json=False) -> str
- ascii_safe(text) -> str
- collapse_whitespace(text) -> str

ASCII-only source (per design constraints).
"""

import json as _json
import pathlib
import re
import sqlite3
import sys


DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "mpep.db"


# Citation regex patterns. Order matters: more specific first.

_RE_USC_NUM = r"(?P<num>\d{1,4}(?:\([a-z]\)(?:\(\d+\))?)?)"
_RE_CFR_NUM = r"(?P<num>\d{1,3}\.\d{1,4}(?:\([a-z]\))?)"

# Variant marker: a parenthetical immediately following a rule/statute
# number. Distinguishes (pre-AIA), (note), (pre-PLT), (pre-PLT (AIA)),
# (transitional), date-range variants like (2012-09-16 thru 2013-12-17),
# and date-cutoff variants like (pre-2013-03-16). Allows one level of
# nested parens for cases like (pre-PLT (AIA)).
_VARIANT_RE = r"\((?:[^()]+|\([^()]*\))*\)"

# Heading-form patterns (for parsing HTML headings during build phase).
# num allows a SEQUENCE of parenthetical groups so sub-subsections like
# 2106.04(d)(1) and 1002.02(c)(1) parse (not just a single (letter)). USPTO
# emits each of these as its own <h1 class="page-title">; matching only one
# paren group silently dropped them at build time. See LESSONS.md.
_RE_HEADING_MPEP = re.compile(
    r"^\s*(?P<num>\d{1,4}(?:\.\d{1,2})?(?:\([a-z0-9]+\))*)\s+(?P<title>.+?)"
    r"(?:\s*\[(?P<rev>R[‐‑‒–—―\-]\d{2}\.\d{4})\])?\s*$",
    re.DOTALL,
)
_RE_HEADING_STATUTE = re.compile(
    r"^\s*(?:AIA\s+)?35\s+U\.S\.C\.\s+" + _RE_USC_NUM
    + r"(?:\s+(?P<variant>" + _VARIANT_RE + r"))?"
    + r"\s+(?P<title>.+?)\.?\s*$",
    re.DOTALL,
)
_RE_HEADING_CFR = re.compile(
    r"^\s*" + _RE_CFR_NUM
    + r"(?:\s+(?P<variant>" + _VARIANT_RE + r"))?"
    + r"\s+(?P<title>.+?)\.?\s*$",
    re.DOTALL,
)
_RE_HEADING_FP = re.compile(
    r"^\s*(?P<num>\d{1,3}\.\d{1,3}(?:\.[A-Za-z0-9]{1,8})?)\s+(?P<title>.+?)\s*$",
    re.DOTALL,
)


# User-input patterns (for runtime lookup). Tolerant of common variants.
_RE_USER_MPEP = re.compile(
    r"^\s*(?:M\.?P\.?E\.?P\.?\s*)?"
    r"(?:s\.?\s*|Section\s+|Sec\.\s*|§\s*)?"
    r"(?P<num>\d{1,4}(?:\.\d{1,2})?)"
    r"(?P<subs>(?:\s*\(\s*[a-z0-9]+\s*\))*)"
    r"(?:\s+sub\s+(?P<sub2>[a-z]))?"
    r"\s*$",
    re.IGNORECASE,
)
_RE_USER_USC = re.compile(
    r"^\s*(?:AIA\s+)?35\s*U\.?S\.?C\.?\s*(?:§\s*)?"
    + _RE_USC_NUM
    + r"(?:\s*(?P<variant>" + _VARIANT_RE + r"))?"
    + r"\s*$",
    re.IGNORECASE,
)
_RE_USER_CFR = re.compile(
    r"^\s*37\s*C\.?F\.?R\.?\s*(?:§\s*)?" + _RE_CFR_NUM
    + r"(?:\s*(?P<variant>" + _VARIANT_RE + r"))?"
    + r"\s*$",
    re.IGNORECASE,
)
_RE_USER_FP = re.compile(
    r"^\s*(?:Form\s+Paragraph|FP|¶)\s*"
    r"(?P<num>\d{1,3}\.\d{1,3}(?:\.[A-Za-z0-9]{1,8})?)\s*$",
    re.IGNORECASE,
)


def _ascii_dashes(text: str) -> str:
    """Replace unicode dash variants with ASCII hyphen-minus."""
    for d in "‐‑‒–—―":
        text = text.replace(d, "-")
    return text


def _normalize_variant_for_citation(variant_text: str) -> str:
    """Render a captured variant marker for the canonical citation field.

    Strips outer parens already in variant_text? No - we keep them. Just
    normalizes unicode dashes to ASCII so the citation field is 7-bit ASCII.

    Examples:
        '(pre-AIA)' -> '(pre-AIA)'
        '(pre‑AIA)' -> '(pre-AIA)'
        '(pre‑PLT (AIA))' -> '(pre-PLT (AIA))'
        '(2012‑09‑16 thru 2013‑12‑17)'
            -> '(2012-09-16 thru 2013-12-17)'

    Pre-AIA spelling is also normalized to canonical 'pre-AIA' (mixed-case
    spellings reduce to one).
    """
    s = _ascii_dashes(variant_text)
    # Canonicalize a few common ones: (pre AIA), (Pre-AIA), (PRE-AIA) -> (pre-AIA)
    s = re.sub(r"\(pre[\s\-]*aia\)", "(pre-AIA)", s, flags=re.IGNORECASE)
    return s


def normalize(citation: str) -> str:
    """Compute citation_normalized for SQL key lookup.

    Examples:
        "MPEP 2141.01(a)" -> "mpep_2141_01_a"
        "35 USC 102" -> "35_usc_102"
        "35 USC 102 (pre-AIA)" -> "35_usc_102_pre_aia"
        "37 CFR 1.131" -> "37_cfr_1_131"
        "Form Paragraph 7.05" -> "fp_7_05"
    """
    s = citation.strip().lower()
    # Replace U+2011 (non-breaking hyphen) with ASCII hyphen
    s = s.replace("‑", "-")
    # Replace section symbol
    s = s.replace("§", "")
    # Replace dots, parens, hyphens, whitespace with single underscore
    s = re.sub(r"[\s.()\-]+", "_", s)
    # Collapse multiple underscores
    s = re.sub(r"_+", "_", s).strip("_")
    # Common abbreviation harmonization
    s = re.sub(r"^m_p_e_p_", "mpep_", s)
    s = re.sub(r"^c_f_r_", "cfr_", s)
    s = re.sub(r"_u_s_c_", "_usc_", s)
    s = re.sub(r"^form_paragraph_", "fp_", s)
    return s


def parse_citation(text: str, kind_hint: str | None = None) -> dict:
    """Parse a citation string into structured form.

    Args:
        text: user-input citation, e.g. "MPEP 2141", "35 USC 102 (pre-AIA)"
        kind_hint: optional kind override (used by build phase when parsing
                   HTML headings where kind is known from file context).
                   One of: "mpep_section", "statute", "cfr_rule",
                   "form_paragraph", "appendix".

    Returns:
        dict with keys: kind, citation, citation_normalized

    Raises:
        ValueError if no pattern matches.
    """
    s = text.strip()
    s_norm = s.replace("‑", "-").replace(" ", " ")

    # When parsing a heading from a known file context, use kind_hint.
    if kind_hint == "statute":
        m = _RE_HEADING_STATUTE.match(s_norm)
        if m:
            num = m.group("num")
            variant = m.group("variant")
            citation = f"35 USC {num}"
            if variant:
                citation = f"{citation} {_normalize_variant_for_citation(variant)}"
            return {"kind": "statute", "citation": citation,
                    "citation_normalized": normalize(citation),
                    "title": m.group("title").strip().rstrip(".")}
    if kind_hint == "cfr_rule":
        m = _RE_HEADING_CFR.match(s_norm)
        if m:
            num = m.group("num")
            variant = m.group("variant")
            citation = f"37 CFR {num}"
            if variant:
                citation = f"{citation} {_normalize_variant_for_citation(variant)}"
            return {"kind": "cfr_rule", "citation": citation,
                    "citation_normalized": normalize(citation),
                    "title": m.group("title").strip().rstrip(".")}
    if kind_hint == "form_paragraph":
        m = _RE_HEADING_FP.match(s_norm)
        if m:
            num = m.group("num")
            citation = f"Form Paragraph {num}"
            return {"kind": "form_paragraph", "citation": citation,
                    "citation_normalized": normalize(citation),
                    "title": m.group("title").strip()}
    if kind_hint == "mpep_section":
        m = _RE_HEADING_MPEP.match(s_norm)
        if m:
            num = m.group("num")
            citation = f"MPEP {num}"
            rev = m.group("rev")
            if rev:
                # Normalize any unicode dash in revision string
                for d in "‐‑‒–—―":
                    rev = rev.replace(d, "-")
            return {"kind": "mpep_section", "citation": citation,
                    "citation_normalized": normalize(citation),
                    "title": m.group("title").strip(),
                    "revision": rev}

    # Unhinted (user input): try patterns in order of specificity.
    m = _RE_USER_USC.match(s_norm)
    if m:
        num = m.group("num")
        variant = m.group("variant")
        citation = f"35 USC {num}"
        if variant:
            citation = f"{citation} {_normalize_variant_for_citation(variant)}"
        return {"kind": "statute", "citation": citation,
                "citation_normalized": normalize(citation)}

    m = _RE_USER_CFR.match(s_norm)
    if m:
        num = m.group("num")
        variant = m.group("variant")
        citation = f"37 CFR {num}"
        if variant:
            citation = f"{citation} {_normalize_variant_for_citation(variant)}"
        return {"kind": "cfr_rule", "citation": citation,
                "citation_normalized": normalize(citation)}

    m = _RE_USER_FP.match(s_norm)
    if m:
        num = m.group("num")
        citation = f"Form Paragraph {num}"
        return {"kind": "form_paragraph", "citation": citation,
                "citation_normalized": normalize(citation)}

    m = _RE_USER_MPEP.match(s_norm)
    if m:
        num = m.group("num")
        # Append each parenthetical sub-group in order: (d)(1) -> "(d)(1)".
        for part in re.findall(r"\(\s*([a-z0-9]+)\s*\)", m.group("subs") or "",
                               re.IGNORECASE):
            num = f"{num}({part.lower()})"
        if m.group("sub2"):  # "... sub a" spoken/written form
            num = f"{num}({m.group('sub2').lower()})"
        citation = f"MPEP {num}"
        return {"kind": "mpep_section", "citation": citation,
                "citation_normalized": normalize(citation)}

    raise ValueError(f"Unrecognized citation format: {text!r}")


def connect_db(path: pathlib.Path | None = None) -> sqlite3.Connection:
    """Open the bundled SQLite file. Fail loud if missing or corrupt."""
    db_path = path if path is not None else DB_PATH
    if not db_path.exists():
        print(
            f"MPEP database missing or unreadable at {db_path}. "
            "Run build pipeline to regenerate.",
            file=sys.stderr,
        )
        sys.exit(3)
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT 1 FROM sections LIMIT 1")
        conn.execute("SELECT 1 FROM sections_fts LIMIT 1")
        return conn
    except sqlite3.DatabaseError as exc:
        print(
            f"MPEP database corrupt or schema mismatch at {db_path}: {exc}. "
            "Rebuild required.",
            file=sys.stderr,
        )
        sys.exit(3)


def format_record(row: dict, as_json: bool = False) -> str:
    """Format a sections row for output."""
    if as_json:
        return _json.dumps(row, ensure_ascii=True, indent=2)
    parts = [
        f"Citation: {row['citation']}",
        f"Title: {row['title']}",
        f"Kind: {row['kind']}",
    ]
    if row.get("revision"):
        parts.append(f"Revision: {row['revision']}")
    if row.get("chapter"):
        parts.append(f"Chapter: {row['chapter']}")
    if row.get("parent_citation"):
        parts.append(f"Parent: {row['parent_citation']}")
    parts.append(f"Source: {row['source_url']}")
    parts.append("")
    parts.append(row["body_md"])
    return "\n".join(parts)


# Replacements for ASCII-safe transform. Done at module level so the dict
# is built once.
_ASCII_REPLACEMENTS = {
    "‘": "'",       # left single quote
    "’": "'",       # right single quote
    "“": '"',       # left double quote
    "”": '"',       # right double quote
    "–": "-",       # en-dash
    "—": "--",      # em-dash
    "…": "...",     # ellipsis
    " ": " ",       # non-breaking space
    "‑": "-",       # non-breaking hyphen
    "‐": "-",       # hyphen
    "‒": "-",       # figure dash
    "―": "-",       # horizontal bar
    "§": "Section ",  # section symbol -> word form
    "¶": "Paragraph ",  # pilcrow / paragraph mark
}


def ascii_safe(text: str) -> str:
    """Convert text to 7-bit ASCII safe form.

    - Smart quotes -> straight ASCII quotes
    - Em-dash -> --, en-dash -> -, hyphens unified
    - Ellipsis -> ...
    - Non-breaking space -> regular space
    - Section symbol (Sec) -> "Section "
    - Pilcrow (P) -> "Paragraph "
    - Any remaining non-ASCII char is dropped (defensive)
    """
    for src, dst in _ASCII_REPLACEMENTS.items():
        text = text.replace(src, dst)
    # Defensive: strip any other non-ASCII so output is guaranteed 7-bit
    return text.encode("ascii", errors="ignore").decode("ascii")


def collapse_whitespace(text: str) -> str:
    """Final post-processing pass.

    - Runs of 3+ newlines collapsed to 2 (paragraph break)
    - Trailing whitespace per line stripped
    - Leading/trailing whitespace of the whole document stripped
    Per-paragraph whitespace collapse should already have happened at the
    NavigableString level during HTML conversion (parser responsibility);
    this function is only the final tidy-up pass.
    """
    # Strip trailing whitespace per line
    lines = [ln.rstrip() for ln in text.split("\n")]
    text = "\n".join(lines)
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def collapse_inline(text: str) -> str:
    """Collapse a NavigableString-level run of whitespace to single spaces.

    Use during HTML -> markdown conversion on raw text nodes. This handles
    USPTO's heavy HTML indentation that bleeds into output otherwise.
    """
    return re.sub(r"\s+", " ", text)
