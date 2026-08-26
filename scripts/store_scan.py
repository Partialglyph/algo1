"""Scan retail product pages for what they sell and what it costs.

    python scripts/store_scan.py URL [URL ...]
    python scripts/store_scan.py --file urls.txt --csv out.csv
    python scripts/store_scan.py --file urls.txt --json out.json
    python scripts/store_scan.py URL --find "monster energy"

You are responsible for confirming each site permits automated access.
robots.txt is respected unless you pass --ignore-robots.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storescan import scan as scanner  # noqa: E402


def load_urls(args) -> list[str]:
    urls = list(args.urls)
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
        urls += [ln.strip() for ln in text.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
    seen, unique = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def print_results(results, find: str | None) -> None:
    for r in results:
        head = r.url if len(r.url) <= 78 else r.url[:75] + "..."
        if not r.ok:
            print(f"\n{head}\n  FAILED  {r.error}")
            if r.hint:
                print(f"          {r.hint}")
            continue

        tag = "cached" if r.from_cache else "fetched"
        print(f"\n{head}\n  {tag} | {r.method} | {len(r.products)} product(s)")
        if r.hint:
            print(f"  {r.hint}")
        for p in r.products:
            price = "-" if p.price is None else f"{p.currency or ''}{p.price:.2f}".strip()
            avail = f"  [{p.availability}]" if p.availability else ""
            print(f"    {p.name[:56]:<58} {price:>10}{avail}")
            for note in p.notes:
                print(f"      note: {note}")

    if find:
        needle = find.lower()
        print(f"\n--- pages containing {find!r} ---")
        hits = 0
        for r in results:
            for p in r.products:
                if needle in p.name.lower():
                    price = "-" if p.price is None else f"{p.price:.2f}"
                    print(f"  YES  {price:>8}  {p.name[:44]:<46} {r.url[:44]}")
                    hits += 1
        if not hits:
            print("  no match in any scanned page")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="*", help="product or listing page URLs")
    ap.add_argument("--file", help="text file of URLs, one per line")
    ap.add_argument("--find", help="report whether a product name appears")
    ap.add_argument("--csv", help="write results to a CSV file")
    ap.add_argument("--json", help="write results to a JSON file")
    ap.add_argument("--no-cache", action="store_true", help="refetch instead of using the disk cache")
    ap.add_argument("--ignore-robots", action="store_true",
                    help="scan even where robots.txt disallows it (only with the site's permission)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")

    urls = load_urls(args)
    if not urls:
        ap.error("give at least one URL, or --file")

    if args.ignore_robots:
        print("WARNING: ignoring robots.txt. Only do this for sites that have "
              "given you permission.\n", file=sys.stderr)

    try:
        results = asyncio.run(scanner.scan(
            urls,
            respect_robots=not args.ignore_robots,
            use_cache=not args.no_cache,
        ))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print_results(results, args.find)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(scanner.to_csv_rows(results))
        print(f"\nwrote {args.csv}")
    if args.json:
        Path(args.json).write_text(scanner.to_json(results), encoding="utf-8")
        print(f"wrote {args.json}")

    found = sum(len(r.products) for r in results)
    failed = sum(1 for r in results if not r.ok)
    print(f"\n{len(urls)} url(s) | {found} product(s) | {failed} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
