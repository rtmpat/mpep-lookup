---
name: mpep-lookup
version: 1.0.0
description: Look up, search, summarize, analyze, and apply the USPTO MPEP, 35 USC statutes (Appendix L), 37 CFR rules (Appendix R), and Form Paragraphs. Use whenever the user cites an MPEP section, statute, or rule; asks what the MPEP says about a topic; asks for analysis, summary, or application of patent prosecution authority; or needs a citation-precise patent practice answer. Use even if the user does not name the MPEP explicitly - questions about obviousness, anticipation, eligibility, restriction, RCE, IDS, double patenting, claim drafting, or any other patent prosecution matter should trigger this skill before generating from general knowledge. Returns verbatim quotations with pin cites, plus optional labeled LLM-generated summaries, IRAC analysis, or application to factual patterns. All LLM-generated content is explicitly labeled to distinguish from verbatim authority.
---

# MPEP Lookup

This skill provides verbatim, citation-precise retrieval from the USPTO Manual of Patent Examining Procedure and related authorities. The full corpus is bundled as a SQLite database in `data/mpep.db` and queried locally via the scripts in `scripts/`.

## When to use this skill

- The user mentions an MPEP section number, 35 USC statute, 37 CFR rule, or Form Paragraph number.
- The user asks what the MPEP / statutes / rules say about a topic.
- The user asks for verbatim quotation of patent prosecution authority.
- The user asks for a summary, analysis, or explanation of patent prosecution authority (use Mode 1 or Mode 3 below).
- The user provides a factual pattern and asks for application of MPEP / statutes / rules to those facts (use Mode 4 below).
- The user asks any patent prosecution practice question (obviousness, anticipation, eligibility, restriction, RCE, IDS, double patenting, claim drafting requirements, etc.) where the right answer is grounded in the MPEP rather than generated from general knowledge.

## Output discipline (mandatory)

The text returned by `lookup.py` and `search.py` is the exact text from the source. Every span you present as a quotation must appear verbatim in the `body_md` returned by the script, with a pin cite. **LLM-generated content (summaries, analysis, application to facts) is allowed and useful, but must be explicitly labeled** so the practitioner can tell at a glance which words are from the authority and which are yours.

The discipline is not "no LLM content." The discipline is "no unlabeled LLM content." A practitioner reading your output should never have to guess whether a sentence is from the MPEP or from you.

**Retrieve before you cite.** Before composing any response that mentions a specific MPEP section, statute, rule, or Form Paragraph, run `lookup.py` for that citation. Cite from the script output, not from memory. This applies even when the citation is incidental — for example, when an `Application` block in IRAC mode references a related rule, that rule must be retrieved before you write about it. The pattern "cite from memory now, verify in the verification pass later" is **not** acceptable. The verification pass is a final-line backstop, not a substitute for upfront retrieval.

If a piece of information is not in the corpus, say so — but verify the absence first. The `[not in corpus — verify against current MPEP]` marker is permitted **only** when `lookup.py` actually returns exit 2 (not found) for the citation, or when the content is structurally outside the corpus (e.g., Federal Circuit case opinions, USPTO Director memos, content from MPEP revisions later than the bundled `metadata.source_revision`). It is **not** permitted as a substitute for running `lookup.py` on a citation that is in the corpus. If unsure whether a citation is in the corpus, retrieve first; only after `lookup.py` returns not-found should you use the placeholder. Do not infer from general knowledge.

## Citation lookup

Run `python scripts/lookup.py "<citation>"`. Accepts:

- `MPEP 2141`, `MPEP 2141.01(a)` - MPEP sections
- `35 USC 102`, `35 U.S.C. 102` - patent statutes (returns AIA version by default)
- `35 USC 102 (pre-AIA)` - pre-AIA version
- `37 CFR 1.131`, `37 C.F.R. 1.131` - patent rules
- `Form Paragraph 7.05`, `FP 7.05` - examiner form paragraphs

