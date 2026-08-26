"""Fetch a list of product URLs politely and extract what is on sale.

Politeness is the default and not a flag: one request at a time per host, a
real delay between them, an honest User-Agent, and a disk cache so re-running
the same scan costs the site nothing.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from . import robots
from .extract import Product, extract, looks_client_rendered

log = logging.getLogger(__name__)

USER_AGENT = os.getenv(
    "STORESCAN_USER_AGENT",
    "StoreScan/0.1 (personal price research; set STORESCAN_USER_AGENT with a contact address)",
)
MIN_DELAY_S = float(os.getenv("STORESCAN_DELAY_S", "2.0"))
CACHE_DIR = Path(os.getenv("STORESCAN_CACHE_DIR", ".storescan-cache"))
CACHE_TTL_S = 6 * 3600
MAX_URLS = int(os.getenv("STORESCAN_MAX_URLS", "200"))
TIMEOUT_S = 25.0


@dataclass
class ScanResult:
    url: str
    ok: bool
    status: int | None = None
    method: str = "none"
    products: list[Product] = field(default_factory=list)
    error: str | None = None
    from_cache: bool = False
    hint: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["products"] = [
            {**asdict(p), "price": p.price} for p in self.products
        ]
        return d


def _cache_path(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".html")


def _read_cache(url: str) -> str | None:
    path = _cache_path(url)
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > CACHE_TTL_S:
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _write_cache(url: str, html: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(html, encoding="utf-8", errors="replace")


async def scan_url(
    client: httpx.AsyncClient,
    url: str,
    respect_robots: bool = True,
    use_cache: bool = True,
) -> ScanResult:
    cached = _read_cache(url) if use_cache else None
    if cached is not None:
        products, method = extract(cached)
        return ScanResult(url=url, ok=True, status=200, method=method,
                          products=products, from_cache=True)

    if respect_robots:
        parser = await robots.get_parser(client, url, USER_AGENT)
        if not robots.allows(parser, url, USER_AGENT):
            return ScanResult(url=url, ok=False, error="blocked_by_robots",
                              hint="robots.txt disallows this path for our user agent")
        delay = robots.crawl_delay(parser, USER_AGENT, MIN_DELAY_S)
    else:
        delay = MIN_DELAY_S

    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT},
                                timeout=TIMEOUT_S, follow_redirects=True)
    except httpx.HTTPError as exc:
        return ScanResult(url=url, ok=False, error=f"request_failed: {exc}")

    if resp.status_code == 403:
        return ScanResult(
            url=url, ok=False, status=403, error="forbidden",
            hint="Often bot protection. Getting past it is out of scope - "
                 "check whether the site offers a data feed or API instead.")
    if resp.status_code != 200:
        return ScanResult(url=url, ok=False, status=resp.status_code, error="bad_status")

    html = resp.text
    if use_cache:
        _write_cache(url, html)

    products, method = extract(html)
    result = ScanResult(url=url, ok=True, status=200, method=method, products=products)

    if not products:
        from bs4 import BeautifulSoup
        if looks_client_rendered(html, BeautifulSoup(html, "html.parser")):
            result.hint = ("Page looks client-rendered - the products are loaded by "
                           "JavaScript after delivery. Look for the JSON endpoint the "
                           "page itself calls.")
        else:
            result.hint = "No structured product data found on the page."

    await asyncio.sleep(delay)   # pay the delay after the request, before the next
    return result


async def scan(
    urls: list[str],
    respect_robots: bool = True,
    use_cache: bool = True,
) -> list[ScanResult]:
    """Sequential on purpose - concurrency is what turns a scan into a problem."""
    if len(urls) > MAX_URLS:
        raise ValueError(f"{len(urls)} URLs exceeds the MAX_URLS guard of {MAX_URLS}")

    results: list[ScanResult] = []
    async with httpx.AsyncClient() as client:
        for url in urls:
            log.info("scanning %s", url)
            results.append(await scan_url(client, url, respect_robots, use_cache))
    return results


def to_json(results: list[ScanResult]) -> str:
    return json.dumps([r.to_dict() for r in results], indent=2)


def to_csv_rows(results: list[ScanResult]) -> list[list[str]]:
    rows = [["url", "product", "price", "currency", "availability",
             "sku", "brand", "method", "confidence"]]
    for r in results:
        if not r.products:
            rows.append([r.url, "", "", "", r.error or "no_products", "", "", r.method, ""])
        for p in r.products:
            rows.append([
                r.url, p.name,
                "" if p.price is None else f"{p.price:.2f}",
                p.currency or "", p.availability or "", p.sku or "", p.brand or "",
                p.method, f"{p.confidence:.2f}",
            ])
    return rows
