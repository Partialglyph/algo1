"""Pull product name / price / availability out of a retail page.

Four extractors, tried best-first. Every result records which one produced it,
because that is the main signal of how much to trust the row: structured data
the site published deliberately is worth far more than a regex over prose.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# Ordered best to worst. Confidence travels with the method, not the value.
METHODS = ("json-ld", "microdata", "opengraph", "heuristic")
CONFIDENCE = {"json-ld": 0.95, "microdata": 0.85, "opengraph": 0.7, "heuristic": 0.3}


@dataclass
class Product:
    name: str
    price_cents: int | None = None
    currency: str | None = None
    availability: str | None = None
    sku: str | None = None
    brand: str | None = None
    method: str = "heuristic"
    confidence: float = 0.3
    notes: list[str] = field(default_factory=list)

    @property
    def price(self) -> float | None:
        return None if self.price_cents is None else self.price_cents / 100


def parse_price(raw) -> int | None:
    """Money as integer cents. Floats cannot hold 2.49 exactly and the error
    compounds the moment you start comparing or averaging prices.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(round(float(raw) * 100))

    text = str(raw).strip()
    match = re.search(r"\d[\d.,  ]*", text)
    if not match:
        return None
    num = re.sub(r"[  ]", "", match.group(0)).rstrip(".,")

    # Both separators present: whichever comes last is the decimal point.
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        # "2,49" is a decimal comma; "1,234" is a thousands separator.
        num = num.replace(",", ".") if re.search(r",\d{1,2}$", num) else num.replace(",", "")

    try:
        return int(round(float(num) * 100))
    except ValueError:
        return None


def _clean(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id") or ""
    if isinstance(value, list):
        value = value[0] if value else ""
        return _clean(value)
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _last_segment(value) -> str | None:
    """schema.org availability arrives as a URL: .../InStock -> InStock."""
    text = _clean(value)
    return text.rstrip("/").split("/")[-1] if text else None


def _walk(node):
    """Yield every dict in an arbitrarily nested JSON-LD document."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _is_product(node: dict) -> bool:
    types = node.get("@type")
    types = [types] if isinstance(types, str) else (types or [])
    return any(str(t).lower().endswith("product") for t in types)


def _offer_fields(node: dict) -> tuple[int | None, str | None, str | None, list[str]]:
    """Offers may be one Offer, a list, or an AggregateOffer with a range."""
    offers = node.get("offers")
    notes: list[str] = []
    if not offers:
        return None, None, None, notes

    candidates = [o for o in _walk(offers) if isinstance(o, dict) and ("price" in o or "lowPrice" in o)]
    if not candidates:
        return None, None, None, notes

    if len(candidates) > 1:
        notes.append(f"{len(candidates)} offers, took the lowest")

    best_cents: int | None = None
    currency = availability = None
    for offer in candidates:
        cents = parse_price(offer.get("price", offer.get("lowPrice")))
        if cents is None:
            continue
        if "lowPrice" in offer and "price" not in offer:
            notes.append("price is a range low")
        if best_cents is None or cents < best_cents:
            best_cents = cents
            currency = _clean(offer.get("priceCurrency"))
            availability = _last_segment(offer.get("availability"))
    return best_cents, currency, availability, notes


def from_jsonld(soup: BeautifulSoup) -> list[Product]:
    found: list[Product] = []
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        try:
            doc = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        for node in _walk(doc):
            if not isinstance(node, dict) or not _is_product(node):
                continue
            name = _clean(node.get("name"))
            if not name:
                continue
            cents, currency, availability, notes = _offer_fields(node)
            found.append(Product(
                name=name,
                price_cents=cents,
                currency=currency,
                availability=availability,
                sku=_clean(node.get("sku")) or _clean(node.get("gtin13")),
                brand=_clean(node.get("brand")),
                method="json-ld",
                confidence=CONFIDENCE["json-ld"],
                notes=notes,
            ))
    return found


def _itemprop(scope, prop: str) -> str | None:
    node = scope.find(attrs={"itemprop": prop})
    if node is None:
        return None
    return _clean(node.get("content") or node.get("href") or node.get_text())


def from_microdata(soup: BeautifulSoup) -> list[Product]:
    found: list[Product] = []
    for scope in soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/Product", re.I)}):
        name = _itemprop(scope, "name")
        if not name:
            continue
        found.append(Product(
            name=name,
            price_cents=parse_price(_itemprop(scope, "price")),
            currency=_itemprop(scope, "priceCurrency"),
            availability=_last_segment(_itemprop(scope, "availability")),
            sku=_itemprop(scope, "sku"),
            brand=_itemprop(scope, "brand"),
            method="microdata",
            confidence=CONFIDENCE["microdata"],
        ))
    return found


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return _clean(tag["content"])
    return None


def from_opengraph(soup: BeautifulSoup) -> list[Product]:
    name = _meta(soup, "og:title")
    price = _meta(soup, "product:price:amount", "og:price:amount")
    if not name or price is None:
        return []
    return [Product(
        name=name,
        price_cents=parse_price(price),
        currency=_meta(soup, "product:price:currency", "og:price:currency"),
        availability=_last_segment(_meta(soup, "product:availability", "og:availability")),
        method="opengraph",
        confidence=CONFIDENCE["opengraph"],
    )]


_MONEY = re.compile(r"(?:CA\$|US\$|[$€£])\s?\d{1,4}(?:[.,]\d{2})?\b")


def from_heuristic(soup: BeautifulSoup) -> list[Product]:
    """Last resort: the page title plus the first currency-looking string.

    Deliberately low confidence. It will happily pick up shipping thresholds
    and crossed-out list prices, so treat every row as needing a human look.
    """
    heading = soup.find("h1")
    name = _clean(heading.get_text()) if heading else _clean(soup.title.get_text() if soup.title else None)
    if not name:
        return []
    match = _MONEY.search(soup.get_text(" ", strip=True))
    symbol = None
    if match:
        sym = re.match(r"(?:CA\$|US\$|[$€£])", match.group(0))
        symbol = sym.group(0) if sym else None
    return [Product(
        name=name,
        price_cents=parse_price(match.group(0)) if match else None,
        currency=symbol,
        method="heuristic",
        confidence=CONFIDENCE["heuristic"],
        notes=["name and price inferred from page text, not structured data"],
    )]


def looks_client_rendered(html: str, soup: BeautifulSoup) -> bool:
    """An SPA shell: lots of script, almost no text. Nothing to parse here."""
    text = soup.get_text(" ", strip=True)
    return len(text) < 600 and len(soup.find_all("script")) >= 3


def extract(html: str) -> tuple[list[Product], str]:
    """Return the best available products and the method that produced them."""
    soup = BeautifulSoup(html, "html.parser")
    for extractor, method in (
        (from_jsonld, "json-ld"),
        (from_microdata, "microdata"),
        (from_opengraph, "opengraph"),
        (from_heuristic, "heuristic"),
    ):
        products = extractor(soup)
        if products:
            return products, method
    return [], "none"
