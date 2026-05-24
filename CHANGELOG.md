# Changelog

All notable changes to the MPEP Lookup skill are documented here. The project
follows semantic versioning (MAJOR.MINOR.PATCH).

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
