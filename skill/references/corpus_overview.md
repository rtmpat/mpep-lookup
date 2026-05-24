# Corpus Overview

Documents what's in `data/mpep.db`. Numbers are written by
`03_build_database.py` into the `metadata` table; this file is a static
description of the structure and source.

## Source

USPTO Manual of Patent Examining Procedure, retrieved from
https://www.uspto.gov/web/offices/pac/mpep/. The current revision is
recorded in `metadata.source_revision` and was the latest available at
build time.

The `metadata` table also records:
- `built_at` (ISO 8601 UTC)
- `total_records`
- `record_counts_by_kind` (JSON)
- `builder_version`
- `corpus_source`

Query metadata directly:

```sql
SELECT key, value FROM metadata;
```

## Record kinds

| kind | source | example citations |
|---|---|---|
| `mpep_section` | MPEP chapter HTML files (`sNNNN.html`) split by h1.page-title | MPEP 100, MPEP 2141, MPEP 2141.01(a) |
| `statute` | MPEP Appendix L (`mpep-9015-appx-l.html`) | 35 USC 101, 35 USC 102, 35 USC 102 (pre-AIA) |
| `cfr_rule` | MPEP Appendix R (`mpep-9020-appx-r.html`) | 37 CFR 1.131, 37 CFR 1.132 |
| `form_paragraph` | Form Paragraphs Consolidated chapter | Form Paragraph 7.05 |
| `index_entry` | Subject Matter Index letter pages (`mpep-index-a.html` .. `-z.html`) | Index: Abandoned application > Revival |
| `appendix` | (other appendices, not parsed in v1) | - |

Index entries are a topic -> section routing layer: each carries the
referenced MPEP sections in its body as `MPEP <num>` tokens. `search.py` is
index-aware by default - it boosts the sections the index corroborates
(tiered `index+keyword` > `index` > `keyword`-only) but never returns index
entries as results. Use `index_lookup.py` for the curated index mapping
returned verbatim, `search.py --keyword-only` for a plain FTS5 search, or
`--include-index` / `--kind index_entry` to query index entries directly.

The `kind` discriminator is the schema-version boundary - adding a new
kind requires updating the CHECK constraint via migration.

## What's NOT in the corpus

- Federal Circuit case law (cases referenced by MPEP are NOT included as
  separate records; only the MPEP/USC/CFR text that mentions them is).
- SCOTUS patent cases (Mayo, Alice, KSR, etc.) - referenced but not stored.
- AIA Trial Practice Guide - separate USPTO publication.
- PCT articles and ISA guidelines - separate.
- USPTO Director's memos and OG notices - separate.
- Other appendices (II List of Decisions, T PCT, AI AIA, P Paris
  Convention) - planned but not in v1. (MPEP Appendix I is [Reserved] /
  empty at USPTO; the Subject Matter Index IS included, as `index_entry`
  records - see above.)

For these, recommend the live USPTO site to the user.

## MPEP subsection structure

MPEP sections like 2141 have anchored subsections (2141.01, 2141.01(a),
2141.02, 2141.03) that live INSIDE the parent file `s2141.html`. The
parser splits the parent file into multiple records by `<h1
class="page-title">` boundary. Each subsection is its own record with
`parent_citation` pointing up the hierarchy.

So:
- `MPEP 2141` (top-level, parent_citation = NULL)
- `MPEP 2141.01` (parent = MPEP 2141)
- `MPEP 2141.01(a)` (parent = MPEP 2141.01)

## Pre-AIA / AIA statute pairs

Many 35 USC sections have both an AIA version and a pre-AIA version.
Citations like `35 USC 102` resolve to the AIA version by default;
`35 USC 102 (pre-AIA)` returns the pre-AIA version. They're stored as
separate records with separate `citation_normalized` keys
(`35_usc_102` vs `35_usc_102_pre_aia`).

## Known limitations

See `LESSONS.md` in the repo root for the full list. Highlights:
- ~62 duplicate citations dropped during build (TOC-style headings in
  Appendix R + AIA-tagging ambiguity in Appendix L). The longer-body
  version wins; dropped duplicates are logged to
  `intermediate/_duplicates.json`.
- BM25 favors short titles and short documents. The default search corrects
  for this: Form Paragraphs are ranked last, and a modest inbound-reference
  bump ("how often is this cited") lifts well-cited authority (e.g. 37 CFR
  1.131, 35 USC 102) above passing mentions. Use `--kind` to scope, or
  `--keyword-only` for raw BM25.
- Cross-reference resolution is manual: when a record cites another
  section, run a separate `lookup.py` to retrieve the cited text.
