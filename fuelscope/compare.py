from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from . import settings
from .places import Place, SurveyResult

# OSM often models one forecourt as several features - an amenity=fuel node and
# a separate shop=convenience node a few metres away. Collapse those before
# comparing anything, or every split site reads as a false disagreement.
CLUSTER_RADIUS_M = 60.0

_EARTH_R = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R * math.asin(math.sqrt(h))


@dataclass
class Site:
    """One physical location, after merging co-located features."""
    source: str
    lat: float
    lon: float
    names: list[str] = field(default_factory=list)
    has_fuel: bool = False
    has_store: bool = False
    members: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.names[0] if self.names else ""

    @property
    def category(self) -> str:
        if self.has_fuel and self.has_store:
            return "fuel_with_store"
        if self.has_fuel:
            return "fuel_only"
        if self.has_store:
            return "store_only"
        return "unclassified"


def cluster(places: list[Place], source: str, radius_m: float = CLUSTER_RADIUS_M) -> list[Site]:
    """Single-linkage merge of features that sit on top of each other."""
    sites: list[Site] = []
    for p in places:
        target = None
        for s in sites:
            if haversine_m(p.lat, p.lon, s.lat, s.lon) <= radius_m:
                target = s
                break
        if target is None:
            target = Site(source=source, lat=p.lat, lon=p.lon)
            sites.append(target)
        # Capability is a union: if any member sells fuel, the site sells fuel.
        target.has_fuel = target.has_fuel or "gas_station" in p.types
        target.has_store = target.has_store or "convenience_store" in p.types
        if p.name and p.name not in target.names:
            target.names.append(p.name)
        target.members.append(p.place_id)
        n = len(target.members)
        target.lat += (p.lat - target.lat) / n
        target.lon += (p.lon - target.lon) / n
    return sites


def match(
    a_sites: list[Site],
    b_sites: list[Site],
    radius_m: float = settings.CROSS_MATCH_RADIUS_M,
) -> tuple[list[tuple[Site, Site, float]], list[Site], list[Site]]:
    """Greedy nearest-neighbour pairing, closest pairs claimed first."""
    candidates: list[tuple[float, int, int]] = []
    for i, a in enumerate(a_sites):
        for j, b in enumerate(b_sites):
            d = haversine_m(a.lat, a.lon, b.lat, b.lon)
            if d <= radius_m:
                candidates.append((d, i, j))
    candidates.sort()

    used_a: set[int] = set()
    used_b: set[int] = set()
    pairs: list[tuple[Site, Site, float]] = []
    for d, i, j in candidates:
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((a_sites[i], b_sites[j], d))

    only_a = [s for i, s in enumerate(a_sites) if i not in used_a]
    only_b = [s for j, s in enumerate(b_sites) if j not in used_b]
    return pairs, only_a, only_b


def _attach_rate(sites: list[Site]) -> float | None:
    fuel = [s for s in sites if s.has_fuel]
    if not fuel:
        return None
    return round(sum(1 for s in fuel if s.has_store) / len(fuel), 3)


def build_comparison(a: SurveyResult, b: SurveyResult) -> dict:
    """Cross-source agreement between two independent surveys of one area.

    The point is that a single source cannot tell you whether it is wrong.
    Two sources disagreeing localises the error even when neither is truth.
    """
    a_sites = cluster(a.places, a.source)
    b_sites = cluster(b.places, b.source)
    pairs, only_a, only_b = match(a_sites, b_sites)

    fuel_agree = store_agree = 0
    conflicts: list[dict] = []
    for sa, sb, d in pairs:
        f_ok = sa.has_fuel == sb.has_fuel
        s_ok = sa.has_store == sb.has_store
        fuel_agree += f_ok
        store_agree += s_ok
        if not (f_ok and s_ok):
            conflicts.append({
                "name": sa.name or sb.name,
                "distance_m": round(d, 1),
                "lat": sa.lat, "lon": sa.lon,
                f"{a.source}_category": sa.category,
                f"{b.source}_category": sb.category,
                "fuel_agrees": f_ok,
                "store_agrees": s_ok,
            })

    # With nothing matched there is no agreement to report. Reporting 0.0 here
    # would read as total disagreement rather than "no comparison happened".
    n = len(pairs)
    return {
        "sources": {"a": a.source, "b": b.source},
        "comparable": n > 0,
        "a_sites": len(a_sites),
        "b_sites": len(b_sites),
        "matched": n,
        "match_rate_a": round(n / (len(a_sites) or 1), 3),
        "match_rate_b": round(n / (len(b_sites) or 1), 3),
        "fuel_agreement": round(fuel_agree / n, 3) if n else None,
        "store_agreement": round(store_agree / n, 3) if n else None,
        "store_attach_rate": {a.source: _attach_rate(a_sites), b.source: _attach_rate(b_sites)},
        "only_in_a": [asdict(s) for s in only_a],
        "only_in_b": [asdict(s) for s in only_b],
        "conflicts": conflicts,
        "matched_pairs": [
            {"a": asdict(sa), "b": asdict(sb), "distance_m": round(d, 1)}
            for sa, sb, d in pairs
        ],
    }
