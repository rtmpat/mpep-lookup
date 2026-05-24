"""Parse fetched USPTO HTML files into per-record markdown with YAML frontmatter.

Universal record boundary: <h1 class="page-title">. The body of a record is
everything from one page-title h1 to the next.

Reads from build/raw_html/, writes to intermediate/sections/ and
intermediate/appendices/. ASCII-safe markdown body. Frontmatter contains
citation, citation_normalized, title, kind, chapter, parent_citation,
revision, source_url.

Usage: python build/02_parse_to_markdown.py
"""

import json
import pathlib
import sys
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag

# Import _common from skill/scripts (single source of truth per Option 1B)
SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent / "skill" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _common import (  # noqa: E402
    ascii_safe,
    collapse_inline,
    collapse_whitespace,
    parse_citation,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Source-of-truth URL prefix for source_url frontmatter field.
USPTO_BASE = "https://www.uspto.gov/web/offices/pac/mpep/"


# --- HTML -> markdown helpers ----------------------------------------------


def _md_text(node: Tag | NavigableString) -> str:
    """Recursively serialize a BS4 node to markdown.

    This is a simplified converter sufficient for MPEP content. It preserves
    paragraphs, headings, lists, blockquotes, italics, bold, and tables (as
    inline HTML for complex cases). It strips USPTO chrome and link targets.
    """
    if isinstance(node, NavigableString):
        return collapse_inline(str(node))

    name = node.name
    if name in ("script", "style", "head", "noscript"):
        return ""

    # Skip USPTO chrome we know about
    if name == "a":
        # Skip the "Add a Note" pencil icons and pure anchor targets
        cls = node.get("class") or []
        if "noteJump" in cls or "image-link" in cls or node.get("title", "").startswith("Add a Note"):
            return ""
        # Convert links to plain text
        return _md_children(node)
    if name == "img":
        return ""

    if name == "p":
        inner = _md_children(node).strip()
        return f"\n\n{inner}\n" if inner else ""
    if name in ("h1", "h2"):
        inner = _md_children(node).strip()
        # We do NOT emit our own header markers - the caller handles record
        # boundaries via h1.page-title detection. Other h1/h2 occurring
        # mid-record are stripped to avoid double-headings.
        return f"\n\n{inner}\n" if inner else ""
    if name == "h3":
        inner = _md_children(node).strip()
        return f"\n\n### {inner}\n" if inner else ""
    if name == "h4":
        # h4.USC = statute inset
        cls = node.get("class") or []
        inner = _md_children(node).strip()
        if "USC" in cls or "CFR" in cls:
            return f"\n\n#### *{inner}*\n" if inner else ""
        return f"\n\n#### {inner}\n" if inner else ""
    if name in ("h5", "h6"):
        inner = _md_children(node).strip()
        return f"\n\n##### {inner}\n" if inner else ""
    if name == "blockquote":
        inner = _md_children(node).strip()
        if not inner:
            return ""
        # Prefix every line with "> "
        lines = inner.split("\n")
        return "\n\n" + "\n".join(f"> {ln}" if ln else ">" for ln in lines) + "\n"
    if name == "ul":
        items = []
        for li in node.find_all("li", recursive=False):
            items.append(f"- {_md_children(li).strip()}")
        return "\n\n" + "\n".join(items) + "\n" if items else ""
    if name == "ol":
        items = []
        for i, li in enumerate(node.find_all("li", recursive=False), 1):
            items.append(f"{i}. {_md_children(li).strip()}")
        return "\n\n" + "\n".join(items) + "\n" if items else ""
    if name == "li":
        return _md_children(node)
    if name in ("i", "em"):
        return f"*{_md_children(node)}*"
    if name in ("b", "strong"):
        return f"**{_md_children(node)}**"
    if name == "br":
        return "\n"
    if name == "table":
        # Preserve table as HTML (simpler than full GFM conversion for now)
        return f"\n\n{str(node)}\n"
    if name == "div":
        return _md_children(node)
    if name == "span":
        return _md_children(node)

    # Default: recurse
    return _md_children(node)


def _md_children(node: Tag) -> str:
    parts = []
    for child in node.children:
        parts.append(_md_text(child))
    return "".join(parts)


def _normalize_whitespace(md: str) -> str:
    """Final post-process; delegates to _common.collapse_whitespace."""
    return collapse_whitespace(md)


# --- Per-kind record splitting ---------------------------------------------


def _slice_records(soup: BeautifulSoup) -> list[tuple[Tag, list[Tag | NavigableString]]]:
    """Find all h1.page-title boundaries and group body content per record.

    Returns list of (heading_h1, [body_node_1, body_node_2, ...]).
    """
    headings = soup.find_all("h1", class_="page-title")
    records = []
    for i, h in enumerate(headings):
        # Skip empty/whitespace-only headings (chrome)
        if not h.get_text().strip():
            continue
        # Body content is all sibling nodes of `h` (or its parent's next
        # siblings) up to but not including the next page-title h1.
        next_h = headings[i + 1] if i + 1 < len(headings) else None
        body_nodes = []
        # Walk forward through the document collecting nodes until next_h
        cursor = h
        while True:
            sib = cursor.next_sibling
            if sib is None:
                # Step out one level and continue
                parent = cursor.parent
                if parent is None or parent.name == "body" or parent.name == "[document]":
                    break
                cursor = parent
                continue
            cursor = sib
            # Check if we've reached the next heading
            if isinstance(sib, Tag):
                if sib is next_h:
                    break
                # Or if the next heading is a descendant of this sibling
                if next_h is not None and next_h in sib.descendants:
                    # Collect the part of `sib` BEFORE next_h, then stop
                    for child in sib.children:
                        if isinstance(child, Tag) and (child is next_h or next_h in (child.descendants if hasattr(child, "descendants") else [])):
                            break
                        body_nodes.append(child)
                    break
            body_nodes.append(sib)
        records.append((h, body_nodes))
    return records


def parse_mpep_section_file(html: str, source_url: str) -> Iterable[dict]:
    """Parse an sNNNN.html file. Emits multiple records (top + subsections).

    Each <h1 class="page-title"> with a citation-pattern heading is one record.
    """
    soup = BeautifulSoup(html, "lxml")
    chapter = _chapter_from_url(source_url)
    for heading_h1, body_nodes in _slice_records(soup):
        heading_text = " ".join(heading_h1.get_text().split())
        try:
            parsed = parse_citation(heading_text, kind_hint="mpep_section")
        except ValueError:
            # Heading didn't match - might be an end-of-document chrome h1
            continue
        body_md = "".join(_md_text(n) for n in body_nodes)
        body_md = ascii_safe(body_md)
        body_md = _normalize_whitespace(body_md)
        # Determine parent citation
        num = parsed["citation"].removeprefix("MPEP ").strip()
        parent = _mpep_parent(num)
        yield {
            "citation": parsed["citation"],
            "citation_normalized": parsed["citation_normalized"],
            "title": ascii_safe(parsed["title"]),
            "kind": "mpep_section",
            "chapter": chapter,
            "parent_citation": f"MPEP {parent}" if parent else None,
            "revision": parsed.get("revision"),
            "body_md": body_md,
            "source_url": source_url,
        }


def parse_appendix_l_file(html: str, source_url: str) -> Iterable[dict]:
    """Parse Appendix L (35 USC). Emits one record per statute (AIA + pre-AIA pairs)."""
    soup = BeautifulSoup(html, "lxml")
    for heading_h1, body_nodes in _slice_records(soup):
        heading_text = " ".join(heading_h1.get_text().split())
        if not heading_text.startswith(("35 U.S.C.", "AIA 35 U.S.C.")):
            # Could be Subtitle, Chapter, Part heading - skip
            continue
        try:
            parsed = parse_citation(heading_text, kind_hint="statute")
        except ValueError:
            continue
        body_md = "".join(_md_text(n) for n in body_nodes)
        body_md = ascii_safe(body_md)
        body_md = _normalize_whitespace(body_md)
        yield {
            "citation": parsed["citation"],
            "citation_normalized": parsed["citation_normalized"],
            "title": ascii_safe(parsed["title"]),
            "kind": "statute",
            "chapter": None,
            "parent_citation": None,
            "revision": None,
            "body_md": body_md,
            "source_url": source_url,
        }


def parse_appendix_r_file(html: str, source_url: str) -> Iterable[dict]:
    """Parse Appendix R (37 CFR). Emits one record per rule."""
    soup = BeautifulSoup(html, "lxml")
    import re
    rule_pattern = re.compile(r"^\s*\d{1,3}\.\d{1,4}\s+\S")
    for heading_h1, body_nodes in _slice_records(soup):
        heading_text = " ".join(heading_h1.get_text().split())
        if not rule_pattern.match(heading_text):
            continue
        try:
            parsed = parse_citation(heading_text, kind_hint="cfr_rule")
        except ValueError:
            continue
        body_md = "".join(_md_text(n) for n in body_nodes)
        body_md = ascii_safe(body_md)
        body_md = _normalize_whitespace(body_md)
        yield {
            "citation": parsed["citation"],
            "citation_normalized": parsed["citation_normalized"],
            "title": ascii_safe(parsed["title"]),
            "kind": "cfr_rule",
            "chapter": None,
            "parent_citation": None,
            "revision": None,
            "body_md": body_md,
            "source_url": source_url,
        }


def parse_form_paragraphs_file(html: str, source_url: str) -> Iterable[dict]:
    """Parse Form Paragraphs Consolidated chapter."""
    soup = BeautifulSoup(html, "lxml")
    import re
    fp_pattern = re.compile(r"^\s*\d{1,3}\.\d{1,3}(?:\.[A-Za-z0-9]{1,8})?\s+\S")
    for heading_h1, body_nodes in _slice_records(soup):
        heading_text = " ".join(heading_h1.get_text().split())
        if not fp_pattern.match(heading_text):
            continue
        try:
            parsed = parse_citation(heading_text, kind_hint="form_paragraph")
        except ValueError:
            continue
        body_md = "".join(_md_text(n) for n in body_nodes)
        body_md = ascii_safe(body_md)
        body_md = _normalize_whitespace(body_md)
        yield {
            "citation": parsed["citation"],
            "citation_normalized": parsed["citation_normalized"],
            "title": ascii_safe(parsed["title"]),
            "kind": "form_paragraph",
            "chapter": None,
            "parent_citation": None,
            "revision": None,
            "body_md": body_md,
            "source_url": source_url,
        }


# --- Subject Matter Index (mpep-index-X.html) -------------------------------


def _index_text_excluding_ul(node: Tag) -> str:
    """Text of `node`, skipping nested <ul> subtrees (those are sub-entries)."""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, Tag):
            if child.name == "ul":
                continue
            parts.append(_index_text_excluding_ul(child))
        elif isinstance(child, NavigableString):
            parts.append(str(child))
    return "".join(parts)


