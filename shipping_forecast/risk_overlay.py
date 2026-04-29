from __future__ import annotations

from dataclasses import dataclass

from .event_features import EventFeatureSet


@dataclass
class RiskOverlay:
    regime_label: str
    net_risk_score: float
    delta_mu_daily: float      # additive adjustment to daily drift
    sigma_multiplier: float    # multiplicative adjustment to daily volatility
    explanation: list[str]


def compute_overlay(features: EventFeatureSet) -> RiskOverlay:
    """
    Convert event features into Monte Carlo parameter adjustments.

    Design principles:
    - Volatility is widened more aggressively than drift is shifted.
      Events reliably increase uncertainty; directional impact is harder to claim.
    - Adjustments are deliberately conservative and capped.
      They condition the model, not override it.
    - Three regimes: Normal / Elevated / Severe.
    """
    score = features.net_risk_score
    explanations: list[str] = []

    if score < 0.20:
        regime = "Normal"
        delta_mu = 0.0
        sigma_mult = 1.0
        explanations.append("No significant disruption signals detected in recent coverage.")

    elif score < 0.45:
        regime = "Elevated disruption risk"
        delta_mu = -0.0003
        sigma_mult = 1.12
        if features.disruption_count > 0:
            explanations.append(
                f"{features.disruption_count} disruption-related articles detected in the past 14 days."
            )
        if features.mean_tone < -1.0:
            explanations.append("News tone for this route region is trending negative.")
        explanations.append("Forecast uncertainty band widened by ~12%.")

    else:
        regime = "Severe disruption regime"
        delta_mu = -0.0010
        sigma_mult = 1.30
        if features.high_severity_count > 0:
            explanations.append(
                f"{features.high_severity_count} high-severity events detected "
                f"(conflict / sanctions / attack) in the past 14 days."
            )
        if features.disruption_count > 0:
            explanations.append(
                f"{features.disruption_count} disruption-related articles detected."
            )
        if features.mean_tone < -2.0:
            explanations.append("News tone strongly negative for this route region.")
        explanations.append("Forecast uncertainty band widened by ~30%.")

    if features.top_headlines:
        explanations.append(f'Top signal: "{features.top_headlines[0]}"')

    return RiskOverlay(
        regime_label=regime,
        net_risk_score=round(score, 3),
        delta_mu_daily=delta_mu,
        sigma_multiplier=sigma_mult,
        explanation=explanations,
    )
