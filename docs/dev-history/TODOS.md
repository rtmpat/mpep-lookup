# TODOs

Deferred work surfaced during planning. Each item documents what, why,
effort, dependencies, and where to start when picked up.

## P2: Auto cross-reference resolution

**What.** When `lookup.py` returns a section that cites another section
(e.g., MPEP 2141 references MPEP 2143.02), automatically retrieve the cited
section(s) in the same response so Claude doesn't need to chain lookups.

**Why.** Patent practitioners reading the MPEP frequently chase the chain of
cross-references manually. Surfacing the chain in a single retrieval reduces
context-switching and matches how the document is actually read in practice.
Currently SKILL.md instructs Claude to chain lookups; this is correct but
slow when the chain is long.

**Effort.** S (human ~1 day / CC ~30 min). Add a regex pass over `body_md` to
extract `MPEP \d+(\.\d+)?(\([a-z]\))?`, `35 U.S.C. \d+`, `37 C.F.R. \d+\.\d+`
references. Add `--resolve-refs N` flag to `lookup.py` (default 0 = no
resolution, 1 = one level, 2 = two levels). Output structure: primary record
+ `referenced_sections: [...]`.

**Risk.** Low. Cross-references are bounded (most sections reference 0-5
others) and the resolution can be capped to prevent runaway recursion.

**Depends on.** v1 ships first. Citation parser must be robust enough to
parse extracted reference strings.

**Where to start.** `skill/scripts/lookup.py` — add `--resolve-refs`. Reuse
`citation_parser.parse_citation()` for normalization.

---

## P2: GitHub Action for auto-rebuild on new MPEP revisions

**What.** Scheduled GitHub Action that runs once a week, fetches USPTO
`index.html`, compares the published revision against the most recent build's
`metadata.source_revision`, and if newer: triggers the full build pipeline
and publishes the resulting `mpep-lookup.skill` as a release artifact.

**Why.** MPEP gets revised every 6-12 months. Manually rebuilding when a new
revision drops is fine for one practitioner but error-prone (you forget; you
notice 4 months late). A scheduled rebuild gives you a current corpus
without thinking about it.

**Effort.** M (human ~1-2 days / CC ~1 hour). Standard GitHub Actions YAML.
Build job: Python 3.12, install deps, run phases 1-5, upload artifact. Add a
secrets-free schedule (no auth needed since USPTO is public).

**Risk.** Low for v1; medium if scope expands. USPTO rate limits could be
tripped by repeated rebuilds during testing — pre-flight in a sandbox first.

**Depends on.** v1 build pipeline must run cleanly end-to-end first. Cannot
automate what isn't reliable manually.

**Where to start.** `.github/workflows/rebuild-on-revision.yml`. Use
`actions/setup-python@v5`. Cron `0 12 * * 1` (Mondays at noon UTC).

---

## P3: AIA-vs-pre-AIA delta search

**What.** A `delta` command that, given a 35 USC section with both AIA and
pre-AIA versions stored, produces a unified diff of the two bodies so the
practitioner can see exactly what changed at the statute level.

**Why.** Mixed dockets where some applications are pre-AIA and some are AIA
require constant context-switching between which version of 102 applies.
A direct diff between `35 USC 102` and `35 USC 102 (pre-AIA)` is more useful
than reading both records separately.

**Effort.** S (human ~half a day / CC ~20 min). Use `difflib.unified_diff`
on the two body fields. Add `delta.py` script alongside `lookup.py` and
`search.py`. Probably useful only for 35 USC 102, 103, 112; specs of which
sections support `delta` documented in `references/citation_format.md`.

**Risk.** Low. Pure read-only; no schema changes.

**Depends on.** v1 ships with pre-AIA records present. Already in scope.

**Where to start.** New `skill/scripts/delta.py`. Run `lookup.py` for both
records under the hood, then diff `body_md`.