def _index_in_subentry(node: Tag, stop: Tag) -> bool:
    """True if `node` sits inside a <ul> nested within `stop` (a sub-entry)."""
    p = node.parent
    while p is not None and p is not stop:
        if isinstance(p, Tag) and p.name == "ul":
            return True
        p = p.parent
    return False


def _extract_index_li(li: Tag) -> tuple[str, list[str], list[str]]:
    """Pull (term, section_refs, see_also_terms) from one index <li>.

    Section refs are <a href="sNNN.html"> link texts (MPEP section numbers);
    see-also targets are <a href="mpep-index-..."> link texts (other index
    terms). Both are restricted to this entry's own content, excluding any
    nested <ul> of sub-entries.
    """
    import re

    refs: list[str] = []
    see_also: list[str] = []
    for a in li.find_all("a", href=True):
        if _index_in_subentry(a, li):
            continue
        href = a["href"]
        txt = " ".join(a.get_text(" ", strip=True).split())
        if not txt:
            continue
        if "mpep-index-" in href:
            see_also.append(txt)
        elif re.match(r"s\d+\.html", href):
            refs.append(txt)
    raw = _index_text_excluding_ul(li)
    # Term = text before the em/en dash (refs follow it), then cut any
    # "( See ... )" / "( See also ... )" cross-reference run (handles nested
    # parens by truncating at the marker rather than balancing).
    term = re.split(r"[\u2013\u2014]", raw)[0]
    term = re.split(r"\(\s*[Ss]ee\b", term, maxsplit=1)[0]
    term = re.sub(r"\s+", " ", term).strip().strip("()").strip()
    return term, refs, see_also


