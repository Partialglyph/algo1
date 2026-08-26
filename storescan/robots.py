"""robots.txt handling.

robots.txt is a convention, not a law, but ignoring it is the single clearest
piece of evidence of bad faith if anyone ever asks what your crawler did. It is
respected by default here and can only be overridden explicitly.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

_cache: dict[str, RobotFileParser | None] = {}


def origin_of(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


async def get_parser(client: httpx.AsyncClient, url: str, user_agent: str) -> RobotFileParser | None:
    """Fetch and cache robots.txt for the URL's origin. None if unavailable."""
    origin = origin_of(url)
    if origin in _cache:
        return _cache[origin]

    parser: RobotFileParser | None = None
    try:
        resp = await client.get(f"{origin}/robots.txt",
                                headers={"User-Agent": user_agent}, timeout=15.0)
        if resp.status_code == 200:
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
        elif resp.status_code in (401, 403):
            # A protected robots.txt conventionally means "stay out entirely".
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
        else:
            log.info("no robots.txt at %s (%s)", origin, resp.status_code)
    except httpx.HTTPError as exc:
        log.warning("could not fetch robots.txt for %s: %s", origin, exc)

    _cache[origin] = parser
    return parser


def allows(parser: RobotFileParser | None, url: str, user_agent: str) -> bool:
    """Absent robots.txt means no stated restriction, so allow."""
    if parser is None:
        return True
    return parser.can_fetch(user_agent, url)


def crawl_delay(parser: RobotFileParser | None, user_agent: str, default: float) -> float:
    if parser is None:
        return default
    try:
        stated = parser.crawl_delay(user_agent)
    except Exception:
        stated = None
    return max(float(stated), default) if stated else default
