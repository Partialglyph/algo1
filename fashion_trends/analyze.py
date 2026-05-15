"""
Fashion Trends Analyzer
=======================
Reads trends.db, computes 7-day moving average delta per keyword,
then writes trends.json to the repo root.

Also purges raw daily_counts rows older than 30 days, rolling them up
into monthly_summaries to keep the DB lean.

Usage:
    python -m fashion_trends.analyze
"""

import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DICT_PATH = Path(__file__).parent / "dictionary.json"
DB_PATH = ROOT / "trends.db"
OUT_PATH = ROOT / "trends.json"

# ─── Thresholds ───────────────────────────────────────────────────────────────
RISING_THRESHOLD = 15.0   # trend_pct > 15  → Rising
FADING_THRESHOLD = -15.0  # trend_pct < -15 → Fading

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("analyze")

# ─── Known site groups (fallback if source_group column missing) ──────────────
RETAIL_SITES = {"zara", "asos", "ssense"}
DROPSHIP_SITES = {
    "trendsi", "spocket", "cjdropshipping", "fondmart",
    "tasha", "bloom", "banggood", "lightinthebox", "eprolo",
}


# ─── DB Maintenance ───────────────────────────────────────────────────────────
def purge_old_rows(conn: sqlite3.Connection) -> None:
    """Roll up rows older than 30 days into monthly_summaries, then delete them."""
    cutoff = (date.today() - timedelta(days=30)).isoformat()

    conn.execute("""
        INSERT INTO monthly_summaries (month, keyword, avg_count, site_source, source_group)
        SELECT
            strftime('%Y-%m', date),
            keyword,
            AVG(count),
            site_source,
            source_group
        FROM daily_counts
        WHERE date < ?
        GROUP BY strftime('%Y-%m', date), keyword, site_source
        ON CONFLICT(month, keyword, site_source) DO UPDATE SET avg_count = excluded.avg_count
    """, (cutoff,))

    deleted = conn.execute(
        "DELETE FROM daily_counts WHERE date < ?", (cutoff,)
    ).rowcount
    conn.commit()
    if deleted:
        log.info("Purged %d old rows (pre-%s)", deleted, cutoff)


# ─── Core Analysis ────────────────────────────────────────────────────────────
def build_trends(conn: sqlite3.Connection, keywords: list[str]) -> list[dict]:
    today = date.today().isoformat()
    seven_ago = (date.today() - timedelta(days=7)).isoformat()

    results: list[dict] = []

    for kw in keywords:
        # ── Today's total and per-group counts ────────────────────────────────
        today_rows = conn.execute(
            "SELECT site_source, source_group, count FROM daily_counts WHERE date=? AND keyword=?",
            (today, kw),
        ).fetchall()

        today_count = sum(r[2] for r in today_rows)
        retail_count = sum(r[2] for r in today_rows if r[1] == "retail" or r[0] in RETAIL_SITES)
        dropship_count = sum(r[2] for r in today_rows if r[1] == "dropship" or r[0] in DROPSHIP_SITES)
        site_breakdown = {r[0]: r[2] for r in today_rows if r[2] > 0}

        # ── 7-day average (days prior to today) ───────────────────────────────
        hist_rows = conn.execute(
            """SELECT date, SUM(count)
               FROM daily_counts
               WHERE date >= ? AND date < ? AND keyword = ?
               GROUP BY date
               ORDER BY date""",
            (seven_ago, today, kw),
        ).fetchall()

        daily_totals = [r[1] for r in hist_rows]
        avg_7d = sum(daily_totals) / len(daily_totals) if daily_totals else None

        # On first days of data, use today's count as a baseline (no delta yet)
        if avg_7d is None or avg_7d == 0:
            trend_pct = 0.0
            avg_7d = float(today_count)
        else:
            trend_pct = ((today_count - avg_7d) / avg_7d) * 100

        # ── Flag ──────────────────────────────────────────────────────────────
        if trend_pct > RISING_THRESHOLD:
            flag = "Rising"
        elif trend_pct < FADING_THRESHOLD:
            flag = "Fading"
        else:
            flag = "Staple"

        # ── Sparkline: last 7 days including today ────────────────────────────
        spark_rows = conn.execute(
            """SELECT date, SUM(count)
               FROM daily_counts
               WHERE date >= ? AND keyword = ?
               GROUP BY date
               ORDER BY date""",
            (seven_ago, kw),
        ).fetchall()

        sparkline = [r[1] for r in spark_rows]
        while len(sparkline) < 7:
            sparkline.insert(0, 0)

        results.append({
            "keyword": kw,
            "flag": flag,
            "today_count": today_count,
            "retail_count": retail_count,
            "dropship_count": dropship_count,
            "avg_7d": round(avg_7d, 1),
            "trend_pct": round(trend_pct, 1),
            "sparkline": sparkline[-7:],
            "site_breakdown": site_breakdown,
            "group_breakdown": {
                "retail": retail_count,
                "dropship": dropship_count,
            },
        })

    # Sort by absolute velocity descending; within same velocity, volume breaks ties
    results.sort(key=lambda x: (abs(x["trend_pct"]), x["today_count"]), reverse=True)
    return results


# ─── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    keywords: list[str] = json.loads(DICT_PATH.read_text())

    if not DB_PATH.exists():
        log.error("trends.db not found at %s — run scraper.py first", DB_PATH)
        raise SystemExit(1)

    conn = sqlite3.connect(DB_PATH)

    purge_old_rows(conn)
    trends = build_trends(conn, keywords)
    conn.close()

    today = date.today().isoformat()
    rising  = sum(1 for t in trends if t["flag"] == "Rising")
    fading  = sum(1 for t in trends if t["flag"] == "Fading")
    staples = sum(1 for t in trends if t["flag"] == "Staple")

    output = {
        "generated_at": f"{today}T00:00:00Z",
        "scrape_date": today,
        "retail_sites": ["zara", "asos", "ssense"],
        "dropship_sites": [
            "trendsi", "spocket", "cjdropshipping", "fondmart",
            "tasha", "bloom", "banggood", "lightinthebox", "eprolo",
        ],
        "summary": {
            "rising_count": rising,
            "fading_count": fading,
            "staple_count": staples,
            "total_keywords": len(trends),
        },
        "keywords": trends,
    }

    OUT_PATH.write_text(json.dumps(output, indent=2))
    log.info(
        "trends.json written — %d rising, %d fading, %d staple (%d total)",
        rising, fading, staples, len(trends),
    )


if __name__ == "__main__":
    main()
