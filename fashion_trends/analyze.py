"""
Fashion Trends Analyzer
=======================
Reads trends.db and produces trends.json with three distinct sections:

  macro_trends   — retail + media sources; cultural/consumer momentum
  competitor_map — dropship sources only; supplier saturation signals
  materials      — material keywords across all sources; price/quality context

Usage:
    python -m fashion_trends.analyze
"""

import json
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .sources import DROPSHIP_SITE_NAMES, TOTAL_DROPSHIP_SOURCES

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
DICT_PATH = Path(__file__).parent / "dictionary.json"
DB_PATH   = ROOT / "trends.db"
OUT_PATH  = ROOT / "trends.json"

# ─── Thresholds ───────────────────────────────────────────────────────────────
RISING_THRESHOLD = 15.0
FADING_THRESHOLD = -15.0

# Lowered from 6/3 — meaningful against real scraped data volumes
SATURATED_THRESHOLD = 4   # site_count >= 4  → Saturated
GROWING_THRESHOLD   = 2   # site_count 2–3   → Growing
                           # site_count < 2   → Niche

# Hard-coded material price tier — replaces broken co-occurrence query
MATERIAL_PRICE_TIER: dict[str, str] = {
    # premium
    "cashmere": "premium", "silk": "premium", "velvet": "premium",
    "satin": "premium", "leather": "premium", "suede": "premium",
    "wool": "premium", "tweed": "premium", "organza": "premium",
    "tulle": "premium", "chiffon": "premium",
    # mid
    "linen": "mid", "cotton": "mid", "denim": "mid", "knit": "mid",
    "modal": "mid", "tencel": "mid", "lyocell": "mid", "bamboo": "mid",
    "jersey": "mid", "fleece": "mid", "sherpa": "mid",
    "faux leather": "mid", "rayon": "mid", "viscose": "mid",
    # budget
    "polyester": "budget", "nylon": "budget", "spandex": "budget",
    "elastane": "budget", "mesh": "budget", "acrylic": "budget",
}
PRICE_SCORE = {"premium": 2, "mid": 1, "budget": 0}

# Logging ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("analyze")


# ─── Dictionary Loading ───────────────────────────────────────────────────────
def load_dict(dict_path: Path) -> dict:
    return json.loads(dict_path.read_text())


# ─── DB Maintenance ───────────────────────────────────────────────────────────
def purge_old_rows(conn: sqlite3.Connection) -> None:
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    conn.execute("""
        INSERT INTO monthly_summaries (month, keyword, avg_count, site_source, source_group)
        SELECT strftime('%Y-%m', date), keyword, AVG(count), site_source, source_group
        FROM daily_counts
        WHERE date < ?
        GROUP BY strftime('%Y-%m', date), keyword, site_source
        ON CONFLICT(month, keyword, site_source) DO UPDATE SET avg_count = excluded.avg_count
    """, (cutoff,))
    deleted = conn.execute("DELETE FROM daily_counts WHERE date < ?", (cutoff,)).rowcount
    conn.commit()
    if deleted:
        log.info("Purged %d rows older than %s", deleted, cutoff)


# ─── Shared Helpers ───────────────────────────────────────────────────────────
def _velocity(today_count: int, avg_7d: float) -> float:
    """
    Velocity = % change vs 7-day average.
    When there is no prior history (avg_7d == 0), we treat a non-zero today
    as a positive signal: use a baseline of 1 so brand-new keywords always
    read as rising rather than flatlined at 0%.
    """
    baseline = avg_7d if avg_7d > 0 else 1.0
    return round(((today_count - baseline) / baseline) * 100, 1)


def _flag(trend_pct: float) -> str:
    if trend_pct > RISING_THRESHOLD:
        return "Rising"
    if trend_pct < FADING_THRESHOLD:
        return "Fading"
    return "Staple"


def _sparkline(conn: sqlite3.Connection, keyword: str, seven_ago: str) -> list[int]:
    rows = conn.execute(
        """SELECT date, SUM(count)
           FROM daily_counts
           WHERE date >= ? AND keyword = ?
           GROUP BY date ORDER BY date""",
        (seven_ago, keyword),
    ).fetchall()
    spark = [r[1] for r in rows]
    while len(spark) < 7:
        spark.insert(0, 0)
    return spark[-7:]


