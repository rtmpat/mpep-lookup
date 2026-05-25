"""Verify supersession redlines against the source PDF's strike/underline marks.

WHY THIS EXISTS: redline text extracted from a PDF's text layer (pdftotext) or
read off a rendered page is strikethrough-BLIND - struck (deleted) words appear
as ordinary text. So a hand-built "revised text" silently retains deleted words,
and neither the accept-all==revised check nor a reject-all==original check can
catch it (the word is legitimately present in the original). The only authority
on what was deleted is the PDF's graphics layer.

This verifier reads that graphics layer with pdfplumber: a thin horizontal rule
through the vertical MIDDLE of a word is a strikethrough (deletion); one at the
BASELINE is an underline (insertion). It then FAILS LOUD if any struck word in
the memo PDF is not covered by a [[del:...]] marker in the transcribed redline
(a missed deletion - the bug that left "is," in MPEP 2106.04(d)(1)).

Insertions are cross-checked best-effort (underlines also cover whole "added"
paragraphs and some memo formatting, so missed insertions are reported as
warnings, not failures). Deletions are the load-bearing check.

Usage: python build/verify_redlines.py            # all section_anc memos
       python build/verify_redlines.py desjardins  # one memo
Requires pdfplumber (requirements-dev.txt). ASCII-only source.
"""

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "build"))
from supersessions_loader import load_manifest, SUPERSESSIONS_DIR  # noqa: E402


def _tokens(text: str) -> set[str]:
    """Content tokens: lowercase alphanumeric runs (case/punctuation-insensitive)."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def extract_marks(pdf_path: pathlib.Path) -> tuple[set[str], set[str]]:
    """Return (struck_tokens, underlined_tokens) from a PDF's graphics layer."""
    import pdfplumber

    struck: set[str] = set()
    underlined: set[str] = set()
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            bars = [g for g in list(page.lines) + list(page.rects)
                    if abs(g["top"] - g["bottom"]) < 2.5 and (g["x1"] - g["x0"]) > 2]
            for w in page.extract_words():
                h = w["bottom"] - w["top"]
                width = w["x1"] - w["x0"]
                for g in bars:
                    gy = (g["top"] + g["bottom"]) / 2
                    xov = min(g["x1"], w["x1"]) - max(g["x0"], w["x0"])
                    if xov < 0.5 * width:  # rule must span over half the word
                        continue
                    if w["top"] + 0.25 * h < gy < w["bottom"] - 0.2 * h:
                        struck |= _tokens(w["text"]); break
                    if w["bottom"] - 0.2 * h <= gy <= w["bottom"] + 0.3 * h:
                        underlined |= _tokens(w["text"]); break
    return struck, underlined


def redline_tokens(memo_dir: pathlib.Path) -> tuple[set[str], set[str]]:
    """(del_tokens, ins_tokens) from a memo's provision files.

    [[del:]]/[[ins:]] markers contribute their tokens; a whole 'added'-status
    provision's revised text is treated as inserted (it is one big insertion
    with no redline of its own)."""
    del_t: set[str] = set()
    ins_t: set[str] = set()
    for path in sorted(memo_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm = text.split("---", 2)
        status = ""
        if len(fm) >= 3:
            m = re.search(r"^status:\s*\"?(\w+)\"?", fm[1], re.MULTILINE)
            status = m.group(1) if m else ""
        redline = text.split("## Redline", 1)
        if len(redline) > 1:
            body = redline[1]
            for d in re.findall(r"\[\[del:(.*?)\]\]", body, re.DOTALL):
                del_t |= _tokens(d)
            for i in re.findall(r"\[\[ins:(.*?)\]\]", body, re.DOTALL):
                ins_t |= _tokens(i)
        elif status == "added":
            revised = text.split("## Revised text", 1)
            if len(revised) > 1:
                ins_t |= _tokens(revised[1].split("## ", 1)[0])
    return del_t, ins_t


def verify_memo(memo: dict, base: pathlib.Path) -> list[str]:
    """Return a list of FAILURES (missed deletions). Warnings print directly."""
    pdf_path = base / "pdf" / f"{memo['slug']}.pdf"
    struck, underlined = extract_marks(pdf_path)
    del_t, ins_t = redline_tokens(base / memo["source_dir"])

    failures = []
    missed_del = struck - del_t
    if missed_del:
        failures.append(
            f"{memo['slug']}: {sorted(missed_del)} struck in the PDF but not marked "
            "[[del:]] in the redline (missed deletion).")
    missed_ins = underlined - ins_t
    if missed_ins:
        print(f"  WARNING {memo['slug']}: underlined-but-not-[[ins:]] (review): "
              f"{sorted(missed_ins)}", file=sys.stderr)
    print(f"  {memo['slug']}: struck={sorted(struck)} -> "
          f"{'OK' if not missed_del else 'MISSED ' + str(sorted(missed_del))}")
    return failures


def main() -> int:
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print("pdfplumber not installed (pip install -r requirements-dev.txt); "
              "cannot verify redlines against PDF marks.", file=sys.stderr)
        return 1
    base = SUPERSESSIONS_DIR
    manifest = load_manifest(base / "manifest.json")
    only = sys.argv[1] if len(sys.argv) > 1 else None
    failures: list[str] = []
    print("=== Redline verification (PDF strike/underline vs transcription) ===")
    for memo in manifest["memos"]:
        if memo["class"] != "section_anc":
            continue
        if only and memo["slug"] != only:
            continue
        failures.extend(verify_memo(memo, base))
    print("=" * 60)
    if failures:
        print(f"FAILED: {len(failures)} redline discrepancy(ies):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("ALL REDLINE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
