"""
Fashion Trends Scraper
======================
Playwright headless scraper targeting new-arrivals / catalog pages on:
  - Group A (Retail Stores): Zara, ASOS, SSENSE
  - Group B (Dropship Suppliers): Trendsi, Spocket, CJ Dropshipping, Modalyst,
    FondMart, Tasha Apparel, Bloom Dropship, Banggood, LightInTheBox, Eprolo

Counts keyword mentions per site per day and writes to trends.db (SQLite).
Idempotent: re-running for the same date overwrites existing rows.

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

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DICT_PATH = Path(__file__).parent / "dictionary.json"
DB_PATH = ROOT / "trends.db"

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

# ─── Target Definitions ───────────────────────────────────────────────────────
TARGETS = [
    # ── Group A: Retail Stores ────────────────────────────────────────────────
    {
        "site": "zara",
        "group": "retail",
        "url": "https://www.zara.com/us/en/woman-new-in-l1180.html",
        "selectors": [
            ".product-grid-product-info__name",
            "h2.product-grid-product-info__name",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 3,
    },
    {
        "site": "asos",
        "group": "retail",
        "url": "https://www.asos.com/women/new-in/new-in-clothing/cat/?cid=2623&sort=freshness",
        "selectors": [
            "[data-auto-id='productTitle']",
            "h3",
            "article h2",
            "img[alt]",
        ],
        "scroll_passes": 3,
    },
    {
        "site": "ssense",
        "group": "retail",
        "url": "https://www.ssense.com/en-us/women/new-arrivals",
        "selectors": [
            ".product-tile__description",
            ".product-tile__name",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
    # ── Group B: Dropship Suppliers ───────────────────────────────────────────
    {
        "site": "trendsi",
        "group": "dropship",
        "url": "https://www.trendsi.com/collections/new-arrivals",
        "selectors": [
            ".product-card__title",
            "h2.product-card__title",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 3,
    },
    {
        "site": "spocket",
        "group": "dropship",
        "url": "https://www.spocket.co/products?category=clothing&sort=newest",
        "selectors": [
            ".product-name",
            ".product-title",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
    {
        "site": "cjdropshipping",
        "group": "dropship",
        "url": "https://cjdropshipping.com/list/?categoryId=1&sortType=0",
        "selectors": [
            ".goods-name",
            ".product-name",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
    {
        "site": "fondmart",
        "group": "dropship",
        "url": "https://www.fondmart.com/new-arrivals/",
        "selectors": [
            ".product-name",
            ".item-title",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
    {
        "site": "tasha",
        "group": "dropship",
        "url": "https://www.tashawholesale.com/new-arrivals",
        "selectors": [
            ".product-title",
            ".grid-product__title",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
    {
        "site": "bloom",
        "group": "dropship",
        "url": "https://bloomdropship.com/collections/new-arrivals",
        "selectors": [
            ".product-item__title",
            ".grid-product__title",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
    {
        "site": "banggood",
        "group": "dropship",
        "url": "https://www.banggood.com/Wholesale-Women-s-Clothing-c-11.html",
        "selectors": [
            ".product-title",
            ".title",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
    {
        "site": "lightinthebox",
        "group": "dropship",
        "url": "https://www.lightinthebox.com/c/women-clothing_0208/?sortBy=newsarrivals",
        "selectors": [
            ".product-name",
            ".title",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
    {
        "site": "eprolo",
        "group": "dropship",
        "url": "https://eprolo.com/product-category/clothing/",
        "selectors": [
            ".woocommerce-loop-product__title",
            "h2.product-title",
            "h3",
            "img[alt]",
        ],
        "scroll_passes": 2,
    },
]


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
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            month       TEXT NOT NULL,
            keyword     TEXT NOT NULL,
            avg_count   REAL NOT NULL,
            site_source TEXT NOT NULL,
            source_group TEXT NOT NULL DEFAULT 'retail',
            UNIQUE(month, keyword, site_source)
        )
    """)
    conn.commit()
    return conn


# ─── Page Text Extraction ─────────────────────────────────────────────────────
async def extract_text(page: Page, selectors: list[str], scroll_passes: int) -> str:
    """Scroll page to trigger lazy-loads, then extract text from all selectors."""
    # Scroll in passes to load lazy content
    for _ in range(scroll_passes):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
        await page.wait_for_timeout(random.randint(600, 1200))

    texts: list[str] = []
    for sel in selectors:
        try:
            elements = await page.query_selector_all(sel)
            for el in elements:
                if sel.endswith("[alt]") or "img" in sel:
                    val = await el.get_attribute("alt") or ""
                else:
                    val = await el.inner_text() or ""
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
        log.info("  → %s (%s) ...", target["site"], target["url"])
        await page.goto(
            target["url"],
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        # Extra wait for JS-heavy pages
        await page.wait_for_timeout(random.randint(1500, 3000))

        text = await extract_text(page, target["selectors"], target["scroll_passes"])
        counts = count_keywords(text, keywords)
        total = sum(counts.values())
        log.info("  ✓ %s — %d keyword hits across %d chars", target["site"], total, len(text))
    except Exception as exc:
        log.warning("  ✗ %s failed: %s", target["site"], exc)
    finally:
        await context.close()

    return counts


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main() -> None:
    keywords: list[str] = json.loads(DICT_PATH.read_text())
    conn = init_db(DB_PATH)
    today = date.today().isoformat()

    log.info("Starting scrape for %s — %d keywords, %d targets", today, len(keywords), len(TARGETS))

    if HAS_STEALTH:
        log.info("playwright-stealth active")
    else:
        log.warning("playwright-stealth not installed — scraping without stealth mode")

    total_rows = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for target in TARGETS:
            counts = await scrape_target(browser, target, keywords)

            if counts:
                for kw, cnt in counts.items():
                    conn.execute(
                        """INSERT INTO daily_counts (date, keyword, count, site_source, source_group)
                           VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(date, keyword, site_source)
                           DO UPDATE SET count=excluded.count""",
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
            "ERROR: scraper returned 0 results for all targets — "
            "aborting to prevent empty trends.json commit"
        )


if __name__ == "__main__":
    asyncio.run(main())
