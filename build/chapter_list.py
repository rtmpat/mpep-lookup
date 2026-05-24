"""Constants for the MPEP build pipeline.

Used by 01_fetch_corpus.py as a sanity check: if discovery finds chapters
not in EXPECTED_CHAPTERS or vice versa, fail loud.

ASCII-only.
"""

# All MPEP chapter numbers discovered from the USPTO index. The original
# spec listed 28 chapters skipping 2600; live USPTO has 29 chapters including
# 2600 (International Design Applications under the Hague Agreement).
EXPECTED_CHAPTERS: tuple[int, ...] = (
    100, 200, 300, 400, 500, 600, 700, 800, 900,
    1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900,
    2000, 2100, 2200, 2300, 2400, 2500, 2600, 2700, 2800, 2900,
)


# Appendix URL stems we expect to see linked from index.html. Used as a
# sanity baseline; discovery is canonical, this list is a tripwire.
EXPECTED_APPENDIX_STEMS: tuple[str, ...] = (
    "mpep-9015-appx-l",       # Patent Laws (35 USC)
    "mpep-9020-appx-r",       # Patent Rules (37 CFR)
    "mpep-9095-Form-Paragraph-Chapter",  # Form Paragraphs
    # Note: MPEP Appendix I is [Reserved] (empty). The Subject Matter Index is
    # a separate set of pages, mpep-index-a.html .. -z.html, discovered in
    # discover_urls(), not an appendix stem.
)


USPTO_BASE_URL = "https://www.uspto.gov/web/offices/pac/mpep/"
INDEX_URL = USPTO_BASE_URL + "index.html"

USER_AGENT = "mpep-lookup-skill-builder/1.0"

# Per-worker rate limit: 1.0s between requests of THIS worker. Aggregate
# across N workers is N req/s. Ban-detection thresholds below.
PER_WORKER_RATE_LIMIT_S = 1.0
DEFAULT_WORKERS = 4

# Ban detection. If we see any of these on aggregate count, halt all workers.
BAN_429_THRESHOLD = 1   # Any 429 -> back off + retry once -> if recurs, halt
BAN_403_THRESHOLD = 1   # Any 403 -> halt immediately
BAN_RST_THRESHOLD = 5   # 5 connection resets across workers -> halt

# Retry policy
MAX_RETRIES = 3
RETRY_BACKOFF_S = (2, 4, 8)
