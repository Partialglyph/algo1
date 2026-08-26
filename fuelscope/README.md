# FuelScope — concept test

One question: **can a map provider's category tags identify fuel stations and
convenience stores well enough to seed a price app, without a human checking every row?**

Two independent sources answer it: Google Places and OpenStreetMap.

Everything else from the larger build plan (PostGIS, crowdsourced prices,
consensus, reputation) is deliberately absent. This answers the data question first.

## Run it

Real data, no key, no billing — OpenStreetMap via Overpass:

```bash
python scripts/fuel_probe.py
```

The synthetic sample, for offline work:

```bash
python scripts/fuel_probe.py --source fixture
```

Cross-check a source against OSM:

```bash
python scripts/fuel_probe.py --source google --compare
```

API + web view:

```bash
python -m uvicorn fuelscope.api:app --port 8000
```

```bash
cd frontend && python -m http.server 5501
```

Then open `http://localhost:5501/fuel.html`.

## Going live

Set `GOOGLE_MAPS_API_KEY` (Places API enabled), preview the cost, then run:

```bash
python scripts/fuel_probe.py --plan
```

```bash
python scripts/fuel_probe.py --live
```

Every tile is one billable Nearby Search request. `--plan` prints the count
before you spend anything; `MAX_TILES_PER_SURVEY` in `settings.py` is a hard stop.

## Sources

| Source | Cost | Coverage of one 4 km radius | Licence |
|---|---|---|---|
| `osm` | free, keyless | 1 request | ODbL, attribution required |
| `google` | billable | 61 tiled requests | Places terms, place ID only may be stored |
| `fixture` | free | n/a | synthetic, invented coordinates |

Overpass answers a real spatial query, so one call covers the area. Nearby Search
returns at most 20 results per call, which is why the Google path needs 61 requests
to cover what OSM does in one.

## What the two sources disagree about

Measured over a 4 km radius on Coquitlam:

- OSM records **13 fuel stations**, but marks only **2** as having a convenience
  store — a 15% attach rate, against roughly two thirds in reality. OSM is good at
  *where the stations are* and poor at *whether they have a shop*.
- OSM also **splits one forecourt across several features**: an `amenity=fuel` node
  and a separate `shop=convenience` node metres apart. Three such pairs appear in
  Coquitlam, including an Esso whose shop is a separate "Tiger Express" node 11 m away.
  Comparing raw features without merging them first turns every split site into a
  false disagreement, so `compare.cluster()` merges anything within 60 m and takes
  the union of its capabilities.

This is the case for two sources rather than one: a single source cannot tell you
where it is wrong. Google collapses a forecourt into one place with both types;
OSM models it as separate objects and often omits the shop entirely. Neither is
truth, but where they disagree is where a human should look.

## How it works

`places.py` — Nearby Search caps out at 20 results per call, so an area is covered
by a grid of overlapping circles rather than one large one. Results are deduped by
place id. Any tile returning a full 20 is recorded as *saturated*: results were
probably hidden behind the cap and the tile radius needs shrinking.

`overpass.py` — one Overpass QL query for `amenity=fuel`, `shop=convenience`, and
`shop=kiosk`, normalised into the same `Place` shape as Google results so a single
classifier scores both. Responses are cached for an hour and 429/504 falls back to
stale cache, because the public endpoint is donated capacity that throttles bursts
quickly — expect intermittent failures and do not run it in a loop.

`compare.py` — clusters co-located features into sites, matches sites across the two
sources by proximity, and reports where they agree. Sites present in one source and
not the other are the interesting output: they are candidate gaps in whichever
source is missing them.

`classify.py` — sorts each place into `fuel_with_store`, `fuel_only`, `store_only`,
or `unclassified` from the `gas_station` / `convenience_store` type tags, then
**audits those tags against the business name**. A place called "Esso" with no
`gas_station` tag is flagged `disagree_name_says_fuel`. The headline number is
`type_disagreement_rate` — the share of places where the tags and the name tell
different stories. That is the ceiling on how far you can trust `types` unattended.

Other flags: `grocery_overlap` (a supermarket also tagged `convenience_store`),
`generic_types_only` (tags carry no information), `not_operational`.

The name lists are an audit instrument, not a classifier — they never overwrite
a type tag, they only disagree with it.

## The map

Which renderer the page uses is a rule about the data, not a preference:

| Data | Basemap | Why |
|---|---|---|
| Offline fixture | Leaflet + OpenStreetMap (CARTO dark tiles) | Synthetic data, so no Places restriction applies |
| Live + browser key | Google Maps, advanced markers | Live Places results must be drawn on a Google map |
| Live, no browser key | None — scatter with a scale bar | Refuses to draw live Places results on a non-Google basemap |

Markers, table rows, and filter chips are linked in every mode: clicking either
selects the other and opens the popup, and filtering redraws both.

Leaflet 1.9.4 is vendored into `frontend/vendor/` rather than pulled from a CDN, so
the page works offline apart from the tiles themselves.

### Enabling the Google path

```bash
export GOOGLE_MAPS_BROWSER_KEY=...   # Maps JavaScript API, HTTP-referrer restricted
export GOOGLE_MAPS_MAP_ID=...        # advanced markers will not load without one
```

This is a **different key** from `GOOGLE_MAPS_API_KEY`. The Places key stays
server-side and should be IP-restricted; the browser key is served to the client by
`/v1/mapconfig` and must be referrer-restricted and scoped to Maps JavaScript API
alone. `/v1/mapconfig` never returns the Places key.

### Tiles

The CARTO/OSM tile endpoints are free public services intended for modest volumes,
which a local concept test is. Anything with real traffic needs its own tile
provider or a self-hosted tile server — do not point production at them.

## Terms that constrain the design

- Only the **place ID** may be stored indefinitely. Names, addresses, and types
  must not be cached beyond Google's permitted window — so a production version
  stores place IDs and re-fetches the rest.
- Live Places results shown on a map must be shown on a **Google Map**, which rules
  out MapLibre/Leaflet for that data. The keyless scatter view is basemap-free and
  renders the synthetic fixture, so it sidesteps the question entirely.
- Nearby Search is a Pro-tier SKU with its own monthly free allowance; the old
  universal $200 credit was retired in March 2025. The field mask determines the
  SKU tier, so `PLACES_FIELD_MASK` is kept narrow.

## Fixture

`fixtures/tricities_sample.json` is **synthetic** data shaped like a real response,
with deliberately fake place ids. It is not cached Google output (which the terms
would not allow) — it exists so the test runs offline and so the classifier's edge
cases stay reproducible.

## Known rough edges

- The filter chips and table list **raw features**, while the stat tiles report
  **clustered sites**. On OSM that reads oddly: "fuel + store 0" next to
  "Store attached 15%". Both numbers are correct — the chips count features, the
  tile counts merged sites — but the table should probably move to sites.
- The Overpass cache is in-process, so it is lost on restart and each worker warms
  its own. A disk cache would be the obvious next step.
- `--compare` against the fixture cannot match anything, since the fixture's
  coordinates are invented. It reports `comparable: false` rather than a fake 0%.
- The Google path is written against the current docs but unexercised — no Places
  key was available to run it.
