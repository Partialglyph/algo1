from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass

from .places import Place, SurveyResult

FUEL_TYPE = "gas_station"
STORE_TYPE = "convenience_store"

# A place carrying one of these alongside convenience_store is almost always a
# grocery run, not a snack-and-fuel stop.
GROCERY_TYPES = {"supermarket", "grocery_store", "discount_supermarket", "food_store"}

# Types that carry no information — nearly every business has them.
GENERIC_TYPES = {"store", "food", "point_of_interest", "establishment", "finance"}

# Name-based ground truth, used only to *audit* the type tags, never to
# overwrite them. If these two signals disagree, that is the finding.
FUEL_BRANDS = [
    "petro-canada", "petrocanada", "petro canada", "shell", "esso", "chevron",
    "husky", "mobil", "co-op", "co op", "coop gas", "super save", "race trac",
    "racetrac", "7-eleven gas", "costco gas", "fas gas", "domo", "centex",
    "ultramar", "irving", "pioneer", "canadian tire gas",
]
STORE_BRANDS = [
    "7-eleven", "7 eleven", "seven eleven", "circle k", "mac's", "macs convenience",
    "petro-pass", "on the run", "extramile", "corner store", "convenience",
    "mini mart", "minimart", "food mart", "foodmart",
]

CATEGORIES = ("fuel_with_store", "fuel_only", "store_only", "unclassified")


def _name_matches(name: str, brands: list[str]) -> str | None:
    lowered = re.sub(r"\s+", " ", name.lower())
    for brand in brands:
        if brand in lowered:
            return brand
    return None


@dataclass
class Classified:
    place_id: str
    name: str
    address: str | None
    lat: float
    lon: float
    category: str
    types: list[str]
    primary_type: str | None
    flags: list[str]

    @property
    def is_disputed(self) -> bool:
        return any(f.startswith("disagree_") for f in self.flags)


def classify(place: Place) -> Classified:
    types = set(place.types)
    has_fuel = FUEL_TYPE in types
    has_store = STORE_TYPE in types

    if has_fuel and has_store:
        category = "fuel_with_store"
    elif has_fuel:
        category = "fuel_only"
    elif has_store:
        category = "store_only"
    else:
        category = "unclassified"

    flags: list[str] = []

    # The audit: does the name agree with the tags?
    fuel_brand = _name_matches(place.name, FUEL_BRANDS)
    store_brand = _name_matches(place.name, STORE_BRANDS)
    if fuel_brand and not has_fuel:
        flags.append(f"disagree_name_says_fuel:{fuel_brand}")
    if store_brand and not has_store:
        flags.append(f"disagree_name_says_store:{store_brand}")

    if has_store and (types & GROCERY_TYPES):
        flags.append("grocery_overlap")
    if not (types - GENERIC_TYPES):
        flags.append("generic_types_only")
    if place.business_status and place.business_status != "OPERATIONAL":
        flags.append(f"not_operational:{place.business_status.lower()}")

    return Classified(
        place_id=place.place_id,
        name=place.name,
        address=place.address,
        lat=place.lat,
        lon=place.lon,
        category=category,
        types=place.types,
        primary_type=place.primary_type,
        flags=flags,
    )


def build_report(result: SurveyResult) -> dict:
    """Turn a survey into the answer the concept test is asking for.

    The number that matters is `type_disagreement_rate`: how often Google's
    type tags contradict what the business name plainly says. That is the
    ceiling on how far you can trust `types` without a human in the loop.
    """
    rows = [classify(p) for p in result.places]

    by_category = Counter(r.category for r in rows)
    flag_counts = Counter(f.split(":")[0] for r in rows for f in r.flags)
    disputed = [r for r in rows if r.is_disputed]

    total = len(rows) or 1
    fuel_sites = by_category["fuel_with_store"] + by_category["fuel_only"]

    # Raw feature counts understate a source that splits one forecourt across
    # several features, so report the merged view alongside. Imported here to
    # keep the module-level dependency one-directional.
    from .compare import cluster

    sites = cluster(result.places, result.source)
    site_categories = Counter(s.category for s in sites)
    site_fuel = site_categories["fuel_with_store"] + site_categories["fuel_only"]

    return {
        "source": result.source,
        "clustered": {
            "sites": len(sites),
            "merged_from_multiple_features": sum(1 for s in sites if len(s.members) > 1),
            "by_category": {c: site_categories[c] for c in CATEGORIES},
            "store_attach_rate": round(
                site_categories["fuel_with_store"] / site_fuel, 3
            ) if site_fuel else None,
        },
        "tiles_requested": result.tiles_requested,
        "coverage_is_suspect": result.coverage_is_suspect,
        "saturated_tiles": len(result.saturated_tiles),
        "total_places": len(rows),
        "by_category": {c: by_category[c] for c in CATEGORIES},
        "fuel_sites": fuel_sites,
        "store_attach_rate": round(
            by_category["fuel_with_store"] / fuel_sites, 3
        ) if fuel_sites else None,
        "type_disagreement_rate": round(len(disputed) / total, 3),
        "flag_counts": dict(flag_counts),
        "disputed": [asdict(r) for r in disputed],
        "places": [asdict(r) for r in rows],
    }
