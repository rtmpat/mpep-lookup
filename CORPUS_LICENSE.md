# Corpus License and Provenance

This project has two distinct licensing layers. Read both.

## 1. The code: Apache License 2.0

All source code in this repository -- the build pipeline (`build/`), the
runtime skill scripts (`skill/scripts/`), tests, and documentation -- is
licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE).

## 2. The corpus text: U.S. Government work, public domain

The substantive content this tool retrieves -- the Manual of Patent
Examining Procedure (MPEP), Title 35 of the U.S. Code (patent statutes,
MPEP Appendix L), Title 37 of the Code of Federal Regulations (patent
rules, MPEP Appendix R), and the USPTO Form Paragraphs -- is a work of the
United States Government. Under [17 U.S.C. Section 105](https://www.law.cornell.edu/uscode/text/17/105),
works prepared by U.S. Government officers and employees as part of their
official duties are not subject to copyright protection in the United
States and are in the public domain.

## How the corpus is obtained

The build pipeline does **not** redistribute a scraped copy of USPTO
content as repository source. Instead, `build/01_fetch_corpus.py` fetches
the HTML pages directly from `uspto.gov` **at build time**, and the
resulting SQLite database (`skill/data/mpep.db`) is generated locally and
is **not** tracked in git (see `.gitignore`).

The packaged `mpep-lookup.skill` bundle published via GitHub Releases
**does** contain the built database. That bundle is a derivative work of
public-domain U.S. Government source material; the public-domain status of
the underlying MPEP / 35 USC / 37 CFR / Form Paragraph text is unchanged
by its inclusion.

## Accuracy and currency

The corpus is locked to a single MPEP revision per build, recorded in the
database's `metadata.source_revision`. USPTO publishes new MPEP revisions
periodically; a bundle reflects only the revision current at its build
time. **This tool is a retrieval aid, not legal advice, and is not a
substitute for consulting the current official text published by the
USPTO.** Always verify against the authoritative source at
[mpep.uspto.gov](https://mpep.uspto.gov) before relying on any passage.

## Trademarks

"USPTO" and related marks are the property of the United States Patent and
Trademark Office. This project is not affiliated with, endorsed by, or
sponsored by the USPTO.
