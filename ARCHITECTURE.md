# Architecture

## Overview

A Claude skill that retrieves verbatim text from the MPEP, 35 USC patent
statutes (Appendix L), 37 CFR patent rules (Appendix R), and Form
Paragraphs, plus the Subject Matter Index as a topic -> section routing
layer. Local SQLite + FTS5; no embeddings, no LLM in the retrieval loop.
Verbatim discipline enforced by SKILL.md's Verification Pass.

## Components

### Build pipeline (offline, ~25-60 min per MPEP revision)

1. `build/fetch_slice.py` (or `01_fetch_corpus.py` for full crawl) -
   downloads HTML pages from USPTO with rate-limited parallel workers.
2. `build/02_parse_to_markdown.py` - converts HTML to ASCII-safe markdown
   with YAML frontmatter, splitting parent files into multiple records by
   `<h1 class="page-title">` boundary, and the Subject Matter Index letter
   pages into one `index_entry` record per nested `<li>` (hierarchy kept via
   `parent_citation`; refs stored as `MPEP <num>` tokens in the body).
3. `build/03_build_database.py` - loads markdown files into a single
   SQLite DB with FTS5 mirror, populates metadata.
4. `build/04_verify_corpus.py` - spot-retrieval and spot-search checks.
5. `build/05_package_skill.py` - produces `mpep-lookup.skill` zip.

### Runtime (the skill, online via Claude)

1. `skill/SKILL.md` - Claude-facing trigger doc with the load-bearing
   Verification Pass.
2. `skill/scripts/_common.py` - **single source of truth for citation
   parsing**. Owns `parse_citation`, `normalize`, `connect_db`,
   `format_record`, `ascii_safe`, whitespace helpers. Build phase imports
   from here via `sys.path` insertion.
3. `skill/scripts/lookup.py` - direct citation -> verbatim record.
4. `skill/scripts/search.py` - FTS5 query -> ranked snippets, Subject Matter
   Index-aware by default (boosts index-corroborated sections; tiered
   index+keyword > index > keyword-only; `--keyword-only` for plain FTS5).
5. `skill/scripts/index_lookup.py` - Subject Matter Index term -> the
   referenced sections, resolved to verbatim records (topic routing layer).
6. `skill/data/mpep.db` - bundled SQLite database (~27 MB at full corpus).

## Data Flow

```
USPTO HTML  ---> fetch ---> raw_html/    ---> parse ---> intermediate/*.md
                                                              |
                                                              v
                                                   build_database
                                                              |
                                                              v
                                                  skill/data/mpep.db
                                                              |
                                                              v
Claude        <--- verbatim ---  lookup.py / search.py        |
   |                                ^                          |
   |                                +-------- _common.py-------+
   |
   +---> Verification Pass ---> user response
```

## Data Model

Single `sections` table:

```
id (PK), citation, citation_normalized (UNIQUE), title, kind, chapter,
parent_citation, revision, body_md, source_url, word_count, inbound_refs
```

`inbound_refs` is the count of other records that cite this one (computed at
build time from a typed citation pass over every `body_md`). Search uses it as
a modest, log-scaled authority bump so heavily-cited rules/sections rank above
passing mentions. See `build/03_build_database.py:_compute_inbound_refs`.

`kind` is one of `mpep_section | statute | cfr_rule | form_paragraph |
index_entry | appendix` (CHECK constraint enforced; this is also the
schema-version boundary - adding a new kind requires migration).

`sections_fts` is an FTS5 virtual table mirroring (citation, title,
body_md) with porter+unicode61 tokenizer. Triggers keep it in sync on
INSERT/UPDATE/DELETE.

`metadata` table holds `source_revision`, `built_at`, `total_records`,
`record_counts_by_kind`, `builder_version`, `corpus_source`.

## External Dependencies

- USPTO website (one-time per MPEP revision rebuild).
- Python 3.12+ stdlib `sqlite3` (with FTS5).
- Build deps: `requests`, `beautifulsoup4`, `lxml`.
- Dev deps: `pytest`.
- Runtime deps: stdlib only.

## Security Considerations

Solo internal practice tool, local SQLite, public-domain corpus content.
No PII, no auth, no secrets. SQL queries are parameterized. FTS5 query
injection is bounded (read-only DB, single user, fail-loud on malformed
query). Prompt-injection from corpus content is negligible (USPTO text
is trusted government work product).

## Constraints and Tradeoffs

- **Verbatim discipline non-negotiable.** SQL provides authoritative
  source; SKILL.md's Verification Pass enforces no-paraphrasing. The
  architecture cannot prevent paraphrasing on its own; the verification
  pass is the load-bearing safeguard.
- **No embeddings / no vector search.** Adds dependency surface and
  creates paraphrasing-disguised-as-retrieval risk. FTS5 with kind
  filters is sufficient for keyword/phrase patent-text lookup.
- **One file, one place to edit citation parsing** (Option 1B). Build
  imports from `skill/scripts/_common.py` via sys.path. No copy step.
- **Polite-but-faster scraping.** 4 worker threads each at 1 req/s
  (~4 req/s aggregate) overrides the original spec's "1 req/s, no
  parallelism" with explicit ban detection (429/403/RST).
- **ASCII-only output.** Section symbol -> "Section "; pilcrow ->
  "Paragraph "; smart quotes -> straight; em-dash -> --; non-breaking
  hyphen -> -. FTS5 tokenizer treats Unicode punctuation as
  non-indexable anyway.
- **Locked to one MPEP revision per build.** `metadata.source_revision`
  records which revision; rebuild from scratch for each new revision.
