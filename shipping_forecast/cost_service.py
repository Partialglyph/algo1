from __future__ import annotations

"""
cost_service.py

Builds the CostBundle for the /dashboard endpoint.
V1: rule-based stubs for duty rates and clearance costs by lane.
Replace with WTO TRAINS API or a commercial customs-data feed when available.
"""

from datetime import datetime, timezone
from typing import Optional

from .models import CostBundle, DutyRate, ClearanceCost

_LANE_DUTY_MAP: dict[str, dict] = {
    "china_europe": {
        "import_country": "EU",
        "export_country": "CN",
        "rate_pct": 5.0,
        "base_clearance": 140.0,
        "trend": "moderate increase",
        "safety": "medium",
        "drivers": [
            "EU carbon border adjustment mechanism phasing in",
            "Heightened customs scrutiny on electronics and textiles",
            "Yuan/EUR volatility affecting customs valuation",
        ],
    },
    "china_us_west": {
        "import_country": "US",
        "export_country": "CN",
        "rate_pct": 25.0,
        "base_clearance": 200.0,
        "trend": "elevated — Section 301 tariffs active",
        "safety": "high",
        "drivers": [
            "Section 301 tariffs: 25% on most manufactured goods",
            "Additional Section 232 steel/aluminum surcharges on some categories",
            "Unpredictable exclusion-review process adds timing uncertainty",
        ],
    },
    "china_us_east": {
        "import_country": "US",
        "export_country": "CN",
        "rate_pct": 25.0,
        "base_clearance": 200.0,
        "trend": "elevated — Section 301 tariffs active",
        "safety": "high",
        "drivers": [
            "Same Section 301 exposure as West Coast routing",
            "East Coast port fees slightly higher than West Coast average",
        ],
    },
}

_DEFAULT_LANE = {
    "import_country": "Various",
    "export_country": "Various",
    "rate_pct": 4.5,
    "base_clearance": 120.0,
    "trend": "stable",
    "safety": "medium",
    "drivers": ["Global average duty rate — replace with lane-specific data"],
}


def build_cost_bundle(lane: str, oil_price: Optional[float] = None) -> CostBundle:
    lane_key = lane.lower().replace(" ", "_").replace("-", "_")
    cfg = _LANE_DUTY_MAP.get(lane_key, _DEFAULT_LANE)
    now = datetime.now(timezone.utc)

    fuel_surcharge = 0.0
    if oil_price and oil_price > 90:
        fuel_surcharge = round((oil_price - 90) * 0.8, 2)  # rough bunker-cost proxy

    total_exposure = round(cfg["base_clearance"] + fuel_surcharge, 2)

    drivers = list(cfg["drivers"])
    if fuel_surcharge > 0:
        drivers.append(
            f"Brent crude at ${oil_price:.0f}/bbl — bunker surcharge adds ~${fuel_surcharge:.0f}/unit"
        )

    summary = (
        f"Effective duty rate: {cfg['rate_pct']}%. "
        f"Estimated clearance cost: ${cfg['base_clearance']:.0f} USD/unit. "
        f"Duty trend: {cfg['trend']}. "
        f"Total cost exposure estimate: ${total_exposure:.0f} USD/unit."
    )

    return CostBundle(
        duty_rate=DutyRate(
            import_country=cfg["import_country"],
            export_country=cfg["export_country"],
            lane=lane,
            rate_pct=cfg["rate_pct"],
            last_updated=now,
            source="stub — replace with WTO TRAINS or commercial data",
        ),
        clearance_cost=ClearanceCost(
            lane=lane,
            currency="USD",
            base_cost=cfg["base_clearance"],
            volatility_adjustment=fuel_surcharge,
            last_updated=now,
        ),
        duty_trend=cfg["trend"],
        duty_safety=cfg["safety"],
        cost_pressure_summary=summary,
        cost_drivers=drivers,
        total_cost_exposure=total_exposure,
    )
