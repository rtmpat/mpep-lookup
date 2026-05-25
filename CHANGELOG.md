# Changelog

All notable changes to the MPEP Lookup skill are documented here. The project
follows semantic versioning (MAJOR.MINOR.PATCH).

## v1.1.0

Incorporates the USPTO advance-notice memoranda that supersede part of the
Ninth Edition, Revision 01.2024 before the next revision, and fixes two corpus
coverage gaps found while building it.

### Supersessions
- New sidecar `supersessions` table (with an FTS mirror) overlaying R-01.2024
  with five post-revision USPTO memos (through 2025-12-05): *Ex Parte Desjardins*
  (35 U.S.C. 101), PE2E Similarity Search recordation, false entity-status
  review, the *LKQ* design form-paragraph changes, and the FY2025 fee-rule form
  paragraphs — 65 affected provisions in total.
- Section-change memos store verbatim revised text plus a redline; form-paragraph
  memos are notice-only (the revised FP body lives in PE2E-OC and is never
  fabricated here).
- `lookup.py` auto-appends a `SUPERSEDED` block on affected provisions;
  `search.py` tags affected results; new `scripts/supersessions.py` searches
  supersessions directly (`--list` / `"<citation>"` / `--search` / `--memo`).
- Source memos are committed under `build/supersessions/` with a manifest; the
  build fails loud if the live USPTO supersede list, the manifest, the source
  files, and the loaded records diverge. Source PDFs are bundled smallest-first
  under a 3 MiB cap and always linked.
- `build/verify_redlines.py` verifies each redline against the source PDF's
  strike/underline marks (via pdfplumber) so deleted words cannot silently leak
  into revised text.

### Corpus coverage fixes
- Sub-subsection citations like MPEP 2106.04(d)(1) (letter + number) were
  silently dropped by the parser; they now parse and are retrievable (+49
  records). The build fails loud on any unparsed section-shaped heading.
- Inline form-paragraph examples inside section files were sliced off and
  dropped; they are now folded into their parent section, recovering 81
  section-only form paragraphs and previously-truncated section tails.

### Metadata
- `source_revision` stays R-01.2024; adds `supersessions_through`,
  `supersession_count`, and the bundled / link-only PDF lists. `builder_version`
  is 1.1.0.

## v1.0.0

Initial release. MPEP Lookup is a Claude skill for verbatim, citation-precise
retrieval from the USPTO patent-examination authorities.

### Coverage
- Verbatim text of the Manual of Patent Examining Procedure (MPEP) sections
  and subsections, 35 U.S.C. patent statutes (MPEP Appendix L), 37 C.F.R.
  patent rules (MPEP Appendix R), and USPTO Form Paragraphs.
- AIA and pre-AIA versions of statutes and rules are both indexed, for mixed
  dockets.
- The USPTO Subject Matter Index is indexed as a topic-to-section routing
  layer.
- Corpus: MPEP Ninth Edition, Revision 01.2024.

### Retrieval
- **Citation lookup** (`lookup.py`): resolve an exact citation to its verbatim
  record, tolerant of format variants (with or without the `MPEP` prefix,
  periods in `U.S.C.`/`C.F.R.`, the section symbol, `s.`/`Section`, etc.).
- **Full-text search** (`search.py`): keyword and phrase search over a local
  SQLite FTS5 index.
- **Subject Matter Index routing** (`index_lookup.py`): match a topic in the
  USPTO's curated index and resolve the sections it points to, returned
  verbatim.

### Ranking
- Search is Subject Matter Index-aware by default. Sections corroborated by
  both the index and the keyword query rank first, then index-referenced
  sections (interleaved with keyword-matching statutes and CFR rules), then
  keyword-only MPEP sections; Form Paragraphs rank last.
- A modest inbound-reference authority bump favors the records the rest of the
  MPEP cites most, so canonical authorities surface above passing mentions.

### Output discipline
- Verbatim quotations with pin cites. All model-generated content (summaries,
  IRAC analysis, application to facts) is explicitly labeled to distinguish it
  from quoted authority.
- Four output modes selected by question type, with a verification pass that
  checks every quotation against the source before the response is sent.

### Packaging
- Bundled SQLite + FTS5 database. No embeddings and no network access at
  runtime; the runtime scripts use only the Python standard library.
