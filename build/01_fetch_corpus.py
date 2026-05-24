"""Full MPEP corpus discovery + parallel polite crawl.

Discovery:
1. Fetch index.html
2. Extract all mpep-NNNN.html chapter URLs and appendix URLs
3. Sanity-check against EXPECTED_CHAPTERS / EXPECTED_APPENDIX_STEMS
4. For each chapter, fetch the chapter index page; extract all
   sNNNN.html section URLs (subsections live INSIDE these files, NOT as
   separate URLs)
5. Build the master URL list

Crawl:
- ThreadPoolExecutor(max_workers=WORKERS), default WORKERS=4
- Each worker uses requests.Session() for connection pooling
- Per-worker rate limit: time.sleep(1.0) after every request regardless
  of outcome
- Single writer thread drains a queue and appends to manifest.json
- Idempotent: skip already-cached files
- Ban detection: 429 -> back off + retry once -> if recurs, halt all
  workers via shared threading.Event; 403 or repeated RST -> halt immediately

Usage:
    python build/01_fetch_corpus.py [--workers N] [--rebuild-cache]
"""

import argparse
import json
import pathlib
import queue
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from chapter_list import (  # noqa: E402
    BAN_429_THRESHOLD,
    BAN_403_THRESHOLD,
    BAN_RST_THRESHOLD,
    DEFAULT_WORKERS,
    EXPECTED_APPENDIX_STEMS,
    EXPECTED_CHAPTERS,
    INDEX_URL,
    MAX_RETRIES,
    PER_WORKER_RATE_LIMIT_S,
    RETRY_BACKOFF_S,
    USER_AGENT,
    USPTO_BASE_URL,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_HTML = REPO_ROOT / "build" / "raw_html"
MANIFEST_PATH = RAW_HTML / "manifest.json"


# --- shared state for ban detection ---------------------------------------


@dataclass
class CrawlState:
    halt_event: threading.Event = field(default_factory=threading.Event)
    counts_lock: threading.Lock = field(default_factory=threading.Lock)
    count_429: int = 0
    count_403: int = 0
    count_rst: int = 0
    halt_reason: Optional[str] = None

    def record(self, kind: str) -> None:
        """Record a ban-signal hit; trigger halt_event if threshold met."""
        with self.counts_lock:
            if kind == "429":
                self.count_429 += 1
                if self.count_429 >= BAN_429_THRESHOLD * 2:  # second 429 halts
                    self.halt_event.set()
                    self.halt_reason = (
                        "USPTO returned 429 (Too Many Requests) repeatedly. "
                        "Reduce --workers (try 2 or 1) and rerun. Idempotent "
                        "fetch will resume from where it stopped."
                    )
            elif kind == "403":
                self.count_403 += 1
                if self.count_403 >= BAN_403_THRESHOLD:
                    self.halt_event.set()
                    self.halt_reason = (
                        "USPTO returned 403 (Forbidden). User-Agent may be "
                        "banned. Update the contact string in build/chapter_list.py "
                        "and wait at least 1 hour before retrying. Idempotent "
                        "fetch will resume from where it stopped."
                    )
            elif kind == "rst":
                self.count_rst += 1
                if self.count_rst >= BAN_RST_THRESHOLD:
                    self.halt_event.set()
                    self.halt_reason = (
                        f"Repeated TCP RST ({self.count_rst} occurrences) "
                        "across workers. Likely IP-level block. Same handling "
                        "as 403: wait at least 1 hour, then retry."
                    )


# --- discovery -------------------------------------------------------------


def discover_urls(session: requests.Session) -> tuple[list[str], dict]:
    """Fetch index.html + chapter indexes, return master URL list + stats."""
    print("Discovering URLs from index.html...", file=sys.stderr)
    resp = session.get(INDEX_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "lxml")

    # Chapter URLs: mpep-NNNN.html (with leading zero)
    chapter_urls = set()
    appendix_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("http://", "https://")):
            continue  # absolute URLs, skip (we want relative)
        if href.startswith("#"):
            continue  # anchor only
        # Match chapter URLs
        if re.match(r"^mpep-\d{4}\.html(?:#|$)", href):
            base = href.split("#")[0]
            chapter_urls.add(base)
        # Match appendix URLs
        elif re.match(r"^mpep-9\d{3}-", href):
            base = href.split("#")[0]
            appendix_urls.add(base)

    # Sanity check chapters
    discovered_chapter_nums = {
        int(re.match(r"^mpep-(\d{4})\.html$", c).group(1)) for c in chapter_urls
    }
    expected = set(EXPECTED_CHAPTERS)
    missing = expected - discovered_chapter_nums
    extra = discovered_chapter_nums - expected
    if missing or extra:
        msg_parts = []
        if missing:
            msg_parts.append(f"missing chapters: {sorted(missing)}")
        if extra:
            msg_parts.append(f"unexpected chapters: {sorted(extra)}")
        print(f"WARNING: chapter discovery mismatch: {'; '.join(msg_parts)}",
              file=sys.stderr)

    # Sanity check appendices
    discovered_appendix_stems = {a.removesuffix(".html") for a in appendix_urls}
    missing_appx = set(EXPECTED_APPENDIX_STEMS) - discovered_appendix_stems
    if missing_appx:
        print(f"WARNING: missing expected appendices: {sorted(missing_appx)}",
              file=sys.stderr)

    # For each chapter, fetch the chapter index and extract section URLs
    section_urls = set()
    for i, chapter_url in enumerate(sorted(chapter_urls)):
        full_url = USPTO_BASE_URL + chapter_url
        print(f"  [{i + 1}/{len(chapter_urls)}] discovering sections in {chapter_url}",
              file=sys.stderr)
        try:
            cresp = session.get(full_url, timeout=30)
            cresp.raise_for_status()
        except requests.RequestException as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
            continue
        csoup = BeautifulSoup(cresp.content, "lxml")
        for a in csoup.find_all("a", href=True):
            href = a["href"].strip()
            base = href.split("#")[0]
            # MPEP section URLs look like sNNNN.html (top-level only;
            # subsections are anchors, not separate files)
            if re.match(r"^s\d{1,4}\.html$", base):
                section_urls.add(base)
        # Polite even during discovery
        time.sleep(PER_WORKER_RATE_LIMIT_S)

    # Subject Matter Index: 26 per-letter HTML pages (mpep-index-a.html ..
    # mpep-index-z.html). index.html links only to the 'a' page; the rest are
    # reachable via each page's letter-nav, so enumerate the full set here.
    index_urls = {"mpep-index-%s.html" % c for c in "abcdefghijklmnopqrstuvwxyz"}

    all_urls = sorted({INDEX_URL.rsplit("/", 1)[1]} | chapter_urls
                      | section_urls | appendix_urls | index_urls)
    stats = {
        "chapters_discovered": len(chapter_urls),
        "sections_discovered": len(section_urls),
        "appendices_discovered": len(appendix_urls),
        "index_pages_discovered": len(index_urls),
        "total_urls": len(all_urls),
    }
    return all_urls, stats


