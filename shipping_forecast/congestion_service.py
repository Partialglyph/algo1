from __future__ import annotations

"""
congestion_service.py

Returns port/chokepoint congestion signals for a given lane.
V1: rule-based stubs derived from the lane name + news risk regime.
Replace stub logic with a real vessel AIS or port-congestion feed when available.
(Viable free/low-cost sources: MarineTraffic free tier, VesselsValue open API,
or scraping PortWatch/Lloyd's List congestion index.)
"""

from datetime import datetime, timezone
from typing import List

from .models import CongestionSignal

_LANE_NODE_MAP: dict[str, list[dict]] = {
    "china_europe": [
        {"node_type": "chokepoint", "node_name": "Suez Canal", "base_index": 0.45},
        {"node_type": "port", "node_name": "Shanghai Port", "base_index": 0.40},
        {"node_type": "port", "node_name": "Rotterdam Port", "base_index": 0.35},
    ],
    "china_us_west": [
        {"node_type": "port", "node_name": "Los Angeles/Long Beach", "base_index": 0.50},
        {"node_type": "chokepoint", "node_name": "Panama Canal", "base_index": 0.40},
        {"node_type": "port", "node_name": "Yantian Port", "base_index": 0.35},
    ],
    "china_us_east": [
        {"node_type": "port", "node_name": "Port of New York/NJ", "base_index": 0.45},
        {"node_type": "chokepoint", "node_name": "Panama Canal", "base_index": 0.40},
    ],
    "default": [
        {"node_type": "regional", "node_name": "Global Average", "base_index": 0.40},
    ],
}


def _trend_from_index(index: float) -> str:
    if index > 0.65:
        return "worsening"
    if index < 0.30:
        return "improving"
    return "stable"


def get_congestion_signals(lane: str, risk_score: float) -> List[CongestionSignal]:
    """
    Return congestion signals for the lane.
    Risk score (0-100) slightly modulates the index to reflect news-driven disruption.
    """
    lane_key = lane.lower().replace(" ", "_").replace("-", "_")
    nodes = _LANE_NODE_MAP.get(lane_key, _LANE_NODE_MAP["default"])

    risk_boost = min(risk_score / 200.0, 0.20)  # max +0.20 from news risk
    now = datetime.now(timezone.utc)

    signals: List[CongestionSignal] = []
    for n in nodes:
        adjusted = round(min(n["base_index"] + risk_boost, 1.0), 2)
        signals.append(CongestionSignal(
            node_type=n["node_type"],
            node_name=n["node_name"],
            lane=lane,
            congestion_index=adjusted,
            trend=_trend_from_index(adjusted),
            last_updated=now,
            note="Stub — replace with live AIS/port-congestion feed",
        ))
    return signals