Tolerates citation format variants: with or without `MPEP` prefix, with or without `s.` / `Section` / `sub`, with or without periods in `U.S.C.` / `C.F.R.`, with or without section symbol.

Example:

```
$ python scripts/lookup.py "MPEP 2141"
Citation: MPEP 2141
Title: Examination Guidelines for Determining Obviousness Under 35 U.S.C. 103
Kind: mpep_section
Revision: R-01.2024
Chapter: 2100
Source: https://www.uspto.gov/web/offices/pac/mpep/s2141.html

[verbatim body text...]
```

Exit codes:
- 0: hit
- 1: bad input format
- 2: not found (suggestions printed to stderr)
- 3: database missing or corrupt

`--json`: emit JSON. `--max-words N`: truncate body if context-constrained.

## Topical search

Run `python scripts/search.py "<query>"`.

**The search is Subject Matter Index-aware by default.** Sections the index corroborates are boosted and tiered: `index+keyword` (matched by the query AND referenced by a matching index term) rank first, then `index` (the sections the index points to) interleaved with keyword-matching statutes and CFR rules (which the index does not cover but which stay keyword-competitive), then `keyword`-only MPEP sections, and finally Form Paragraphs (templates, not authority, ranked last). Index entries are never returned as results - they are a ranking signal. Each hit is tagged with its tier and the index term that corroborated it. Within every tier a modest authority bump favors records the rest of the MPEP cites most (so 35 USC 102 leads "novelty" and 37 CFR 1.131 surfaces for "affidavit", despite long bodies). Use `--keyword-only` for a plain FTS5 search.

Query syntax is SQLite FTS5. Common patterns:

- `"reasonable expectation of success"` - exact phrase (note the quotes)
- `obviousness AND prima facie` - both terms
- `analogous NEAR/10 art` - within 10 tokens
- `obvious*` - prefix wildcard

For full FTS5 syntax see `references/search_syntax.md`.

**Use kind filters for narrower searches.** The corpus contains 5+ record types. Without filters, short Form Paragraph titles can outrank canonical MPEP/USC/CFR records on common terms. To find authoritative text on a topic, filter by kind:

```
python scripts/search.py "novelty" --kind statute       # -> 35 USC 102
python scripts/search.py "obviousness" --kind mpep_section
python scripts/search.py "affidavit" --kind cfr_rule    # -> 37 CFR 1.132
python scripts/search.py "rejection" --kind form_paragraph
```

`--keyword-only` disables index fusion; `--include-index` surfaces index entries themselves as hits; `--kind KIND` scopes to one record type. To get ONLY the index's curated sections for a topic, returned verbatim and grouped by index term, use `index_lookup.py` below.

## Topic routing via the Subject Matter Index

`search.py` already blends the index into its default ranking. Reach for `index_lookup.py "<topic>"` when you want *only* the sections the USPTO's own Subject Matter Index assigns to a term, returned verbatim and grouped by the matching index entry - e.g. "exactly which sections does the MPEP index file under double patenting."

```
python scripts/index_lookup.py "double patenting"
python scripts/index_lookup.py --entries 3 --sections 8 "incomplete reply"
```

It prints the matched index entries (with their section pointers and any "See also" cross-references), then the verbatim section records those references resolve to. Quote from the resolved sections, not from the index entry itself. For broad full-text search (including statutes, rules, and Form Paragraphs, which the index does not cover), use `search.py`.

## Output modes

### Step 1: Decompose the request

Before picking a mode, scan the user's request for distinct asks. Compound questions like "What is the rule for X? If [hypothetical], how do I argue Y?" are **two requests, not one** — a rule lookup and an application argument. Answer them in sequence with the appropriate mode for each, rather than force-fitting both into a single mode.

The most common compound pattern in patent practice is "rule + rebuttal": the practitioner wants the rule first (clean Mode 1), then guidance on how to argue or apply it (lightweight Mode 3). Don't bury the direct rule answer inside IRAC scaffolding when the user asked for it cleanly. See the **Compound questions** pattern below.

### Step 2: Pick a mode (or modes, in sequence)

