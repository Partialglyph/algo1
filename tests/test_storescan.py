from storescan.extract import extract, parse_price


def test_parse_price_handles_common_formats():
    assert parse_price("$2.49") == 249
    assert parse_price("CA$12.00") == 1200
    assert parse_price(2.49) == 249
    assert parse_price("2,49 €") == 249          # decimal comma
    assert parse_price("1,234.56") == 123456     # thousands separator
    assert parse_price("1.234,56") == 123456     # European thousands
    assert parse_price("free") is None
    assert parse_price(None) is None


JSONLD = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Monster Energy 473ml",
 "sku":"0700847","brand":{"@type":"Brand","name":"Monster"},
 "offers":{"@type":"Offer","price":"3.49","priceCurrency":"CAD",
           "availability":"https://schema.org/InStock"}}
</script></head><body><h1>Monster Energy</h1></body></html>
"""


def test_jsonld_is_preferred_and_complete():
    products, method = extract(JSONLD)
    assert method == "json-ld"
    assert len(products) == 1
    p = products[0]
    assert p.name == "Monster Energy 473ml"
    assert p.price_cents == 349 and p.price == 3.49
    assert p.currency == "CAD"
    assert p.availability == "InStock"
    assert p.brand == "Monster"
    assert p.sku == "0700847"
    assert p.confidence > 0.9


GRAPH = """
<html><head><script type="application/ld+json">
{"@graph":[{"@type":"WebPage","name":"Not a product"},
 {"@type":["Product"],"name":"Coke Zero 355ml",
  "offers":[{"@type":"Offer","price":"2.79","priceCurrency":"CAD"},
            {"@type":"Offer","price":"1.99","priceCurrency":"CAD"}]}]}
</script></head><body></body></html>
"""


def test_jsonld_walks_graph_and_takes_lowest_offer():
    products, _ = extract(GRAPH)
    assert len(products) == 1
    assert products[0].name == "Coke Zero 355ml"
    assert products[0].price_cents == 199
    assert any("2 offers" in n for n in products[0].notes)


MICRODATA = """
<html><body>
<div itemscope itemtype="http://schema.org/Product">
  <span itemprop="name">Doritos Nacho 235g</span>
  <meta itemprop="price" content="4.99">
  <meta itemprop="priceCurrency" content="CAD">
  <link itemprop="availability" href="https://schema.org/OutOfStock">
</div></body></html>
"""


def test_microdata_used_when_no_jsonld():
    products, method = extract(MICRODATA)
    assert method == "microdata"
    assert products[0].name == "Doritos Nacho 235g"
    assert products[0].price_cents == 499
    assert products[0].availability == "OutOfStock"


OPENGRAPH = """
<html><head>
<meta property="og:title" content="Red Bull 250ml">
<meta property="product:price:amount" content="2.29">
<meta property="product:price:currency" content="CAD">
</head><body></body></html>
"""


def test_opengraph_fallback():
    products, method = extract(OPENGRAPH)
    assert method == "opengraph"
    assert products[0].price_cents == 229


HEURISTIC = """
<html><body><h1>Corner Store Milk 2L</h1>
<p>Fresh daily. Only $5.29 today. Free delivery over $35.</p>
<p>More text so the page does not look like an empty client-rendered shell,
padding this out well past the six hundred character threshold used to detect
single page application shells. Lorem ipsum dolor sit amet, consectetur
adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna
aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris
nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in
reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla.</p>
</body></html>
"""


def test_heuristic_is_last_and_low_confidence():
    products, method = extract(HEURISTIC)
    assert method == "heuristic"
    assert products[0].name == "Corner Store Milk 2L"
    assert products[0].price_cents == 529      # picks $5.29, not the $35 threshold
    assert products[0].confidence <= 0.3
    assert products[0].notes


def test_empty_page_yields_nothing():
    products, method = extract("<html><body><p>Hello</p></body></html>")
    assert products == [] and method == "none"
