"""HTML structure inspection (development helper).

Documents what we find in each fetched HTML file so the parser can be
written from observed structure, not assumed structure. Key questions:

- Main content container (id/class)?
- Heading element with section number + title + revision indicator?
- Statute / CFR insets within MPEP sections?
- Cross-reference link format?
- For Appendix L: how do AIA / pre-AIA splits look?
- For Form Paragraphs: how is each FP delimited?

Run: python build/inspect_html.py
"""

import pathlib
import sys

from bs4 import BeautifulSoup

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

FILES = [
    ("MPEP section + subsections", "build/raw_html/sections/s2141.html"),
    ("Appendix L (35 USC, AIA + pre-AIA)", "build/raw_html/appendices/mpep-9015-appx-l.html"),
    ("Appendix R (37 CFR)", "build/raw_html/appendices/mpep-9020-appx-r.html"),
    ("Form Paragraphs Consolidated", "build/raw_html/appendices/mpep-9095-Form-Paragraph-Chapter.html"),
]


def inspect(label: str, rel_path: str) -> None:
    path = REPO_ROOT / rel_path
    print(f"\n{'=' * 72}\n{label}\n  {rel_path} ({path.stat().st_size} bytes)\n{'=' * 72}")
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    # 1. Main content container
    print("\n-- Top-level structure --")
    body = soup.body
    if body is None:
        print("  NO <body> tag")
        return
    direct_children = [c for c in body.find_all(recursive=False) if c.name]
    for c in direct_children[:10]:
        attrs = " ".join(f"{k}={v!r}" for k, v in (c.attrs or {}).items() if k in ("id", "class"))
        print(f"  <{c.name}> {attrs}")

    # 2. Find candidate main content container
    print("\n-- Candidate main content container --")
    for selector in ['div#mainContent', 'div.maincontent', 'main', 'div#content',
                     'div.region-content', 'div[role="main"]', 'article']:
        found = soup.select(selector)
        if found:
            print(f"  HIT: {selector!r} ({len(found)} match)")

    # 3. Headings (h1-h4) — first 30
    print("\n-- First 20 headings (h1-h6) with text --")
    for i, h in enumerate(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])[:20]):
        text = " ".join(h.get_text().split())[:120]
        attrs = " ".join(f"{k}={v!r}" for k, v in (h.attrs or {}).items() if k in ("id", "class"))
        print(f"  <{h.name}> {attrs} :: {text}")

    # 4. Anchored ids that look like section anchors (d0e\d+)
    print("\n-- Sample anchored ids matching d0e\\d+ pattern (first 10) --")
    anchors = soup.find_all(id=lambda x: x and x.startswith("d0e"))
    print(f"  total: {len(anchors)} anchors")
    for a in anchors[:10]:
        text = " ".join(a.get_text().split())[:80]
        print(f"  id={a['id']:<20} <{a.name}> :: {text}")

    # 5. Look for AIA / pre-AIA markers (only matters for Appendix L)
    if "appx-l" in rel_path:
        print("\n-- AIA / pre-AIA markers --")
        text = soup.get_text()
        for marker in ["pre-AIA", "Pre-AIA", "(pre-AIA)", "AIA 35 U.S.C.", "[Editor Note"]:
            count = text.count(marker)
            print(f"  {marker!r:<25} count: {count}")

    # 6. Look for Form Paragraph markers
    if "Form-Paragraph" in rel_path:
        print("\n-- Form Paragraph patterns --")
        # Form paragraphs typically formatted like: ¶ 7.05 ... or "Form Paragraph 7.05"
        text = soup.get_text()
        import re
        fp_refs = re.findall(r"\b(?:¶|Form Paragraph)\s*\d+\.\d+(?:\.\w+)?", text)
        print(f"  total paragraph references found: {len(fp_refs)}")
        print(f"  unique: {len(set(fp_refs))}")
        print(f"  sample: {sorted(set(fp_refs))[:10]}")

    # 7. Statute / CFR insets within MPEP section
    if "/sections/" in rel_path:
        print("\n-- Statute / CFR insets --")
        # Insets often use specific class or are in <h4> or styled boxes
        for sel in ['div.statute', 'div.section', 'div.law', 'h4', 'div[class*="stat"]',
                   'div[class*="rule"]']:
            found = soup.select(sel)
            if found:
                print(f"  selector {sel!r}: {len(found)} elements; first text: "
                      f"{' '.join(found[0].get_text().split())[:100]}")
        # Find any heading containing "U.S.C." or "C.F.R."
        usc_headings = [h for h in soup.find_all(["h1", "h2", "h3", "h4", "h5"])
                        if "U.S.C." in h.get_text() or "C.F.R." in h.get_text()]
        print(f"  headings mentioning U.S.C. or C.F.R.: {len(usc_headings)}")
        for h in usc_headings[:3]:
            print(f"    <{h.name}> :: {' '.join(h.get_text().split())[:100]}")


def main() -> int:
    for label, rel_path in FILES:
        try:
            inspect(label, rel_path)
        except Exception as exc:
            print(f"\n!! ERROR inspecting {rel_path}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