Pick the mode that fits each request. If ambiguous, default to **Mode 1 (Quotation + Summary)** — don't over-deliver analysis when the user just wanted to look up a section.

Strong signals for each mode:

| Signal | Mode |
|---|---|
| "what does X say," "look up X," "what's the rule for," "what's the requirement for," cite numbers, default | Mode 1 |
| "full text," "everything," "the entire section" | Mode 2 |
| **Pedagogical / doctrinal asks only:** "analyze," "explain how X applies," "walk me through the test for Y," "what's the doctrinal framework for Z" | Mode 3 |
| User provides facts + asks "is this X" / "would this be obvious" / "is this patentable" | Mode 4 |

**Mode 3 is NOT triggered by practical-guidance questions.** "How do I respond to X," "what's the appropriate argument for Y," "how should I push back on Z" are practitioner workflow questions, not pedagogical ones. They default to **Mode 1 with an extended summary** that includes practical implications, OR a **compound response** (Mode 1 + lightweight Mode 3) when the question has both a rule-lookup and an argument component. Reserve full Mode 3 IRAC scaffolding for explicit pedagogical or doctrinal asks.

### Proportionality (applies to Modes 3 and 4)

Length of LLM-generated sections scales to the weight of the question. A doctrinal question with a one-sentence hypothetical produces a one-paragraph Application and a 2-3 sentence Conclusion (or no separate Conclusion at all — see "Conclusion folding" below). A substantive multi-fact analysis can run longer. **Do not pad to fill the IRAC scaffold.** A colleague-style answer beats a courtroom-exhibit answer when the question is colleague-sized.

### Conclusion folding (applies to Modes 3 and 4)

When the Application is three paragraphs or fewer and the Conclusion would only restate the Application's final move, **omit the separate Conclusion block.** The Application's last sentence carries the conclusion in that case. Keep a separate Conclusion block as the default for substantive doctrinal analyses where the conclusion adds genuinely new framing or caveats.

### Consolidate same-source quotations (applies to Modes 3 and 4)

When multiple verbatim spans come from the same MPEP section, CFR rule, or USC statute, combine them into a single Quotation or Rule block (use ellipses or paragraph breaks for omitted text between spans) rather than emitting multiple labeled blocks for the same source. Use separate Rule blocks only when the rule text spans genuinely different sources (e.g., MPEP 2141 + 35 USC 103).

### Mode 1: Quotation + Summary (DEFAULT)

For "what does the MPEP say about X" / "look up MPEP 2141" / direct citation lookups. A pinpoint verbatim quote (1-3 paragraphs of the most relevant body_md text), then a brief plain-language summary in your own words.

```
**Quotation** — *MPEP 2141.01(a) [R-01.2024]*:

> [verbatim pinpoint quote, 1-3 paragraphs of the most relevant body_md text]

**Summary (LLM-generated, citing MPEP 2141.01(a)):**

[Plain-language restatement, 2-4 sentences. Reference the citation again
at the end of the summary.]

---
*Quotation verbatim from MPEP corpus (revision per metadata.source_revision).
Summary is Claude's restatement, not authority.*
```

### Mode 2: Full Quotation

For "show me the full text of," "everything about," or explicit requests for deeper quoting. Return more or all of the verbatim body. Skip the summary unless the user also asks for one.

```
**Quotation** — *MPEP 2141.01(a) [R-01.2024]*:

> [verbatim full body or the requested span]

---
*Retrieved verbatim from MPEP corpus.*
```

### Mode 3: Analysis (IRAC)

For pedagogical / doctrinal asks only: "analyze," "explain how X applies," "walk me through the test for Y," "what's the doctrinal framework." Use IRAC structure (Issue / Rule / Application / Conclusion). The Rule is verbatim from the corpus. The Issue, Application, and Conclusion are LLM-generated and labeled as such. Apply the Proportionality, Conclusion folding, and Consolidate same-source quotations rules from above.

