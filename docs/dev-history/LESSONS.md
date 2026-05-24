# Lessons Learned

Non-obvious things discovered during build. Each entry: what went wrong (or
nearly went wrong), why, and how to avoid repeating it.

## 2026-04-26 - MPEP subsections live in parent files, not separate URLs

**Problem:** An early version of the parser was set up to fetch `s2141.01(a).html` for the
MPEP 2141.01(a) subsection record. All such URLs return 404.

**Failed approach:** Following the original spec's URL pattern
`sNNNN.NN(x).html`. The spec was wrong - that pattern doesn't exist on
USPTO's servers.

**Solution:** Subsections are anchored sections WITHIN the parent file.
`s2141.html` contains five `<h1 class="page-title">` elements, one for the
top-level (no id) and four for subsections (`id="d0eXXXXXX"`). The chapter
2100 index page links them as `s2141.html#d0eXXXXXX` fragments. The parser
splits the parent file into multiple records by h1 boundary.

**Why:** USPTO's MPEP HTML generation pipeline emits one HTML file per
top-level section, with all subsections inlined. There is no per-subsection
URL to fetch, only per-subsection anchor fragments.

## 2026-04-26 - Pre-AIA marker uses U+2011 NON-BREAKING HYPHEN, not ASCII

**Problem:** Initial regex matched `pre-AIA` (ASCII hyphen) in Appendix L
heading text. Got zero matches. The strings `'pre-AIA'`, `'(pre-AIA)'`, and
`'Pre-AIA'` all returned count 0 in the file.

**Failed approach:** Assuming `-` is `-` (U+002D). USPTO uses U+2011
NON-BREAKING HYPHEN in `pre‑AIA` to keep the term from line-breaking in
rendered HTML.

**Solution:** Regex matches `pre[‐‑‒–—―\-]AIA` covering U+002D, U+2010,
U+2011, U+2012, U+2013, U+2014, U+2015. `ascii_safe()` then normalizes all
of these to U+002D before storing/indexing.

**Why:** Whoever generated the MPEP HTML used a smart-typography pass that
substituted hyphens-in-compound-terms with non-breaking hyphens. Standard
practice for typeset legal text. Trips up naive regex.

## 2026-04-26 - BM25 favors short documents; corpus mix breaks "find the authority" intent

**Problem:** Searching for "novelty" without filters returns 6 Form
Paragraphs ahead of 35 USC 102 (the canonical statute on novelty).

**Failed approach:** Default BM25, then BM25 with title-column boost up to
25x. Form Paragraphs have 2-3 word titles; statutes have 5-7 word titles.
Even with heavy title boost, "Lacks Novelty" (FP 18.01) outranks
"Conditions for patentability; novelty" (35 USC 102) on raw term density.

**Solution:** Document the kind-filter pattern in SKILL.md and
`references/search_syntax.md`. Practitioners use `search.py "novelty"
--kind statute` to find the canonical authority. Without filter, results
are "all records mentioning novelty" which is also legitimate.

**Why:** Corpus is dominated 4:1 by Form Paragraphs (775) over statutes
(213) plus MPEP sections (5 in vertical slice). BM25 weights term density,
not document type. The "right" ranking depends on the user's intent
(which authority? which template?), and only the user knows that.

## 2026-04-26 - Citation duplicates from Appendix R (~55 records)

**Problem:** An early version of the parser emitted 1820 records but only 1758 unique
`citation_normalized` slugs landed in the DB. 62 records collided.

**Initial (wrong) hypothesis:** TOC-style listings duplicating actual
rule bodies. The longer-body-wins dedup masked this and let the
build verify clean.

**Actual root cause (discovered 2026-04-27 by drilling into the dropped
records):** the parser was collapsing legitimate distinct records:

- 7 in Appendix L: `(note)` companions to statutes (Public Law amendment
  notes, e.g. "AIA First inventor to file provisions"). Distinct
  content; should be separate records.
- 55 in Appendix R: `(pre-AIA)`, `(pre-PLT)`, `(pre-PLT (AIA))`,
  `(transitional)`, and date-range variants like `(2012-09-16 thru
  2013-12-17)`. Each is a distinct version of the rule that applies to
  applications during a specific period. Patent practitioners with
  mixed dockets (some pre-AIA, some AIA) need ALL versions retrievable.