def _index_slug(full_path: list[str]) -> str:
    """Unique citation_normalized for an index entry, from its term path.

    Levels are joined with '__' so a sibling term and a nested term never
    collapse (e.g. 'Application publication' vs 'Application > Publication').
    Always prefixed 'index_' so it cannot collide with section / statute /
    rule slugs.
    """
    import re

    parts = []
    for term in full_path:
        seg = re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")
        if seg:
            parts.append(seg)
    core = "__".join(parts)
    return "index_" + core if core else "index_entry"


def _index_body_md(full_path: list[str], refs: list[str], see_also: list[str]) -> str:
    """Body for an index entry.

    Section refs are written as 'MPEP <num>' tokens so the search-time router
    (index_lookup.py) can extract and resolve them with parse_citation. This is
    the option-c design: refs live in body_md, no separate join table.
    """
    lines = [" > ".join(full_path) if full_path else "Index entry"]
    if refs:
        lines += ["", "Referenced sections: " + "; ".join(f"MPEP {r}" for r in refs)]
    if see_also:
        lines += ["", "See also (index): " + "; ".join(see_also)]
    return "\n".join(lines)


def _walk_index_li(li: Tag, parent_path: list[str], parent_citation: str | None,
                   source_url: str, revision: str | None) -> Iterable[dict]:
    """Recursively emit index_entry records for `li` and its sub-entries."""
    term, refs, see_also = _extract_index_li(li)
    full_path = parent_path + [term] if term else list(parent_path)
    citation = "Index: " + " > ".join(full_path) if full_path else "Index"
    body_md = _index_body_md(full_path, refs, see_also)
    yield {
        "citation": ascii_safe(citation),
        "citation_normalized": _index_slug(full_path),
        "title": ascii_safe(" > ".join(full_path)) or "Index",
        "kind": "index_entry",
        "chapter": None,
        "parent_citation": ascii_safe(parent_citation) if parent_citation else None,
        "revision": revision,
        "body_md": ascii_safe(body_md),
        "source_url": source_url,
    }
    nested = li.find("ul", recursive=False)
    if nested is not None:
        for child in nested.find_all("li", recursive=False):
            yield from _walk_index_li(child, full_path, citation, source_url, revision)