```
**Analysis (LLM-generated, IRAC):**

**Issue (LLM-generated):** [Claude's framing of the legal question]

**Rule** — *verbatim from MPEP 2141.01(a) [R-01.2024]*:

> [pinpoint quotation of the rule; consolidate spans from the same
> source into one block using ... or paragraph breaks for omitted text
> between spans]

[If helpful, an optional brief restatement labeled
"**Rule restatement (LLM-generated, citing MPEP 2141.01(a)):**"]

**Application (LLM-generated):**

[Claude's analysis. Length scales to the question — one paragraph for
short hypotheticals, multi-paragraph for substantive doctrinal asks.
May include additional verbatim quotations from genuinely different
sources, each properly labeled and cited as a Quotation block.]

**Conclusion (LLM-generated, preliminary):**

[Claude's conclusion, with caveats about jurisdictional limits, factual
assumptions, and a recommendation to verify against current MPEP and
case law before relying on it for filing decisions.

OMIT this entire block when the Application is three paragraphs or
fewer and the conclusion would only restate the Application's final
move. The Application's last sentence carries the conclusion in that
case.]

---
*Rule sections are verbatim from the MPEP corpus; Issue / Application /
Conclusion are LLM-generated analysis. Confirm against current MPEP and
case law before relying on this for filing decisions.*
```

### Mode 4: Application to a Factual Pattern

For "given these facts, would this be obvious / patentable / restricted?" The user has provided a factual pattern; you apply the authority to those facts. Use IRAC anchored to the user's facts. Apply the Proportionality, Conclusion folding, and Consolidate same-source quotations rules from above. Match length to the weight of the facts: a one-to-two sentence hypothetical produces a one-paragraph Application; a five-paragraph fact pattern produces a longer Application. Don't pad.

```
**Application to facts (LLM-generated, applying MPEP):**

**Facts (as stated by user):**

[Claude's restatement of the user's factual pattern. If the user's facts
are incomplete in a way that affects the analysis, flag the gap here.]

**Issue (LLM-generated):** [What legal question the facts raise]

**Rule** — *verbatim from MPEP 2141 [R-01.2024]*:

> [pinpoint quotation; consolidate spans from the same source into one
> block. Use additional Rule blocks only for genuinely different sources
> (e.g., MPEP + 35 USC + 37 CFR).]

**Application (LLM-generated):**

[Claude applies the rule to the user's facts. Length scales to the
weight of the facts.]

**Conclusion (LLM-generated, preliminary):**

[Claude's preliminary conclusion. OMIT this block when the Application
is three paragraphs or fewer and the conclusion would only restate the
Application's final move; the Application's last sentence carries it.]

---
*Rule sections are verbatim from the MPEP corpus; Facts / Issue /
Application / Conclusion are LLM-generated analysis applied to
user-provided facts. This is preliminary practitioner guidance, not legal
advice — confirm against current MPEP, applicable case law, and the
specific examiner's tendencies before relying on it.*
```

### Compound questions: Mode 1 + lightweight Mode 3

When the user asks both a rule-lookup question AND a practical-guidance question in the same turn, answer in sequence.

**Pattern:**

1. **Mode 1 first** — quotation + summary covering the rule and any sub-distinctions. The direct answer to the rule question lands first in the response, NOT buried inside an IRAC scaffold.
2. **Lightweight Mode 3 second** — for the application or argument portion. The Rule block references back to the quotation already provided rather than re-quoting the same authority: `**Rule** — see quotation above (<short pin cite>).`
3. The lightweight Mode 3's Application is one paragraph for short hypotheticals. The Conclusion folds into the Application's final sentence per the Conclusion folding rule above.

**Worked example.** User asks: "What is the requirement for identifying pages in an IDS publication citation under 37 CFR 1.98(b)? If an examiner refuses to consider a citation for lack of page numbers, what is the appropriate rebuttal argument?"

Response structure:

