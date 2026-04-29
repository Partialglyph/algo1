from __future__ import annotations

# Maps each trade lane to GDELT query keywords and region labels.
# Keywords drive the GDELT Doc API search.
# Add or extend entries as new lanes are loaded from Excel.

LANE_EVENT_MAP: dict[str, dict] = {
    "Far East to Europe": {
        "keywords": [
            "container shipping",
            "Red Sea",
            "Suez Canal",
            "port strike",
            "shipping disruption",
            "Houthi",
        ],
    },
    "Far East to North America": {
        "keywords": [
            "container shipping",
            "Panama Canal",
            "West Coast port",
            "port strike",
            "shipping disruption",
            "trans-Pacific",
        ],
    },
    "Europe to Far East": {
        "keywords": [
            "container shipping",
            "Red Sea",
            "Suez Canal",
            "port strike",
            "shipping disruption",
        ],
    },
    "Australasia & Oceania": {
        "keywords": [
            "container shipping",
            "Australia port",
            "Pacific shipping",
            "port strike",
            "shipping disruption",
        ],
    },
    "South & Central America": {
        "keywords": [
            "container shipping",
            "South America port",
            "Panama Canal",
            "port strike",
            "shipping disruption",
        ],
    },
    "Indian Sub Cont & Middle East": {
        "keywords": [
            "container shipping",
            "Indian Ocean",
            "Red Sea",
            "Middle East shipping",
            "port strike",
            "Houthi",
            "shipping disruption",
        ],
    },
}

DEFAULT_KEYWORDS: list[str] = [
    "container shipping",
    "freight rates",
    "shipping disruption",
    "port strike",
    "canal disruption",
]


def get_keywords_for_lane(lane: str) -> list[str]:
    """Return GDELT search keywords for the given lane name using a fuzzy key match."""
    lane_lower = lane.lower()
    for key, config in LANE_EVENT_MAP.items():
        if key.lower() in lane_lower or any(
            part in lane_lower for part in key.lower().split(" to ")
        ):
            return config["keywords"]
    return DEFAULT_KEYWORDS
