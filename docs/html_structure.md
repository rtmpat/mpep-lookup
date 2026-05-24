# USPTO MPEP HTML Structure Findings

Established by `build/inspect_html.py` against the 4 vertical-slice fetches
on 2026-04-26. The parser is written from these observations, not assumed
structure.

## Universal pattern

**Every record across all 4 file types is delimited by `<h1 class="page-title">`.**
That is the universal record boundary. The body of a record is everything
between one `page-title` h1 and the next.

No file has a clean main-content container (`<main>`, `<article>`, etc.).
USPTO uses `<div id="custom-doc">` plus `extra-div-N` siblings for
chrome. The parser scans the entire document for `h1.page-title` and
ignores everything else.

## File-type specifics

### MPEP sections (`sNNNN.html`)

- Top-level section heading: `<h1 class="page-title">` (no id).
  Format: `NNNN Title [R-XX.YYYY]`
- Subsections: `<h1 class="page-title" id="d0eXXXXXX">` (with id).
  Format: `NNNN.NN Title [R-XX.YYYY]` or `NNNN.NN(letter) Title [R-XX.YYYY]`
- **Subsections live INSIDE the parent file**, NOT as separate URLs.
  The original spec was wrong about `sNNNN.NN(x).html` URLs existing —
  they 404. Anchored sections within `sNNNN.html` are the truth.
- Statute insets: `<h4 class="USC">35 U.S.C. NNN Title.</h4>` and
  `<h4 class="USC">Pre-AIA 35 U.S.C. NNN Title.</h4>` — preserved verbatim
  in body markdown. Both AIA and pre-AIA versions appear together within
  one MPEP section.

Example heading texts seen in `s2141.html`:
- `2141 Examination Guidelines for Determining Obviousness Under 35 U.S.C. 103 [R-01.2024]`
- `2141.01 Scope and Content of the Prior Art [R-01.2024]`
- `2141.01(a) Analogous and Nonanalogous Art [R-01.2024]`
- `2141.02 Differences Between Prior Art and Claimed Invention [R-01.2024]`

### Appendix L (35 USC)

- Each statute: `<h1 class="page-title">` (no id on most; some have ids).
  Format: `35 U.S.C. NUMBER Title.`
- Pre-AIA versions: `35 U.S.C. NUMBER (pre‑AIA) Title.` — **the dash in
  "pre‑AIA" is U+2011 NON-BREAKING HYPHEN, not ASCII hyphen-minus.**
  The ASCII string "pre-AIA" does NOT appear in this file.
  The parser must match both `pre‑AIA` and `pre-AIA` (defensive)
  and normalize either to a canonical "(pre-AIA)" suffix in citation.
- Editor notes: 77 occurrences of `[Editor Note:` — these are inline
  annotations within statute bodies, preserved as italic blockquote.
- AIA-version sections that need explicit AIA tagging exist (5
  occurrences of `AIA 35 U.S.C.` in headings, e.g., when a statute has
  both versions and one is labeled AIA explicitly).

Example heading texts:
- `35 U.S.C. 1 Establishment.`
- `35 U.S.C. 6 Patent Trial and Appeal Board.` (current = AIA)
- `35 U.S.C. 6 (pre‑AIA) Board of Patent Appeals and Interferences.` (older)

### Appendix R (37 CFR)

- Each rule: `<h1 class="page-title">`. Format: `RULE_NUM Title.`
  e.g., `1.131 Affidavit or declaration of prior invention or derivation.`
- Subpart structure: `<h1 class="SubPartLevel1">Subpart A - General Provisions`
- Section dividers: `<h1 class="SubPartLevel2">GENERAL INFORMATION AND
  CORRESPONDENCE`
- The parser tracks Part context (currently always Part 1 for patents)
  via the `PART PART N - ...` headings. For mpep-lookup v1, all rules in
  Appendix R are in 37 CFR Part 1; record citation as `37 CFR 1.131`.
  Subpart and section dividers are organizational only — they are not
  emitted as records, just as parent-citation context if useful.

### Form Paragraphs (`mpep-9095-Form-Paragraph-Chapter.html`)

- Each FP: `<h1 class="page-title">`. Format: `NNN.NN[.NN] Title`
  e.g., `2.01 Possible Status as Divisional`
- After each FP heading: zero or more `<h3>Examiner Note:</h3>` blocks
  with associated text. Preserve as part of the FP record body.
- **No `d0e\d+` anchored ids in this file** (different generation
  pipeline upstream at USPTO; doesn't matter for our parser since we
  delimit by h1 boundaries).
- Citation format: `Form Paragraph 2.01` (at runtime). In the heading,
  just the number `2.01` appears.

## Key decisions for the parser

1. **Scan for `h1.page-title`, split into records by boundary.** Universal
   across all 4 file types.
2. **Heading parsing is kind-specific** — different regex patterns for
   `mpep_section` / `statute` / `cfr_rule` / `form_paragraph`. The build
   script tells the parser which kind it's processing based on the file
   it's reading.
3. **Filter chrome.** A `<h1 class="page-title">` with empty text or text
   that is just whitespace is skipped (some files have placeholder
   headings at end-of-document).
4. **Pre-AIA detection: match `pre[‑-]AIA`** (Unicode-aware regex).
   Normalize to canonical suffix `_pre_aia` in `citation_normalized`.
5. **Editor notes preserved verbatim** in body — they ARE the legal
   commentary that distinguishes AIA from pre-AIA application contexts.
6. **For statutes that exist as both AIA and pre-AIA, emit two records**
   with citations `35 USC NNN` (AIA, default) and `35 USC NNN (pre-AIA)`.
   Detection: the heading text contains `(pre‑AIA)` or `(pre-AIA)`.

## Filter rules (drop these from body markdown)

- `<a>` tags converted to plain text (no link targets in corpus per spec)
- Navigation icons / pencil "Add a Note" graphics
- Empty headings
- The entire `<head>` element and any USPTO chrome divs (`extra-div-*`)

## ASCII-safety transform

After markdown conversion, replace:
- Smart quotes `"" '' ` → `"`, `'`
- Em-dash `—` → `--`
- En-dash `–` → `-`
- Ellipsis `…` → `...`
- Non-breaking space ` ` → space
- **Non-breaking hyphen `‑`** → `-` (only after pre-AIA detection
  has already happened; do not lose information)
- Section symbol `§` → kept as-is (FTS5 tokenizer treats as punctuation)
