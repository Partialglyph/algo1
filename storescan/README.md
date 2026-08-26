# StoreScan — prototype product/price extractor

Give it retail product URLs; it tells you what is on the page and what it costs.
Built for the "does this store carry X, and for how much" question.

**You are responsible for confirming each site permits automated access.** This
tool makes the polite path the default, but it cannot tell you whether a given
site's terms allow it.

## Run it

```bash
python scripts/store_scan.py https://example.com/product/123
```

```bash
python scripts/store_scan.py --file urls.txt --csv out.csv --find "monster energy"
```

`urls.txt` is one URL per line; `#` comments are ignored.

## How it extracts

Four methods, tried best-first. **Every row records which method produced it**,
because that is the real signal of how much to trust the number.

| Method | Confidence | What it reads |
|---|---|---|
| `json-ld` | 0.95 | `<script type="application/ld+json">` schema.org Product |
| `microdata` | 0.85 | `itemtype="schema.org/Product"` attributes |
| `opengraph` | 0.70 | `og:title` + `product:price:amount` meta tags |
| `heuristic` | 0.30 | `<h1>` plus the first currency-looking string |

The first three read data the site published *deliberately* for machines — that
is why they are worth so much more than the fourth. A `heuristic` row is a
prompt to go look at the page yourself, not an answer. It will cheerfully
mistake a free-shipping threshold or a crossed-out list price for the price.

JSON-LD handling covers the shapes real sites actually emit: `@graph` wrappers,
arrays of products, `@type` as a list, and `offers` as a single Offer, a list of
Offers, or an AggregateOffer with a price range. Where several offers exist it
takes the lowest and says so in the row's notes.

Prices are stored as **integer cents**. Floats cannot represent 2.49 exactly and
the error compounds as soon as you compare or average.

## Politeness, which is not optional

- **robots.txt is respected by default.** A disallowed URL returns
  `blocked_by_robots` and is not fetched. `--ignore-robots` exists for sites that
  have given you permission, and prints a warning every time.
- **One request at a time, never concurrent.** Concurrency is what turns a scan
  into someone's incident.
- **A real delay between requests** — 2 s, or robots.txt's `Crawl-delay` if it
  asks for more.
- **Responses cached to disk for 6 hours**, so re-running a scan costs the site
  nothing. `--no-cache` to force a refetch.
- **An honest User-Agent.** Set `STORESCAN_USER_AGENT` to include a contact
  address before doing anything at volume.
- **A 200-URL guard** per run (`STORESCAN_MAX_URLS`).

The defaults are deliberately slow. A 50-page scan takes about two minutes, and
that is the correct speed.

## What it will not do

- Get past bot protection. A 403 is reported with a hint and left alone —
  if a site is actively blocking, the answer is a data feed or an API, not a
  cleverer scraper.
- Log in, or scan anything behind authentication.
- Render JavaScript. Pages that build their product list client-side return no
  products and a hint saying so. The fix is usually to find the JSON endpoint the
  page's own frontend calls, which is both easier to parse and cheaper for the
  site than a headless browser.

## Output

Console by default; `--csv` and `--json` for files. CSV columns:

```
url, product, price, currency, availability, sku, brand, method, confidence
```

Filter on `method` before trusting anything in bulk.

## Tests

```bash
python -c "import sys;sys.path.insert(0,'.');import tests.test_storescan as t;[getattr(t,n)() for n in dir(t) if n.startswith('test_')];print('ok')"
```

Covers price parsing across decimal-comma and thousands-separator formats, each
of the four extractors, extractor precedence, `@graph` traversal, and
lowest-offer selection.

## Known limits

- Listing pages only yield multiple products if the site marks each one up
  individually. Many mark up only the page.
- No pagination following. Give it the URLs you want.
- The cache key is the exact URL, so tracking parameters defeat it.
- Windows consoles in cp1252 mangle currency symbols on screen; the CSV and JSON
  output are UTF-8 and correct.
