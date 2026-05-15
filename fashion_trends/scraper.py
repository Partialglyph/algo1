"""
Fashion Trends Scraper
======================
Lightweight scraper using requests + BeautifulSoup (no browser required).
Works reliably in GitHub Actions without Playwright or Chromium.

Source groups:
  media    — RSS feeds from Vogue, Hypebae, Who What Wear (no blocking)
  retail   — requests + BeautifulSoup for Zara, ASOS, SSENSE
  dropship — requests + BeautifulSoup for 9 supplier catalogs

Usage:
    python -m fashion_trends.scraper
"""

import json
import logging
import random
import sqlite3
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
DICT_PATH = Path(__file__).parent / "dictionary.json"
DB_PATH   = ROOT / "trends.db"

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

# ─── Request Headers ──────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }

# ─── RSS Feed Targets (media group) ──────────────────────────────────────────
RSS_TARGETS = [
    {
        "site": "vogue",
        "group": "media",
        "url": "https://www.vogue.com/feed/rss",
    },
    {
        "site": "whowhatwear",
        "group": "media",
        "url": "https://www.whowhatwear.com/feeds/all.rss",
    },
    {
        "site": "harpersbazaar",
        "group": "media",
        "url": "https://www.harpersbazaar.com/feed/",
    },
    {
        "site": "elle",
        "group": "media",
        "url": "https://www.elle.com/feed/",
    },
    {
        "site": "refinery29",
        "group": "media",
        "url": "https://www.refinery29.com/en-us/rss.xml",
    },
]

# ─── HTML Scrape Targets (retail + dropship) ──────────────────────────────────
HTML_TARGETS = [
    # ── Retail ────────────────────────────────────────────────────────────────
    {
        "site": "asos",
        "group": "retail",
        "url": "https://www.asos.com/women/new-in/new-in-clothing/cat/?cid=2623&sort=freshness",
        "tags": ["h3", "a", "img"],
        "attrs": ["alt", "title", "aria-label"],
    },
    {
        "site": "zara",
        "group": "retail",
        "url": "https://www.zara.com/us/en/woman-new-in-l1180.html",
        "tags": ["h2", "h3", "span", "img"],
        "attrs": ["alt", "title"],
    },
    {
        "site": "ssense",
        "group": "retail",
        "url": "https://www.ssense.com/en-us/women/new-arrivals",
        "tags": ["h3", "p", "img"],
        "attrs": ["alt"],
    },
    # ── Dropship ──────────────────────────────────────────────────────────────
    {
        "site": "trendsi",
        "group": "dropship",
        "url": "https://www.trendsi.com/collections/new-arrivals",
        "tags": ["h2", "h3", "a", "img"],
        "attrs": ["alt", "title"],
    },
    {
        "site": "fondmart",
        "group": "dropship",
        "url": "https://www.fondmart.com/new-arrivals/",
        "tags": ["h2", "h3", "a", "span", "img"],
        "attrs": ["alt", "title"],
    },
    {
        "site": "tasha",
        "group": "dropship",
        "url": "https://www.tashawholesale.com/new-arrivals",
        "tags": ["h2", "h3", "a", "img"],
        "attrs": ["alt", "title"],
    },
    {
        "site": "bloom",
        "group": "dropship",
        "url": "https://bloomdropship.com/collections/new-arrivals",
        "tags": ["h2", "h3", "a", "img"],
        "attrs": ["alt", "title"],
    },
    {
        "site": "banggood",
        "group": "dropship",
        "url": "https://www.banggood.com/Wholesale-Women-s-Clothing-c-11.html",
        "tags": ["h3", "a", "img"],
        "attrs": ["alt", "title"],
    },
    {
        "site": "lightinthebox",
        "group": "dropship",
        "url": "https://www.lightinthebox.com/c/women-clothing_0208/?sortBy=newsarrivals",
        "tags": ["h3", "a", "img"],
        "attrs": ["alt", "title"],
    },
    {
        "site": "eprolo",
        "group": "dropship",
        "url": "https://eprolo.com/product-category/clothing/",
        "tags": ["h2", "h3", "a", "img"],
        "attrs": ["alt", "title"],
    },
    {
        "site": "cjdropshipping",
        "group": "dropship",
        "url": "https://cjdropshipping.com/list/?categoryId=1&sortType=0",
        "tags": ["h3", "a", "img"],
        "attrs": ["alt", "title"],
    },
]


# ─── Keyword Loading ──────────────────────────────────────────────────────────
CUSTOM_PATH = Path(__file__).parent / "custom_keywords.json"

def load_all_keywords(dict_path: Path) -> list[str]:
    d = json.loads(dict_path.read_text())
    seen: set[str] = set()
    out: list[str] = []
    for lst in d.values():
        for kw in lst:
            if kw.lower() not in seen:
                seen.add(kw.lower())
                out.append(kw)
    # Merge user-submitted custom keywords
    if CUSTOM_PATH.exists():
        try:
            custom = json.loads(CUSTOM_PATH.read_text())
            for kw in custom:
                if kw.lower() not in seen:
                    seen.add(kw.lower())
                    out.append(kw)
        except Exception:
            pass
    return out