**Solution:** generalized the heading regex to capture any parenthetical
marker after the rule/statute number. `_VARIANT_RE` allows one level of
nested parens. The captured marker becomes part of the citation
(`37 CFR 1.14 (pre-AIA)`) and the citation_normalized
(`37_cfr_1_14_pre_aia`). Records that previously collapsed are now
distinct retrievable records.

**Why:** the original spec language about pre-AIA was specific to 35 USC
("emit two records for AIA + pre-AIA") and the implementer extrapolated
that the same was needed for CFR. Wrong. CFR has more variants (4+
labels plus date ranges) and the parser was missing them entirely. Plus
35 USC (note) wasn't in the original spec at all.

**Lesson:** when the dedup heuristic is "longer-body-wins," check the
deltas. A 21KB-vs-1.3KB "duplicate" is a smoking-gun signal that the
parser is collapsing distinct records, not deduping noise. Tiny deltas
(8 bytes) might be true duplicates; large deltas mean there's a parser
bug. The `_duplicates.json` log made this discoverable.

**Result:** post-fix 3804 records (was 3742), 0 dropped duplicates.
+62 retrievable records that the practitioner now has access to.

## 2026-04-26 - HTML indentation bleeds into output without per-NavigableString collapse

**Problem:** First pass of the parser produced bodies with massive runs of
internal whitespace, e.g., `\n                                 For
applications\n                                 subject to ...`. Looked
broken even though structurally correct.

**Failed approach:** Document-level whitespace collapse (`re.sub(r'\n{3,}',
'\n\n', text)`). Did not touch intra-paragraph indentation that came from
USPTO's heavily-indented HTML source.

**Solution:** Added `collapse_inline()` helper that runs `re.sub(r'\s+', '
', text)` on each NavigableString as it's converted from HTML. Block-level
elements (`<p>`, `<li>`) wrap their inner content in `\n\n` boundaries, so
paragraph structure survives. The final `collapse_whitespace()` pass only
needs to collapse 3+ newlines to 2 and strip trailing whitespace per line.

**Why:** USPTO's HTML is indented for human readability with large runs of
spaces inside `<p>` and `<li>` text. BeautifulSoup preserves those as
NavigableString content. The fix is to do whitespace normalization at the
text-node level, not the document level.

## 2026-05-23 - Labeling discipline must scale to question weight

**Problem:** Initial SKILL.md mandated a bold provenance label on every
LLM-generated paragraph. In live use on compound practitioner questions
(rule lookup + rebuttal argument), this produced courtroom-exhibit
scaffolding when a colleague-email response was appropriate. The Mode 3
trigger also swept in practical-guidance questions ("how do I argue Y")
that should default to Mode 1 with extended summary.

**Failed approach:** Single mode per response with rigid IRAC scaffolding
regardless of question size. Result: practitioners scrolled past the
direct rule answer because it was buried under labels.

**Solution:** Three additions to SKILL.md: question
decomposition step before mode selection, proportionality principle
(length scales to question weight), compound-question pattern (Mode 1
first for rule, lightweight Mode 3 second for argument referencing the
quotation above). Plus umbrella-label exception for short LLM blocks
(<=3 paragraphs) so one label at the top of the block suffices.

**Why:** The verbatim-vs-LLM provenance distinction is the
non-negotiable property. Labeling EVERY paragraph was belt-and-suspenders
for short responses where blockquotes already make provenance visually
obvious. The discipline is "no unlabeled LLM content" not "label every
paragraph individually." That distinction wasn't clear in the first
draft; live use surfaced it.

## 2026-05-23 - Retrieve-before-cite is a pre-composition rule, not a verification-pass backstop

**Problem:** When asked to explain an IDS-page-numbers rebuttal, I
introduced 37 CFR 1.97(f) as a fallback argument, used a `Rule` label,
but filled the blockquote with `[not in corpus excerpt above - verify
against current MPEP/CFR]` because retrieval was skipped. The verification
pass detected the gap (claim provenance check) but the response shipped
anyway with a half-finished argument. The rule was actually IN the corpus
and one `lookup.py` call away.

**Failed approach:** Treating the verification pass as a "label and ship"
escape hatch rather than a "go retrieve" trigger. The `[verify before
relying]` placeholder felt honest but left the practitioner with an
unfinished citation.