def _avg_7d(conn: sqlite3.Connection, keyword: str, today: str, seven_ago: str,
             groups: list[str] | None = None) -> float:
    if groups:
        placeholders = ",".join("?" * len(groups))
        rows = conn.execute(
            f"""SELECT SUM(count) FROM daily_counts
                WHERE date >= ? AND date < ? AND keyword = ?
                AND source_group IN ({placeholders})
                GROUP BY date""",
            (seven_ago, today, keyword, *groups),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT SUM(count) FROM daily_counts
               WHERE date >= ? AND date < ? AND keyword = ?
               GROUP BY date""",
            (seven_ago, today, keyword),
        ).fetchall()
    totals = [r[0] for r in rows if r[0]]
    return round(sum(totals) / len(totals), 1) if totals else 0.0


# ─── Panel 1: Macro Trends ────────────────────────────────────────────────────
def build_macro_trends(conn: sqlite3.Connection, trend_keywords: list[str],
                       today: str, seven_ago: str) -> list[dict]:
    results = []

    # Pre-fetch all today's retail+media counts so we can normalise sentiment
    all_counts = conn.execute(
        """SELECT keyword, SUM(count) FROM daily_counts
           WHERE date = ? AND source_group IN ('retail','media')
           GROUP BY keyword""",
        (today,),
    ).fetchall()
    # max mention count today — used to derive a relative sentiment score
    max_count = max((r[1] for r in all_counts), default=1) or 1
    count_map = {r[0]: r[1] for r in all_counts}

    for kw in trend_keywords:
        rows = conn.execute(
            """SELECT site_source, source_group, count FROM daily_counts
               WHERE date = ? AND keyword = ? AND source_group IN ('retail','media')""",
            (today, kw),
        ).fetchall()

        today_count  = sum(r[2] for r in rows)
        retail_count = sum(r[2] for r in rows if r[1] == "retail")
        media_count  = sum(r[2] for r in rows if r[1] == "media")

        # Skip keywords with zero mentions — they add nothing to charts
        if today_count == 0:
            continue

        avg = _avg_7d(conn, kw, today, seven_ago, groups=["retail", "media"])
        trend_pct = _velocity(today_count, avg)

        # Sentiment: relative mention volume (this keyword vs top keyword today)
        # Produces a meaningful 0–1 spread rather than a binary ratio
        sentiment_score = round(min(today_count / max_count, 1.0), 3)

        composite = round(sentiment_score * abs(trend_pct), 2)

        results.append({
            "keyword": kw,
            "flag": _flag(trend_pct),
            "trend_pct": trend_pct,
            "sentiment_score": sentiment_score,
            "composite_score": composite,
            "today_count": today_count,
            "avg_7d": avg,
            "sparkline": _sparkline(conn, kw, seven_ago),
            "source_breakdown": {"retail": retail_count, "media": media_count},
        })

    # Sort by composite score descending, fall back to today_count
    results.sort(key=lambda x: (x["composite_score"], x["today_count"]), reverse=True)
    return results


# ─── Panel 2: Competitor Saturation Map ──────────────────────────────────────
def build_competitor_map(conn: sqlite3.Connection, trend_keywords: list[str],
                         today: str, seven_ago: str) -> list[dict]:
    results = []
    total_suppliers = TOTAL_DROPSHIP_SOURCES

    for kw in trend_keywords:
        rows = conn.execute(
            """SELECT site_source, count FROM daily_counts
               WHERE date = ? AND keyword = ? AND source_group = 'dropship'""",
            (today, kw),
        ).fetchall()

        site_breakdown   = {r[0]: r[1] for r in rows if r[1] > 0}
        site_count       = len(site_breakdown)
        saturation_score = sum(site_breakdown.values())

        # Skip keywords with zero dropship presence
        if saturation_score == 0:
            continue

        avg = _avg_7d(conn, kw, today, seven_ago, groups=["dropship"])
        trend_pct = _velocity(saturation_score, avg)

        if site_count >= SATURATED_THRESHOLD:
            sat_flag = "Saturated"
        elif site_count >= GROWING_THRESHOLD:
            sat_flag = "Growing"
        else:
            sat_flag = "Niche"

        results.append({
            "keyword": kw,
            "flag": sat_flag,
            "saturation_score": saturation_score,
            "trend_pct": trend_pct,
            "site_count": site_count,
            "total_suppliers": total_suppliers,
            "avg_7d": avg,
            "sparkline": _sparkline(conn, kw, seven_ago),
            "site_breakdown": site_breakdown,
        })

    results.sort(key=lambda x: x["saturation_score"], reverse=True)
    return results


# ─── Panel 3: Materials Intelligence ─────────────────────────────────────────
def build_materials(conn: sqlite3.Connection, material_keywords: list[str],
                    material_qualifiers: list[str], today: str, seven_ago: str) -> list[dict]:
    results = []

    # Quality signals: qualifiers that are neither premium nor budget indicators
    TIER_WORDS = {"premium", "luxury", "budget", "cheap", "affordable",
                  "high quality", "low quality", "cashmere"}
    quality_signals = [q for q in material_qualifiers if q.lower() not in TIER_WORDS]

    for mat in material_keywords:
        rows = conn.execute(
            """SELECT source_group, SUM(count) FROM daily_counts
               WHERE date = ? AND keyword = ?
               GROUP BY source_group""",
            (today, mat),
        ).fetchall()

        today_count   = sum(r[1] for r in rows)
        src_breakdown = {r[0]: r[1] for r in rows}

        # Skip materials with no mentions
        if today_count == 0:
            continue

        avg = _avg_7d(conn, mat, today, seven_ago)
        trend_pct = _velocity(today_count, avg)

        # Price tier from hard-coded lookup — much more reliable than co-occurrence
        price_tier  = MATERIAL_PRICE_TIER.get(mat.lower(), "mid")
        price_score = PRICE_SCORE[price_tier]

        # Top quality signals: pick qualifier words with most mentions today
        qual_counts = []
        for q in quality_signals:
            cnt = conn.execute(
                "SELECT SUM(count) FROM daily_counts WHERE date=? AND keyword=?",
                (today, q)
            ).fetchone()[0] or 0
            if cnt > 0:
                qual_counts.append((q, cnt))
        qual_counts.sort(key=lambda x: x[1], reverse=True)
        top_signals = [q for q, _ in qual_counts[:3]]

        results.append({
            "material": mat,
            "flag": _flag(trend_pct),
            "trend_pct": trend_pct,
            "today_count": today_count,
            "avg_7d": avg,
            "price_tier": price_tier,
            "price_score": price_score,
            "quality_signals": top_signals,
            "sparkline": _sparkline(conn, mat, seven_ago),
            "source_breakdown": src_breakdown,
        })

    results.sort(key=lambda x: x["trend_pct"], reverse=True)
    return results


# ─── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    if not DB_PATH.exists():
        log.error("trends.db not found at %s — run scraper.py first", DB_PATH)
        raise SystemExit(1)

    d              = load_dict(DICT_PATH)
    trend_kws      = d["trend_keywords"]
    material_kws   = d["material_keywords"]
    mat_qualifiers = d["material_qualifiers"]

    conn      = sqlite3.connect(DB_PATH)
    today     = date.today().isoformat()
    seven_ago = (date.today() - timedelta(days=7)).isoformat()

    purge_old_rows(conn)

    log.info("Building macro trends (%d keywords)...", len(trend_kws))
    macro = build_macro_trends(conn, trend_kws, today, seven_ago)

    log.info("Building competitor map (%d keywords)...", len(trend_kws))
    competitor = build_competitor_map(conn, trend_kws, today, seven_ago)

    log.info("Building materials intel (%d materials)...", len(material_kws))
    materials = build_materials(conn, material_kws, mat_qualifiers, today, seven_ago)

    conn.close()

    macro_rising = sum(1 for m in macro      if m["flag"] == "Rising")
    macro_fading = sum(1 for m in macro      if m["flag"] == "Fading")
    saturated    = sum(1 for c in competitor if c["flag"] == "Saturated")
    mat_rising   = sum(1 for m in materials  if m["flag"] == "Rising")

    output = {
        "generated_at": f"{today}T00:00:00Z",
        "scrape_date":  today,
        "summary": {
            "macro_rising":     macro_rising,
            "macro_fading":     macro_fading,
            "saturated_niches": saturated,
            "material_rising":  mat_rising,
        },
        "macro_trends":   macro,
        "competitor_map": competitor,
        "materials":      materials,
    }

    OUT_PATH.write_text(json.dumps(output, indent=2))
    log.info(
        "trends.json written — macro: %d rising / %d fading | "
        "competitor: %d saturated | materials: %d rising",
        macro_rising, macro_fading, saturated, mat_rising,
    )


if __name__ == "__main__":
    main()