# ─── DB Helpers ───────────────────────────────────────────────────────────────
def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_counts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT NOT NULL,
            keyword      TEXT NOT NULL,
            count        INTEGER NOT NULL DEFAULT 0,
            site_source  TEXT NOT NULL,
            source_group TEXT NOT NULL DEFAULT 'retail',
            UNIQUE(date, keyword, site_source)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_summaries (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            month        TEXT NOT NULL,
            keyword      TEXT NOT NULL,
            avg_count    REAL NOT NULL,
            site_source  TEXT NOT NULL,
            source_group TEXT NOT NULL DEFAULT 'retail',
            UNIQUE(month, keyword, site_source)
        )
    """)
    conn.commit()
    return conn


def write_counts(conn: sqlite3.Connection, today: str, site: str, group: str,
                 counts: dict[str, int]) -> int:
    written = 0
    for kw, cnt in counts.items():
        conn.execute(
            """INSERT INTO daily_counts (date, keyword, count, site_source, source_group)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(date, keyword, site_source)
               DO UPDATE SET count = excluded.count""",
            (today, kw, cnt, site, group),
        )
        written += 1
    conn.commit()
    return written


# ─── Text Counting ────────────────────────────────────────────────────────────
def count_keywords(text: str, keywords: list[str]) -> dict[str, int]:
    t = text.lower()
    return {kw: t.count(kw.lower()) for kw in keywords}


# ─── RSS Scraper ──────────────────────────────────────────────────────────────
def scrape_rss(target: dict, keywords: list[str]) -> dict[str, int]:
    if not HAS_FEEDPARSER:
        log.warning("feedparser not installed — skipping RSS targets")
        return {}
    try:
        feed = feedparser.parse(target["url"])
        texts = []
        for entry in feed.entries[:60]:
            texts.append(getattr(entry, "title", "") or "")
            texts.append(getattr(entry, "summary", "") or "")
            content = getattr(entry, "content", [])
            if content:
                texts.append(content[0].get("value", "") or "")
        combined = " ".join(texts)
        counts = count_keywords(combined, keywords)
        total = sum(counts.values())
        log.info("  ✓ %s (RSS) — %d keyword hits, %d entries", target["site"], total, len(feed.entries))
        return counts
    except Exception as exc:
        log.warning("  ✗ %s (RSS) failed: %s", target["site"], exc)
        return {}


# ─── HTML Scraper ─────────────────────────────────────────────────────────────
def scrape_html(target: dict, keywords: list[str], session: requests.Session) -> dict[str, int]:
    try:
        resp = session.get(target["url"], headers=_headers(), timeout=20, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        texts = []
        # Tag text content
        for tag in target.get("tags", ["h3", "a", "img"]):
            for el in soup.find_all(tag):
                texts.append(el.get_text(" ", strip=True))
                # Also grab specified attributes (alt, title, aria-label)
                for attr in target.get("attrs", ["alt"]):
                    val = el.get(attr, "")
                    if val:
                        texts.append(val)

        combined = " ".join(texts)
        counts = count_keywords(combined, keywords)
        total = sum(counts.values())
        log.info("  ✓ %s — %d keyword hits across %d chars", target["site"], total, len(combined))
        return counts
    except requests.HTTPError as exc:
        log.warning("  ✗ %s HTTP %s — skipping", target["site"], exc.response.status_code)
        return {}
    except Exception as exc:
        log.warning("  ✗ %s failed: %s", target["site"], exc)
        return {}


# ─── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    keywords = load_all_keywords(DICT_PATH)
    conn     = init_db(DB_PATH)
    today    = date.today().isoformat()

    log.info(
        "Scrape starting — date=%s  keywords=%d  rss=%d  html=%d",
        today, len(keywords), len(RSS_TARGETS), len(HTML_TARGETS),
    )

    total_rows = 0
    session = requests.Session()

    # ── RSS feeds (media group) ───────────────────────────────────────────────
    for target in RSS_TARGETS:
        log.info("  → [media/RSS] %s", target["site"])
        counts = scrape_rss(target, keywords)
        if counts:
            total_rows += write_counts(conn, today, target["site"], target["group"], counts)
        time.sleep(random.uniform(0.5, 1.5))

    # ── HTML scraping (retail + dropship) ────────────────────────────────────
    for target in HTML_TARGETS:
        log.info("  → [%s/HTML] %s", target["group"], target["site"])
        counts = scrape_html(target, keywords, session)
        if counts:
            total_rows += write_counts(conn, today, target["site"], target["group"], counts)
        time.sleep(random.uniform(1.0, 3.0))

    conn.close()
    log.info("Scrape complete — %d rows written for %s", total_rows, today)

    if total_rows == 0:
        raise SystemExit(
            "ERROR: all targets returned 0 results — "
            "aborting to prevent overwriting trends.json with empty data"
        )


if __name__ == "__main__":
    main()