# --- fetch worker ----------------------------------------------------------


def url_to_local_path(url: str) -> pathlib.Path:
    """Map a USPTO MPEP relative URL to its local cache path."""
    base = url.split("#")[0]
    if base == "index.html":
        return RAW_HTML / "index.html"
    if base.startswith("mpep-index-"):  # Subject Matter Index letter page
        return RAW_HTML / "index" / base
    if base.startswith("mpep-9"):  # appendix
        return RAW_HTML / "appendices" / base
    if base.startswith("mpep-"):   # chapter
        return RAW_HTML / "chapters" / base
    if base.startswith("s"):       # section
        return RAW_HTML / "sections" / base
    return RAW_HTML / "_other" / base


def fetch_one(
    session: requests.Session,
    url: str,
    state: CrawlState,
    out_q: "queue.Queue",
) -> Optional[dict]:
    """Fetch one URL with retries. Push manifest entry to out_q on success.

    Returns None on failure or halt.
    """
    if state.halt_event.is_set():
        return None

    full_url = USPTO_BASE_URL + url if not url.startswith("http") else url
    local = url_to_local_path(url)
    if local.exists():
        return {
            "url": full_url,
            "local_path": str(local.relative_to(REPO_ROOT)),
            "status_code": 200,
            "content_length": local.stat().st_size,
            "from_cache": True,
            "fetched_at": None,
            "sha256": None,  # cached entries don't recompute hash
        }

    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        if state.halt_event.is_set():
            return None
        try:
            resp = session.get(full_url, timeout=30)
        except requests.exceptions.ConnectionError as exc:
            msg = str(exc).lower()
            if "reset" in msg or "rst" in msg:
                state.record("rst")
            last_exc = exc
            time.sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])
            continue
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])
            continue
        finally:
            time.sleep(PER_WORKER_RATE_LIMIT_S)

        if resp.status_code == 200:
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(resp.content)
            import hashlib
            sha = hashlib.sha256(resp.content).hexdigest()
            return {
                "url": full_url,
                "local_path": str(local.relative_to(REPO_ROOT)),
                "status_code": 200,
                "content_length": len(resp.content),
                "from_cache": False,
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "sha256": sha,
            }
        if resp.status_code == 429:
            state.record("429")
            print(f"    HTTP 429 on {url} (retry after backoff)", file=sys.stderr)
            time.sleep(60)  # back off generously
            continue
        if resp.status_code == 403:
            state.record("403")
            print(f"    HTTP 403 on {url} (likely banned)", file=sys.stderr)
            return None
        if 500 <= resp.status_code < 600:
            print(f"    HTTP {resp.status_code} on {url} (retry)", file=sys.stderr)
            time.sleep(RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S) - 1)])
            continue
        # 4xx other than 403/429 - don't retry
        print(f"    HTTP {resp.status_code} on {url} (giving up)", file=sys.stderr)
        return None

    print(f"    FAILED after {MAX_RETRIES} retries: {url} ({last_exc})",
          file=sys.stderr)
    return None