def parse_index_file(html: str, source_url: str) -> Iterable[dict]:
    """Parse a Subject Matter Index letter page (mpep-index-X.html).

    The index is a nested <ul>/<li> tree; each <li> is one entry with a term,
    optional MPEP section refs, optional 'See also' cross-references, and
    optional nested sub-entries. Emits one index_entry record per <li>, with
    parent_citation set from the nesting so the hierarchy is preserved.
    """
    soup = BeautifulSoup(html, "lxml")
    index_lis = [li for li in soup.find_all("li")
                 if (li.get("id") or "").startswith("mpep-index--")]
    for li in index_lis:
        if li.find_parent("li") is not None:
            continue  # nested under another entry; emitted via its ancestor's recursion
        yield from _walk_index_li(li, [], None, source_url, None)


# --- Helpers ----------------------------------------------------------------


def _mpep_parent(num: str) -> str | None:
    """Compute parent citation number from MPEP section number.

    2141        -> None (top-level)
    2141.01     -> 2141
    2141.01(a)  -> 2141.01
    """
    import re
    if "(" in num:
        return num.split("(")[0].rstrip(".")
    if "." in num:
        return num.rsplit(".", 1)[0]
    return None


def _chapter_from_url(url: str) -> str | None:
    """Extract chapter NNNN from an sNNNN.html URL."""
    import re
    m = re.search(r"/s(\d{2,4})\b", url)
    if m:
        # Round down to chapter granularity (e.g., 2141 -> 2100)
        n = m.group(1)
        chapter_num = int(n[:2] + "00")
        return str(chapter_num)
    return None


# --- YAML frontmatter emit --------------------------------------------------


def _yaml_frontmatter(record: dict) -> str:
    parts = ["---"]
    for key in ("citation", "citation_normalized", "title", "kind",
                "chapter", "parent_citation", "revision", "source_url"):
        val = record.get(key)
        if val is None:
            parts.append(f"{key}: null")
        else:
            # Quote strings to be safe with special chars
            s = str(val).replace('"', '\\"')
            parts.append(f'{key}: "{s}"')
    parts.append("---")
    return "\n".join(parts) + "\n"


# --- Main -------------------------------------------------------------------