```
[Mode 1 — answers the rule question]

**Quotation** — *37 CFR 1.98(b)(5)*:

> [verbatim text]

**Quotation** — *MPEP 609.04(a) [R-07.2022]*:

> [verbatim text including the "when no page numbers are supplied" sentence]

**Summary (LLM-generated, citing 37 CFR 1.98(b)(5) and MPEP 609.04(a)):**

[Plain-language summary covering the rule and its sub-distinctions —
e.g., that "relevant pages" applies only to non-patent literature, and
that omitting pages is treated as a designation that all pages are
relevant.]

---

[Lightweight Mode 3 — answers the rebuttal question]

**Analysis (LLM-generated, IRAC):**

**Issue (LLM-generated):** [Framing of the rebuttal question.]

**Rule** — see quotation above (MPEP 609.04(a)).

**Application (LLM-generated):**

[One paragraph of analysis explaining the rebuttal: the primary move
(MPEP 609.04(a) construes omission as a default designation), and the
fallback if needed (37 CFR 1.97(f) bona-fide-attempt provision — quote
that ONLY if it adds genuinely different rule text). The final sentence
carries the conclusion. No separate Conclusion block.]
```

Total length should be proportional to the fact pattern. For a one-to-two sentence hypothetical, expect roughly the length of a colleague's email response, not a courtroom exhibit.

### Search results (any mode)

