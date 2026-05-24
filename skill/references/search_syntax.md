# Search Syntax

`search.py` runs SQLite FTS5 queries over the MPEP corpus. The full FTS5
syntax reference is at https://www.sqlite.org/fts5.html#full_text_query_syntax.
This document covers the patterns most useful for patent practitioners.

## Phrase search (most common)

Wrap the phrase in double quotes:

```
search.py '"reasonable expectation of success"'
```

The shell single-quotes prevent the outer double quotes from being
stripped. Without quotes, words are searched separately (implicit AND).

## Boolean operators

```
obviousness AND prima facie
102 OR 103
analogous NOT secondary
```

`AND` is implicit when terms are space-separated. Use `OR` for either
match. `NOT` excludes the right-hand term.

## Proximity (NEAR)

Find terms within N tokens of each other:

```
analogous NEAR/10 art       # within 10 tokens
KSR NEAR/5 Teleflex         # within 5 tokens
```

## Prefix wildcards

Match any word starting with a prefix:

```
obvious*       # obvious, obviousness, obviously, ...
patent*        # patent, patented, patenting, ...
```

Suffix wildcards (`*ness`) are NOT supported by FTS5.

## Column-specific search

Search only specific columns of the FTS5 index:

```
title:novelty                    # match only in title field
citation:"MPEP 2141"             # match only in citation field
body_md:obvious*                 # match only in body
```

## Combining operators

```
title:obviousness AND body_md:KSR
"reasonable expectation" NEAR/30 success
(obvious OR non-obvious) AND prima facie
```

## Filter by kind / chapter

Use the `--kind` and `--chapter` flags rather than FTS5 syntax for
metadata filters (kind and chapter are UNINDEXED columns in FTS5):

```
search.py --kind statute "novelty"            # only 35 USC records
search.py --kind cfr_rule "affidavit"         # only 37 CFR records
search.py --kind form_paragraph "rejection"   # only examiner templates
search.py --kind mpep_section "obviousness"   # only MPEP sections
search.py --chapter 2100 "claim interpretation"
```

## Why kind filters matter

The corpus is dominated by Form Paragraphs (~775) and 37 CFR rules
(~800), with fewer statutes (~210) and MPEP sections (~900). BM25
ranking favors short documents, which means short Form Paragraph titles
("Lacks Novelty") can outrank canonical authority (35 USC 102
"Conditions for patentability; novelty") on common terms.

For "find the canonical authority on X" queries, ALWAYS pass
`--kind statute` or `--kind mpep_section`. Without filter, the practitioner
gets a broad view of all records mentioning the term, which is useful
but not what's usually wanted.

## Common patent-prosecution searches

| Goal | Command |
|---|---|
| Statute on novelty | `search.py "novelty" --kind statute` |
| MPEP guidance on obviousness | `search.py "obviousness" --kind mpep_section` |
| Rule for swearing behind | `search.py "prior invention" --kind cfr_rule` |
| Examiner FP for rejection | `search.py "rejection" --kind form_paragraph` |
| Cases citing KSR | `search.py "KSR Teleflex" --kind mpep_section` |
| Reasonable expectation cases | `search.py '"reasonable expectation of success"'` |
| Alice/Mayo eligibility | `search.py "Alice Mayo" --kind mpep_section` |
| Restriction practice | `search.py "restriction" --chapter 800` |
| Section-specific search | `search.py 'citation:"MPEP 2141.01" obvious'` |

## Quoting in shell

When running from a shell, FTS5 phrase quotes (`"..."`) need to survive
the shell. Three reliable ways:

```sh
search.py '"reasonable expectation"'        # single-quote outside
search.py "\"reasonable expectation\""      # escape inside double-quotes
search.py "'reasonable expectation'"        # FTS5 also accepts single quotes for phrases
```

## Result format

Results are ranked by BM25 (negative numbers, more negative = better
match). The `snippet()` function produces a 20-word window around the
match, with hits wrapped in `<b>...</b>`. Use `--snippet-words N` to
adjust the window size.

JSON mode (`--json`) returns:

```json
{
  "hits": [
    {
      "rowid": 123,
      "citation": "MPEP 2143.02",
      "title": "Reasonable Expectation of Success Is Needed",
      "kind": "mpep_section",
      "chapter": "2100",
      "snippet": "...there must be a <b>reasonable expectation</b> of <b>success</b>...",
      "rank": -11.42
    }
  ],
  "fts_query": "\"reasonable expectation of success\"",
  "fts_query_parsed_ok": true,
  "total_records_searched": 2900,
  "filters": {"kind": null, "chapter": null}
}
```

The `fts_query_parsed_ok: true` field is the load-bearing signal that
the query ran cleanly even if `hits` is empty. If you see `hits: []` AND
`fts_query_parsed_ok: true`, the corpus genuinely has no matches - do
not fall back to training-data knowledge.
