"""
Scraper Target Definitions
==========================
Three source groups, each with a different signal type:

  retail   — established fashion stores (consumer demand)
  media    — fashion editorial / trend media (cultural momentum)
  dropship — supplier catalogs (competitor saturation)
"""

# ── Group A: Retail Stores ────────────────────────────────────────────────────
RETAIL_TARGETS = [
    {
        "site": "zara",
        "group": "retail",
        "url": "https://www.zara.com/us/en/woman-new-in-l1180.html",
        "selectors": [
            ".product-grid-product-info__name",
            "h2.product-grid-product-info__name",
            "h3",
            "img[alt]",
        ],
        "body_text": False,
        "scroll_passes": 3,
    },
    {
        "site": "asos",
        "group": "retail",
        "url": "https://www.asos.com/women/new-in/new-in-clothing/cat/?cid=2623&sort=freshness",
        "selectors": [
            "[data-auto-id='productTitle']",
            "h3",
            "article h2",
            "img[alt]",
        ],
        "body_text": False,
        "scroll_passes": 3,
    },
    {
        "site": "ssense",
        "group": "retail",
        "url": "https://www.ssense.com/en-us/women/new-arrivals",
        "selectors": [
            ".product-tile__description",
            ".product-tile__name",
            "h3",
            "img[alt]",
        ],
        "body_text": False,
        "scroll_passes": 2,
    },
]

# ── Group B: Fashion Media / Trendsetters ─────────────────────────────────────
# body_text=True → also scrape <p> tags for richer editorial language
MEDIA_TARGETS = [
    {
        "site": "vogue",
        "group": "media",
        "url": "https://www.vogue.com/fashion",
        "selectors": [
            "h2", "h3",
            ".summary-item__hed",
            ".summary-item__dek",
            "img[alt]",
        ],
        "body_text": True,
        "scroll_passes": 2,
    },
    {
        "site": "hypebae",
        "group": "media",
        "url": "https://hypebae.com/fashion",
        "selectors": [
            "h1", "h2", "h3",
            ".article-title",
            "img[alt]",
        ],
        "body_text": True,
        "scroll_passes": 2,
    },
    {
        "site": "whowhatwear",
        "group": "media",
        "url": "https://www.whowhatwear.com/fashion/trends",
        "selectors": [
            "h2", "h3",
            ".article-header__title",
            ".card__title",
            "img[alt]",
        ],
        "body_text": True,
        "scroll_passes": 2,
    },
]

# ── Group C: Dropship Supplier Catalogs ───────────────────────────────────────
DROPSHIP_TARGETS = [
    {
        "site": "trendsi",
        "group": "dropship",
        "url": "https://www.trendsi.com/collections/new-arrivals",
        "selectors": [".product-card__title", "h2", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 3,
    },
    {
        "site": "spocket",
        "group": "dropship",
        "url": "https://www.spocket.co/products?category=clothing&sort=newest",
        "selectors": [".product-name", ".product-title", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 2,
    },
    {
        "site": "cjdropshipping",
        "group": "dropship",
        "url": "https://cjdropshipping.com/list/?categoryId=1&sortType=0",
        "selectors": [".goods-name", ".product-name", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 2,
    },
    {
        "site": "fondmart",
        "group": "dropship",
        "url": "https://www.fondmart.com/new-arrivals/",
        "selectors": [".product-name", ".item-title", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 2,
    },
    {
        "site": "tasha",
        "group": "dropship",
        "url": "https://www.tashawholesale.com/new-arrivals",
        "selectors": [".product-title", ".grid-product__title", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 2,
    },
    {
        "site": "bloom",
        "group": "dropship",
        "url": "https://bloomdropship.com/collections/new-arrivals",
        "selectors": [".product-item__title", ".grid-product__title", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 2,
    },
    {
        "site": "banggood",
        "group": "dropship",
        "url": "https://www.banggood.com/Wholesale-Women-s-Clothing-c-11.html",
        "selectors": [".product-title", ".title", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 2,
    },
    {
        "site": "lightinthebox",
        "group": "dropship",
        "url": "https://www.lightinthebox.com/c/women-clothing_0208/?sortBy=newsarrivals",
        "selectors": [".product-name", ".title", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 2,
    },
    {
        "site": "eprolo",
        "group": "dropship",
        "url": "https://eprolo.com/product-category/clothing/",
        "selectors": [".woocommerce-loop-product__title", "h2.product-title", "h3", "img[alt]"],
        "body_text": False,
        "scroll_passes": 2,
    },
]

# Combined list for the scraper to iterate
ALL_TARGETS = RETAIL_TARGETS + MEDIA_TARGETS + DROPSHIP_TARGETS

DROPSHIP_SITE_NAMES = {t["site"] for t in DROPSHIP_TARGETS}
TOTAL_DROPSHIP_SOURCES = len(DROPSHIP_TARGETS)