def _dedup_records(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Deduplicate by citation_normalized using longer-body-wins.

    Returns (kept, dropped). The dropped list is logged so duplicates can
    be reviewed manually.
    """
    by_slug: dict[str, dict] = {}
    dropped: list[dict] = []
    for r in records:
        slug = r["citation_normalized"]
        existing = by_slug.get(slug)
        if existing is None:
            by_slug[slug] = r
            continue
        # Pick the one with the longer body
        if len(r["body_md"]) > len(existing["body_md"]):
            dropped.append({
                "slug": slug, "citation": existing["citation"],
                "kept_len": len(r["body_md"]),
                "dropped_len": len(existing["body_md"]),
            })
            by_slug[slug] = r
        else:
            dropped.append({
                "slug": slug, "citation": r["citation"],
                "kept_len": len(existing["body_md"]),
                "dropped_len": len(r["body_md"]),
            })
    return list(by_slug.values()), dropped


def _discover_inputs() -> list[tuple[str, callable, str, pathlib.Path]]:
    """Build the parse plan from build/raw_html/ contents.

    Returns list of (rel_input_path, parser_fn, source_url, output_dir).
    """
    sections_out = REPO_ROOT / "intermediate" / "sections"
    appendices_out = REPO_ROOT / "intermediate" / "appendices"
    index_out = REPO_ROOT / "intermediate" / "index"
    sections_out.mkdir(parents=True, exist_ok=True)
    appendices_out.mkdir(parents=True, exist_ok=True)
    index_out.mkdir(parents=True, exist_ok=True)

    plan: list = []
    # All MPEP section files
    section_dir = REPO_ROOT / "build" / "raw_html" / "sections"
    if section_dir.exists():
        for f in sorted(section_dir.glob("s*.html")):
            rel = str(f.relative_to(REPO_ROOT))
            plan.append((rel, parse_mpep_section_file,
                         USPTO_BASE + f.name, sections_out))
    # Appendix L (35 USC), R (37 CFR), and Form Paragraphs
    appx_dir = REPO_ROOT / "build" / "raw_html" / "appendices"
    if appx_dir.exists():
        for f in sorted(appx_dir.glob("*.html")):
            rel = str(f.relative_to(REPO_ROOT))
            name = f.name
            if name.startswith("mpep-9015-appx-l"):
                plan.append((rel, parse_appendix_l_file, USPTO_BASE + name, appendices_out))
            elif name.startswith("mpep-9020-appx-r"):
                plan.append((rel, parse_appendix_r_file, USPTO_BASE + name, appendices_out))
            elif "Form-Paragraph" in name or name.startswith("mpep-9095"):
                plan.append((rel, parse_form_paragraphs_file, USPTO_BASE + name, appendices_out))
            # Other appendices (II, T, AI, P) are not yet parsed; skip silently.
    # Subject Matter Index letter pages (mpep-index-a.html .. mpep-index-z.html)
    index_dir = REPO_ROOT / "build" / "raw_html" / "index"
    if index_dir.exists():
        for f in sorted(index_dir.glob("mpep-index-*.html")):
            rel = str(f.relative_to(REPO_ROOT))
            plan.append((rel, parse_index_file, USPTO_BASE + f.name, index_out))
    return plan


def main() -> int:
    plan = _discover_inputs()
    if not plan:
        print("No raw HTML found under build/raw_html/", file=sys.stderr)
        return 1

    counts: dict[str, int] = {}
    all_dropped: list[dict] = []
    failures = []
    for rel, parser, src_url, out_dir in plan:
        path = REPO_ROOT / rel
        if not path.exists():
            print(f"  MISSING: {path}")
            failures.append(rel)
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        records = list(parser(html, src_url))
        kept, dropped = _dedup_records(records)
        for record in kept:
            slug = record["citation_normalized"]
            md_path = out_dir / f"{slug}.md"
            content = _yaml_frontmatter(record) + "\n" + record["body_md"]
            md_path.write_text(content, encoding="ascii", errors="replace")
        all_dropped.extend(dropped)
        counts[rel] = len(kept)
        if dropped:
            print(f"  {rel}: {len(kept)} kept, {len(dropped)} dropped (dup)")
        else:
            print(f"  {rel}: {len(kept)} records")
    if failures:
        print(f"\nFAILED: {failures}", file=sys.stderr)
        return 1
    total = sum(counts.values())
    print(f"\nTotal: {total} records, {len(all_dropped)} dropped duplicates")
    summary_path = REPO_ROOT / "intermediate" / "_summary.json"
    summary_path.write_text(json.dumps(counts, indent=2))
    if all_dropped:
        dup_path = REPO_ROOT / "intermediate" / "_duplicates.json"
        dup_path.write_text(json.dumps(all_dropped, indent=2))
        print(f"  duplicates logged to {dup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