**Solution:** Hardened SKILL.md with three changes:
(a) explicit "Retrieve before you cite" rule in Output discipline -
every citation in the response must be retrieved BEFORE composition,
not after; (b) tightened `[not in corpus]` escape hatch - only permitted
after `lookup.py` returns exit 2, never as a "skip retrieval for now"
shortcut; (c) Rule label now strictly requires an immediately-following
verbatim blockquote, plus verification pass step 5 catches the
"Rule label without blockquote" violation; (d) added "Common failure
modes" section with WRONG / RIGHT (in-corpus) / RIGHT (outside-corpus)
examples using the actual 1.97(f) failure.

**Why:** Post-composition checks are weaker than pre-composition rules.
The verification pass is a final-line backstop, not a substitute for
upfront retrieval. The label semantics need to be enforced as the model
writes, not after.

## 2026-05-24 - BM25 buries long canonical rules; inbound-reference count rescues them

**Problem:** `search.py "affidavit" --kind cfr_rule` ranked 37 CFR 1.131
(the prior-invention affidavit rule a practitioner expects) at #16, below
disciplinary (11.27, 1 inbound ref) and appeal-evidence (41.63, 3 refs)
rules that only mention affidavits in passing.

**Root cause:** BM25 length normalization. 1.131 is the longest affidavit
rule (892 words) and its regulatory text says "affidavit" exactly once in
the body plus once in the title - almost no term frequency. Short rules
(1.132, 89 words) or repetitive ones (1.130, 8 body mentions) win on
density. FTS5 does not expose BM25's length-normalization `b` parameter,
so column weights are the only lever - and a title-weight sweep plateaued
1.131 at #6 (it cannot beat the shorter affidavit-titled rules lexically).

**Failed approach:** Boosting the title column weight (4 -> 12). It lifted
1.131 but over-rewarded short titles that merely stem-match the query:
"obviousness" then surfaced MPEP 702.01 "Obviously informal application"
above MPEP 2141 (the actual obviousness guidelines). Reverted to 4.

**Solution:** Added an `inbound_refs` column (count of other records citing
each, built by a typed citation pass over every body_md) and a modest,
log-scaled ranking bump `bm25 - alpha*log1p(inbound_refs)`. A rule the rest
of the MPEP keeps citing is more likely the canonical authority. 1.131 is
the *most-cited* affidavit rule (~110 inbound), so it climbs into the
affidavit-rule cluster.

**Tuning:** alpha is the whole game. At 1.0 references dominate BM25 and
MPEP 2141 (only ~14 inbound, because many MPEP cross-refs omit the "MPEP"
prefix and are undercounted) fell to #3 for "obviousness". At **0.5** the
bump is a tiebreaker: 2141 stays #1, 35 USC 102 (387 refs) leads "novelty",
MPEP 804 (91 refs) leads "double patenting", and 1.131 rises #16 -> #6.

**Lesson:** Reference/citation count is a strong authority signal that BM25
lacks, but it must stay modest (log-scaled, low alpha) or it overrides
relevance. It works best for statutes/CFR (always cited with full
"35 U.S.C."/"37 CFR" prefixes, so counts are reliable); MPEP-section counts
are undercounted because cross-refs often drop the "MPEP" prefix. Improving
MPEP-ref extraction would let alpha go higher safely - a future refinement.

## 2026-05-24 - source_revision was derived from an arbitrary section, not the edition

**Problem:** The release asset nearly shipped named `mpep-...-R-07.2015.skill` -
implying a 9-year-old corpus - even though the build fetched the current Ninth
Edition, Revision 01.2024.

**Root cause:** `03_build_database.py` set `metadata.source_revision` from
`SELECT revision FROM sections WHERE kind='mpep_section' ... LIMIT 1` - an
arbitrary section. Each MPEP section heading carries the revision in which that
section was last changed, so the corpus has 11 distinct markers (R-01.2024 x552,
R-07.2022 x375, R-07.2015 x246, ...). The first row by rowid happened to be an
R-07.2015 section.

**Solution:** the edition is the NEWEST revision present (no section can be
revised later than the edition shipping it). Pick the max by (year,
revision-number) over the distinct markers; malformed ones (e.g. a typo'd
"R-08.1012") sort low and lose. Now correctly R-01.2024.

**Lesson:** when a value is meant to be a corpus-wide constant but you derive it
from per-record data, `LIMIT 1` is a latent bug - aggregate (max/mode) instead.
This one stayed invisible until the release-asset naming put the wrong value in
front of a human.
