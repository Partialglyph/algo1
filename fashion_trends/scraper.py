"""
Fashion Trends Scraper
======================
Playwright headless scraper. Targets are defined in sources.py:
  - retail   : Zara, ASOS, SSENSE
  - media    : Vogue, Hypebae, Who What Wear
  - dropship : Trendsi, Spocket, CJ, FondMart, Tasha, Bloom, Banggood, LightInTheBox, Eprolo

Keywords are loaded from dictionary.json (all four lists merged for DB storage;
analysis splits them back out by type).

Usage:
    python -m fashion_trends.scraper
"""

import asyncio
import json
import logging
import random
import sqlite3
from datetime import date
from pathlib import Path

from playwright.async_api import async_playwright, Page

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

from .sources import ALL_TARGETS

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

# ─── User-Agent Pool ──────────────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


# ─── All tracked terms (merged) ───────────────────────────────────────────────
def load_all_keywords(dict_path: Path) -> list[str]:
    """Return deduplicated union of all keyword lists from dictionary.json."""
    d = json.loads(dict_path.read_text())
    seen: set[str] = set()
    out: list[str] = []
    for lst in d.values():
        for kw in lst:
            if kw.lower() not in seen:
                seen.add(kw.lower())
                out.append(kw)
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


# ─── Page Text Extraction ─────────────────────────────────────────────────────
async def extract_text(page: Page, target: dict) -> str:
    """Scroll to trigger lazy-loads then extract text from configured selectors."""
    for _ in range(target.get("scroll_passes", 2)):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
        await page.wait_for_timeout(random.randint(600, 1200))

    texts: list[str] = []

    for sel in target["selectors"]:
        try:
            elements = await page.query_selector_all(sel)
            for el in elements:
                if "img" in sel or sel.endswith("[alt]"):
                    val = (await el.get_attribute("alt")) or ""
                else:
                    val = (await el.inner_text()) or ""
                texts.append(val.strip())
        except Exception:
            pass

    # For media targets: also grab body paragraph text for richer language
    if target.get("body_text"):
        try:
            paras = await page.query_selector_all("p")
            for p in paras[:80]:   # cap to first 80 paragraphs
                val = (await p.inner_text()) or ""
                if len(val) > 20:  # skip navigation noise
                    texts.append(val.strip())
        except Exception:
            pass

    return " ".join(texts).lower()


# ─── Keyword Counter ──────────────────────────────────────────────────────────
def count_keywords(text: str, keywords: list[str]) -> dict[str, int]:
    return {kw: text.count(kw.lower()) for kw in keywords}


# ─── Per-site Scrape ──────────────────────────────────────────────────────────
async def scrape_target(browser, target: dict, keywords: list[str]) -> dict[str, int]:
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1280, "height": 900},
        locale="en-US",
    )
    page = await context.new_page()

    if HAS_STEALTH:
        await stealth_async(page)

    counts: dict[str, int] = {}
    try:
        log.info("  → [%s] %s ...", target["group"], target["site"])
        await page.goto(target["url"], wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(random.randint(1500, 3000))
        text = await extract_text(page, target)
        counts = count_keywords(text, keywords)
        total = sum(counts.values())
        log.info("  ✓ %s — %d keyword hits across %d chars of text", target["site"], total, len(text))
    except Exception as exc:
        log.warning("  ✗ %s failed: %s", target["site"], exc)
    finally:
        await context.close()

    return counts


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main() -> None:
    keywords = load_all_keywords(DICT_PATH)
    conn     = init_db(DB_PATH)
    today    = date.today().isoformat()

    log.info(
        "Scrape starting — date=%s  keywords=%d  targets=%d",
        today, len(keywords), len(ALL_TARGETS),
    )
    if HAS_STEALTH:
        log.info("playwright-stealth active")
    else:
        log.warning("playwright-stealth not installed — running without stealth mode")

    total_rows = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for target in ALL_TARGETS:
            counts = await scrape_target(browser, target, keywords)

            if counts:
                for kw, cnt in counts.items():
                    conn.execute(
                        """INSERT INTO daily_counts (date, keyword, count, site_source, source_group)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(date, keyword, site_source)
                           DO UPDATE SET count = excluded.count""",
                        (today, kw, cnt, target["site"], target["group"]),
                    )
                total_rows += len(counts)
                conn.commit()

            # Polite delay between sites
            await asyncio.sleep(random.uniform(2.0, 5.0))

        await browser.close()

    conn.close()
    log.info("Scrape complete — %d rows written for %s", total_rows, today)

    if total_rows == 0:
        raise SystemExit(
            "ERROR: all targets returned 0 results — "
            "aborting to prevent overwriting trends.json with empty data"
        )


if __name__ == "__main__":
    asyncio.run(main())
