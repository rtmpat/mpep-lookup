# Citation Formats

Reference for the citation forms accepted by `lookup.py` and the canonical
output format used by both `lookup.py` and `search.py`.

## Accepted input formats

The citation parser is tolerant of common variants. All of the following
resolve to the same record:

### MPEP sections

| Input | Resolves to |
|---|---|
| `MPEP 2141` | MPEP 2141 |
| `MPEP 2141.01(a)` | MPEP 2141.01(a) |
| `MPEP 2141.01 (a)` | MPEP 2141.01(a) |
| `MPEP § 2141.01(a)` | MPEP 2141.01(a) |
| `M.P.E.P. 2141.01(a)` | MPEP 2141.01(a) |
| `MPEP s. 2141.01(a)` | MPEP 2141.01(a) |
| `MPEP Section 2141.01 sub a` | MPEP 2141.01(a) |
| `2141.01(a)` (no MPEP prefix) | MPEP 2141.01(a) |

### 35 USC patent statutes

| Input | Resolves to |
|---|---|
| `35 USC 102` | 35 USC 102 (AIA, default) |
| `35 U.S.C. 102` | 35 USC 102 |
| `35 U.S.C. § 102` | 35 USC 102 |
| `35 USC §102` | 35 USC 102 |
| `35 USC 102 (pre-AIA)` | 35 USC 102 (pre-AIA) |
| `35 U.S.C. 102 (pre-AIA)` | 35 USC 102 (pre-AIA) |
| `35 USC 102 (pre AIA)` | 35 USC 102 (pre-AIA) |

**AIA vs pre-AIA:** for statutes with both versions, `lookup.py` returns
the AIA version by default. To get the pre-AIA version, use the explicit
`(pre-AIA)` suffix. The corpus stores them as separate records.

### 37 CFR patent rules

| Input | Resolves to |
|---|---|
| `37 CFR 1.131` | 37 CFR 1.131 |
| `37 C.F.R. 1.131` | 37 CFR 1.131 |
| `37 C.F.R. § 1.131` | 37 CFR 1.131 |

### Form Paragraphs

| Input | Resolves to |
|---|---|
| `Form Paragraph 7.05` | Form Paragraph 7.05 |
| `FP 7.05` | Form Paragraph 7.05 |
| `Form Paragraph 7.05.aia` | Form Paragraph 7.05.aia |

## Canonical output format

The `citation` field in the database is the canonical form:

- `MPEP NNNN[.NN][(letter)]`
- `35 USC NNN[(letter)]` or `35 USC NNN (pre-AIA)`
- `37 CFR PART.RULE[(letter)]`
- `Form Paragraph NNN.NN[.NN-or-suffix]`

The `citation_normalized` field is lowercase, dots/parens/hyphens/whitespace
all converted to single underscores, used as the SQL key:

- `mpep_2141_01_a`
- `35_usc_102` / `35_usc_102_pre_aia`
- `37_cfr_1_131`
- `fp_7_05` (note: "Form Paragraph" is canonicalized to "fp")