# --- writer thread ---------------------------------------------------------


def manifest_writer(out_q: "queue.Queue", stop: threading.Event,
                    manifest_path: pathlib.Path) -> None:
    """Drain queue, append entries to manifest.json. One writer, no lock."""
    entries = []
    if manifest_path.exists():
        try:
            entries = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            entries = []
    seen_urls = {e["url"] for e in entries}

    while True:
        try:
            entry = out_q.get(timeout=1.0)
        except queue.Empty:
            if stop.is_set():
                break
            continue
        if entry is None:
            break
        if entry["url"] not in seen_urls:
            entries.append(entry)
            seen_urls.add(entry["url"])
            # Flush every 50 entries to survive crash
            if len(entries) % 50 == 0:
                manifest_path.write_text(json.dumps(entries, indent=2))
        out_q.task_done()
    manifest_path.write_text(json.dumps(entries, indent=2))


# --- main ------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                   help=f"Worker count (default {DEFAULT_WORKERS})")
    p.add_argument("--rebuild-cache", action="store_true",
                   help="Delete build/raw_html/ before fetching")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap total URLs fetched (for testing)")
    args = p.parse_args()

    if args.rebuild_cache and RAW_HTML.exists():
        import shutil
        print(f"Removing {RAW_HTML}/", file=sys.stderr)
        shutil.rmtree(RAW_HTML)
    RAW_HTML.mkdir(parents=True, exist_ok=True)

    # Discovery uses one session, sequential, polite
    disc_session = requests.Session()
    disc_session.headers["User-Agent"] = USER_AGENT
    urls, stats = discover_urls(disc_session)
    disc_session.close()

    print(f"\nDiscovery complete:", file=sys.stderr)
    for k, v in stats.items():
        print(f"  {k}: {v}", file=sys.stderr)

    if args.limit is not None:
        urls = urls[:args.limit]
        print(f"  (limited to first {args.limit} for testing)", file=sys.stderr)

    state = CrawlState()
    out_q: queue.Queue = queue.Queue()
    writer_stop = threading.Event()
    writer = threading.Thread(
        target=manifest_writer, args=(out_q, writer_stop, MANIFEST_PATH), daemon=True
    )
    writer.start()

    print(f"\nFetching {len(urls)} URLs with {args.workers} workers "
          f"(~{len(urls) / args.workers:.0f}s estimated at {args.workers} req/s)",
          file=sys.stderr)
    t0 = time.time()
    succeeded = 0
    failed = 0

    def worker_for_url(url: str) -> Optional[dict]:
        # Each worker thread gets its own Session for connection pooling
        s = getattr(threading.current_thread(), "_session", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
            threading.current_thread()._session = s  # type: ignore[attr-defined]
        return fetch_one(s, url, state, out_q)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker_for_url, u): u for u in urls}
        for fut in as_completed(futures):
            if state.halt_event.is_set():
                # Cancel remaining futures
                for f in futures:
                    f.cancel()
                break
            url = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                failed += 1
                print(f"    Worker exception for {url}: {exc}", file=sys.stderr)
                continue
            if result is None:
                failed += 1
            else:
                succeeded += 1
                out_q.put(result)
                if succeeded % 50 == 0:
                    elapsed = time.time() - t0
                    rate = succeeded / elapsed if elapsed > 0 else 0
                    print(f"  [{succeeded}/{len(urls)}] elapsed {elapsed:.0f}s, "
                          f"~{rate:.1f} req/s", file=sys.stderr)

    # Drain queue and stop writer
    out_q.join()
    writer_stop.set()
    writer.join(timeout=5)

    elapsed = time.time() - t0
    print(f"\nFetch complete in {elapsed:.0f}s.", file=sys.stderr)
    print(f"  Succeeded: {succeeded}", file=sys.stderr)
    print(f"  Failed: {failed}", file=sys.stderr)

    if state.halt_event.is_set():
        print(f"\nHALTED: {state.halt_reason}", file=sys.stderr)
        return 1

    if failed > 0:
        print(f"\n{failed} URLs failed (see logs above). Manifest at {MANIFEST_PATH}",
              file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
