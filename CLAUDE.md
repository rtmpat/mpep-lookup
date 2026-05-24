# Project: mpep-lookup

A Claude skill that retrieves verbatim text from the USPTO MPEP, 35 USC
patent statutes (Appendix L), 37 CFR patent rules (Appendix R), and Form
Paragraphs. Local SQLite + FTS5, no embeddings, no LLM in the retrieval loop.

## Tech Stack
- Python 3.12+ (runtime scripts are stdlib only; `sqlite3` with FTS5)
- Build deps: `requests`, `beautifulsoup4`, `lxml` (see `requirements.txt`)
- Dev deps: `pytest` (see `requirements-dev.txt`)
- Distribution: a packaged `mpep-lookup.skill` zip (published via GitHub
  Releases, tagged by MPEP revision; not tracked in git)

## Commands
- Test: `pytest` (from repo root; e2e tests skip if `skill/data/mpep.db` is absent)
- Build DB from scratch (needs network to USPTO, ~5 min):
  ```sh
  python build/01_fetch_corpus.py      # fetch USPTO HTML
  python build/02_parse_to_markdown.py # HTML -> markdown records
  python build/03_build_database.py    # -> skill/data/mpep.db
  python build/04_verify_corpus.py     # spot retrieval + search checks
  python build/05_package_skill.py     # -> mpep-lookup.skill
  ```
- Lint: none configured

## Architecture
Offline build pipeline scrapes USPTO HTML, parses to ASCII-safe markdown,
and loads a single SQLite DB with an FTS5 mirror. The runtime skill
(`skill/SKILL.md` + `scripts/lookup.py` + `scripts/search.py` +
`scripts/index_lookup.py`) answers citation lookups, keyword searches, and
Subject Matter Index topic routing against the bundled DB.
`skill/scripts/_common.py` is the single source of truth for citation
parsing, shared by both build and runtime.

See @ARCHITECTURE.md for full details.

## Folder Structure
- `build/` - offline pipeline scripts (fetch, parse, build DB, verify, package)
- `skill/` - the shippable skill: `SKILL.md`, `scripts/`, `references/`
  (`data/mpep.db` is regenerable and gitignored)
- `tests/` - pytest suite (citation parser, parsers, e2e smoke tests)
- `docs/` - `html_structure.md`, `decisions/`, and `dev-history/`
- `transfers/` - cross-repo transfer docs (gitignored)

## THE LOAD-BEARING INVARIANT: verbatim discipline
This is the entire product. The skill returns verbatim quotations with pin
cites; all LLM-generated content (summaries, analysis) is explicitly
labeled to distinguish it from quoted authority. `SKILL.md`'s
**Verification Pass** instructs Claude to check every quoted passage
against the SQL record before responding. The "retrieve before you cite"
rule is a pre-composition requirement, not a post-hoc backstop. Do NOT let
any refactor weaken this. No embeddings / no vector search has been
considered and deliberately rejected (paraphrasing-disguised-as-retrieval
risk). See @docs/dev-history/LESSONS.md for why.

## Project Knowledge
- @ARCHITECTURE.md - system design and data flow
- @docs/dev-history/LESSONS.md - non-obvious things discovered during build
- @docs/dev-history/TODOS.md - deferred roadmap items
- @docs/decisions/ - records for non-obvious choices

## Important Notes
- The DB is regenerable, not source-of-truth. Rebuild per the commands above.
- Source is USPTO HTML; no XML/JSON exists. The parser handles USPTO-specific
  quirks (U+2011 non-breaking hyphens in pre-AIA markers; subsections living
  inside parent files as anchor fragments; variant markers like `(pre-AIA)`,
  `(pre-PLT)`, date ranges as distinct records). All documented in LESSONS.md.
- AIA and pre-AIA are both indexed; practitioners with mixed dockets need both.
- Locked to one MPEP revision per build (`metadata.source_revision`).
- ASCII-only output (section symbol -> "Section ", pilcrow -> "Paragraph ", etc.).
- The citation parser is hot: 46 of the 67 tests cover it. Run `pytest` after
  any change to `_common.py`.