For `search.py` results showing multiple hits, show top N as a list of pin cites with snippets, then offer to retrieve any of them in full via `lookup.py`. The search snippets ARE verbatim spans (FTS5's `snippet()` function returns body_md substrings with `<b>`/`</b>` markers around matches), so they count as Quotation under the labeling discipline.

## Required labels

Every section of LLM-generated content must carry one of these labels. Verbatim quotations carry their own label. Anything in your response that does NOT carry one of these labels is non-compliant — re-label or strike.

**Umbrella label exception for short blocks.** For an LLM analysis block of three paragraphs or fewer within a single mode section (e.g., a short Application paragraph in Mode 3 or a compound-response Application), a single umbrella label at the top of the block is sufficient. Sub-paragraph labeling within that block is optional. Every LLM block must still carry at least one provenance label; no unlabeled LLM content appears in any response. The exception is about visual weight, not provenance — the block is still labeled, just once instead of repeatedly.

| Label | Use for |
|---|---|
| `**Quotation** — *<cite>*:` | Verbatim text from corpus, in `>` blockquote |
| `**Summary (LLM-generated, citing <cite>):**` | Claude's restatement of authority |
| `**Analysis (LLM-generated, IRAC):**` | Top-level IRAC analysis section header |
| `**Issue (LLM-generated):**` | IRAC issue statement |
| `**Rule** — *verbatim from <cite>*:` | IRAC rule. **MUST be immediately followed by a `>` blockquote containing the verbatim text retrieved via `lookup.py`. A `Rule` label without an accompanying verbatim blockquote is non-compliant — strike the label or retrieve and quote properly.** |
| `**Rule restatement (LLM-generated, citing <cite>):**` | Optional rule paraphrase |
| `**Application (LLM-generated):**` | IRAC application or application to facts |
| `**Conclusion (LLM-generated, preliminary):**` | IRAC conclusion |
| `**Facts (as stated by user):**` | Claude's restatement of user-provided facts |

## Verification pass (mandatory before sending output)

After composing a response, perform this self-check:

1. **String-containment check.** Every span inside a `>` blockquote must appear as an exact substring of the `body_md` returned by the script, modulo whitespace normalization. If any quoted span fails containment, strike it or replace it with the verbatim original.
2. **Citation match.** Every citation in your response must match the `citation` field from the script output exactly (case-insensitive on the prefix `MPEP` / `35 USC` / `37 CFR` / `Form Paragraph`, exact on the section number).
3. **Claim provenance.** No factual claim about MPEP / 35 USC / 37 CFR appears in your response unless it is directly supported by the script output. Claims based on training-data knowledge are stripped or replaced with `[not in corpus — verify against current MPEP]`.
4. **Labeling discipline.** Every paragraph or block of text in your response is either (a) a `>` blockquote under a `**Quotation**` / `**Rule**` label, or (b) prose under one of the LLM-generated labels from the table above — with the umbrella-label exception: a single label at the top of an LLM block of three paragraphs or fewer covers the whole block, and sub-paragraph labeling within that block is optional. No mixing within a single block — a paragraph is either entirely quoted authority or entirely Claude's analysis. Any unlabeled LLM-generated content (i.e., a block carrying NO provenance label at all) is non-compliant.
5. **Rule label has its blockquote.** Every `**Rule** — *verbatim from <cite>*:` label is immediately followed by a `>` blockquote whose contents pass the string-containment check against the corresponding `lookup.py` output. A `Rule` label not followed by a verbatim blockquote means you tried to assert a rule from memory — strike the label or retrieve and quote properly.
6. **Citation retrieval.** Every citation in your response — including those in Rule blocks, Quotation blocks, inline references in Application/Conclusion blocks, and footers — has been retrieved via `lookup.py` during this conversation. If a citation appears that was never retrieved, retrieve it now. Do not ship `[verify before relying]` placeholders for citations that are in the corpus.

If any check fails, correct the response before sending.

## Common failure modes

**Anti-pattern: citing from memory and labeling the gap.**

The most common discipline failure is using a label that promises verbatim text, then filling it with a "verify before relying" placeholder because retrieval was skipped. The `Rule` label promises verbatim. The placeholder breaks the promise.

WRONG (rule IS in the corpus):

```
**Rule** — *verbatim from 37 CFR 1.97(f)*:

[not in corpus excerpt above — verify against current MPEP/CFR]
```

If the rule is in the corpus, retrieve it. `lookup.py "37 CFR 1.97"` returns the full rule text including subsection (f). The right move is to retrieve and quote, not label-and-defer.

RIGHT (rule IS in the corpus):

```
**Rule** — *verbatim from 37 CFR 1.97(f)*:

> If a *bona fide* attempt is made to comply with Section 1.98, but
> part of the required content is inadvertently omitted, additional
> time may be given to enable full compliance.
```

RIGHT (citation is OUTSIDE the corpus):

When a Federal Circuit case opinion or other authority that the corpus does not include is genuinely relevant, do NOT use a `Rule` label. Describe the limitation under an `Application (LLM-generated):` label instead, and tell the user the citation is unverified.

```
**Application (LLM-generated):**

Federal Circuit precedent on inequitable conduct (e.g., *Therasense,
Inc. v. Becton, Dickinson and Co.*) is not in this skill's corpus.
The general doctrine is that materiality and intent must both be
established by clear and convincing evidence, but confirm against
current case law before relying on this characterization.
```

The pattern: a `Rule` label always gets a verbatim blockquote retrieved from the corpus this turn. If you can't produce that blockquote, you're not in `Rule` territory — you're in `Application` territory, and the practitioner needs to know the underlying authority is unverified.

## When zero results

If `search.py` returns `{"hits": [], "fts_query_parsed_ok": true}` (JSON mode) or "No matches for FTS5 query ... (query parsed cleanly)" (text mode), the query ran successfully but found nothing. **Do not fall back to training-data knowledge.** Tell the user the corpus has no match for that query and suggest reformulating, broadening, or trying a different kind filter.

## Limitations

- Corpus is locked to the revision recorded in `metadata.source_revision`.
- Cross-reference resolution is not automatic; run separate lookups for cited sections.
- Vector / semantic similarity is not provided - search is keyword/phrase based. Use kind filters to narrow.
- Form Paragraphs are examiner template language; the default search ranks them last. Use `--kind form_paragraph` when you specifically want them.

## Version

This skill is **MPEP Lookup v1.0.0**. End every response produced with this skill with a footer on its own line, as the very last line of the response:

`*MPEP Lookup v1.0.0*`

`CHANGELOG.md` (bundled with the skill) summarizes the features of this version. When the version changes, update the footer string above, the `version` field in this file's frontmatter, and `CHANGELOG.md` together.
