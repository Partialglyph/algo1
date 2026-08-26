"""Run the FuelScope concept test from the command line.

    python scripts/fuel_probe.py                      # real OSM data, free
    python scripts/fuel_probe.py --source fixture     # synthetic sample
    python scripts/fuel_probe.py --source google      # billable
    python scripts/fuel_probe.py --compare            # cross-check a source against OSM
    python scripts/fuel_probe.py --source google --plan
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fuelscope import overpass, settings                      # noqa: E402
from fuelscope.classify import build_report                   # noqa: E402
from fuelscope.compare import build_comparison                # noqa: E402
from fuelscope.places import build_tiles, load_fixture, survey  # noqa: E402


async def run_survey(source, args):
    if source == "fixture":
        return load_fixture()
    if source == "osm":
        return await overpass.survey(args.lat, args.lon, args.radius)
    if not settings.GOOGLE_MAPS_API_KEY:
        raise SystemExit("GOOGLE_MAPS_API_KEY is not set")
    return await survey(args.lat, args.lon, args.radius, args.tile)


def print_report(report):
    cl = report["clustered"]
    print(f"source              : {report['source']}")
    print(f"raw features        : {report['total_places']}")
    print(f"sites after merging : {cl['sites']}  ({cl['merged_from_multiple_features']} built from >1 feature)")
    print()
    for category, count in cl["by_category"].items():
        print(f"  {category:<18}: {count}")
    print()
    print(f"store attach rate   : {cl['store_attach_rate']}")
    print(f"type disagreement   : {report['type_disagreement_rate']}")
    if report["coverage_is_suspect"]:
        print(f"WARNING             : {report['saturated_tiles']} tile(s) hit the 20-result cap")
    if report["disputed"]:
        print()
        print("Name contradicts the type tags:")
        for row in report["disputed"]:
            print(f"  {row['name']:<28} {row['category']:<16} {', '.join(row['flags'])}")


def print_comparison(cmp_):
    a, b = cmp_["sources"]["a"], cmp_["sources"]["b"]
    print(f"comparing           : {a} vs {b}")
    print(f"sites               : {a}={cmp_['a_sites']}  {b}={cmp_['b_sites']}")
    print(f"matched             : {cmp_['matched']}  "
          f"({int(cmp_['match_rate_a'] * 100)}% of {a}, {int(cmp_['match_rate_b'] * 100)}% of {b})")
    if not cmp_["comparable"]:
        print()
        print("NOT COMPARABLE      : nothing matched, so there is no agreement to report.")
        if cmp_.get("warning"):
            print(f"  {cmp_['warning']}")
        return
    print(f"fuel agreement      : {cmp_['fuel_agreement']}")
    print(f"store agreement     : {cmp_['store_agreement']}")
    print(f"store attach rate   : {cmp_['store_attach_rate']}")
    if cmp_["conflicts"]:
        print()
        print("Sites the two sources describe differently:")
        for c in cmp_["conflicts"]:
            print(f"  {(c['name'] or '(unnamed)'):<26} {a}={c[a + '_category']:<16} "
                  f"{b}={c[b + '_category']:<16} {c['distance_m']:.0f} m apart")
    only_b = cmp_["only_in_b"]
    if only_b:
        print()
        print(f"In {b} but not {a} (candidate gaps in {a}):")
        for s in only_b[:15]:
            print(f"  {(s['names'][0] if s['names'] else '(unnamed)'):<30} {s['lat']:.4f},{s['lon']:.4f}")
        if len(only_b) > 15:
            print(f"  ... and {len(only_b) - 15} more")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["osm", "fixture", "google"], default="osm")
    ap.add_argument("--compare", action="store_true", help="cross-check --source against OSM")
    ap.add_argument("--plan", action="store_true", help="show Google request count and exit")
    ap.add_argument("--lat", type=float, default=settings.DEFAULT_CENTER_LAT)
    ap.add_argument("--lon", type=float, default=settings.DEFAULT_CENTER_LON)
    ap.add_argument("--radius", type=float, default=settings.DEFAULT_SURVEY_RADIUS_M)
    ap.add_argument("--tile", type=float, default=settings.TILE_RADIUS_M)
    args = ap.parse_args()

    if args.plan:
        tiles = build_tiles(args.lat, args.lon, args.radius, args.tile)
        print(f"{len(tiles)} tiles -> {len(tiles)} billable Nearby Search requests")
        print("Overpass covers the same area in 1 free request.")
        return 0

    try:
        if args.compare:
            if args.source == "osm":
                raise SystemExit("--compare needs --source fixture or google")
            a = asyncio.run(run_survey(args.source, args))
            b = asyncio.run(overpass.survey(args.lat, args.lon, args.radius))
            cmp_ = build_comparison(a, b)
            if args.source == "fixture":
                cmp_["warning"] = ("The fixture is synthetic with invented coordinates, so it "
                                   "cannot match real OSM features.")
            print_comparison(cmp_)
        else:
            print_report(build_report(asyncio.run(run_survey(args.source, args))))
    except overpass.OverpassBusy as exc:
        print(f"Overpass unavailable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
