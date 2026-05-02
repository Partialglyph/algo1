from __future__ import annotations

"""
summarizer.py

Generates deterministic English text for articles and the lane risk narrative.
Translation is now handled by translation_service.py (DeepL-first).
This module exposes sync helpers consumed by forecast_service and the new dashboard.
"""

from .event_features import EventFeatureSet, DISRUPTION_KEYWORDS, HIGH_SEVERITY_KEYWORDS


def _severity_label(title: str) -> str:
    t = title.lower()
    if any(k.lower() in t for k in HIGH_SEVERITY_KEYWORDS):
        return "high-severity"
    if any(k.lower() in t for k in DISRUPTION_KEYWORDS):
        return "disruption-related"
    return "background"


def generate_article_summary(
    title_english: str, source: str, tone: float, themes: list[str]
) -> str:
    severity = _severity_label(title_english)
    theme_str = ", ".join(themes[:2]) if themes else "shipping markets"
    tone_label = (
        "sharply negative" if tone < -3.0
        else "negative" if tone < -1.0
        else "positive" if tone > 1.0
        else "neutral"
    )
    if severity == "high-severity":
        return (
            f"Reporting from {source or 'this outlet'} covers a high-severity event "
            f"touching {theme_str}, with {tone_label} news tone. "
            f"Events of this type are a direct signal for elevated freight-rate volatility."
        )
    if severity == "disruption-related":
        return (
            f"Coverage from {source or 'this outlet'} describes a disruption in "
            f"{theme_str} with {tone_label} sentiment. "
            f"Disruption-language articles raise the disruption component of the composite risk score."
        )
    return (
        f"Article from {source or 'this outlet'} provides background context on "
        f"{theme_str}. Tone is {tone_label}. "
        f"Shipping-market impact is indirect — contributes primarily to volume signal."
    )


def generate_why_it_matters(
    title_english: str, themes: list[str], relevance: float, tone: float
) -> str:
    severity = _severity_label(title_english)
    theme_str = " and ".join(themes[:2]) if themes else "this route"
    relevance_pct = round(relevance * 100)
    if severity == "high-severity":
        return (
            f"This article raises the severity score because it describes a conflict, "
            f"sanctions, or security event directly relevant to {theme_str}. "
            f"Shipping-relevance: {relevance_pct}%. "
            f"High-severity articles increase the Monte Carlo model's volatility multiplier "
            f"and apply a downward drift adjustment."
        )
    if severity == "disruption-related":
        return (
            f"Disruption language in this headline — port closures, rerouting, "
            f"strikes, or canal events — increases the disruption risk component for {theme_str}. "
            f"Relevance: {relevance_pct}%."
        )
    return (
        f"This article contributes background signal to {theme_str} coverage volume. "
        f"At {relevance_pct}% relevance, direct impact on the disruption or severity "
        f"components is limited, but sustained volume can shift the score toward Elevated."
    )


def generate_risk_summary(
    features: EventFeatureSet, regime: str, score_100: float
) -> str:
    parts: list[str] = []
    parts.append(
        f"The net risk score for this lane is {score_100:.1f} out of 100, "
        f"placing it in the {regime} regime."
    )
    if features.count_72h == 0:
        parts.append("No matching articles were detected in the past 72 hours — volume is at baseline.")
    else:
        bm = round(features.count_72h / 5, 1)
        if bm >= 2.0:
            parts.append(
                f"Article volume over 72 hours is {bm}x the 7-day baseline "
                f"({features.count_72h} articles matched) — volume component is significantly elevated."
            )
        else:
            parts.append(
                f"{features.count_72h} articles matched in the past 72 hours ({bm}x baseline). "
                f"Volume is near normal."
            )
    if features.disruption_count > 0:
        parts.append(
            f"{features.disruption_count} article{'s' if features.disruption_count != 1 else ''} "
            f"contain disruption-related language — strikes, port closures, rerouting, or canal events. "
            f"The disruption component carries 40% weight in the composite score."
        )
    else:
        parts.append("No disruption-specific language detected — disruption component is near zero.")
    if features.high_severity_count > 0:
        parts.append(
            f"{features.high_severity_count} article{'s' if features.high_severity_count != 1 else ''} "
            f"reference high-severity events (conflict, sanctions, piracy)."
        )
    tone_desc = (
        "strongly negative" if features.mean_tone < -3.0
        else "negative" if features.mean_tone < -1.0
        else "positive" if features.mean_tone > 0.5
        else "neutral"
    )
    parts.append(
        f"Average news tone is {features.mean_tone:.2f} ({tone_desc}). "
        f"Tone feeds the sentiment component, which carries 20% of the composite weight."
    )
    if features.concentration_score > 0.5:
        parts.append(
            f"Coverage concentration is high ({round(features.concentration_score * 100)}%), "
            f"meaning most articles cluster around a single disruption narrative."
        )
    else:
        parts.append(
            f"Coverage is distributed ({round(features.concentration_score * 100)}% concentration), "
            f"limiting the concentration component's contribution."
        )
    if regime == "Normal":
        parts.append("Monte Carlo runs with unmodified drift and volatility.")
    elif regime == "Elevated":
        parts.append(
            "In response, the Monte Carlo model applies a modest downward drift (\u22120.0003/day) "
            "and widens the volatility band by 12%."
        )
    else:
        parts.append(
            "In response, the Monte Carlo model applies a meaningful downward drift (\u22120.0010/day) "
            "and widens the volatility band by 30%."
        )
    return " ".join(parts)


def generate_top_drivers(features: EventFeatureSet, regime: str) -> list[str]:
    drivers: list[str] = []
    bm = round(features.count_72h / 5, 1)
    if features.count_72h == 0:
        drivers.append("No matching articles detected in the 72-hour window")
    elif bm >= 1.5:
        drivers.append(f"Article volume is {bm}x the 7-day baseline ({features.count_72h} in 72 h)")
    else:
        drivers.append(f"Volume near baseline ({features.count_72h} in 72 h, {features.count_7d} over 7 d)")
    if features.high_severity_count > 0:
        drivers.append(
            f"{features.high_severity_count} high-severity article"
            f"{'s' if features.high_severity_count != 1 else ''} (conflict/sanctions/piracy)"
        )
    if features.disruption_count > 0:
        drivers.append(
            f"{features.disruption_count} disruption-linked article"
            f"{'s' if features.disruption_count != 1 else ''} (strikes, closures, rerouting)"
        )
    if features.mean_tone < -1.0:
        drivers.append(f"Average tone {features.mean_tone:.2f} ({'strongly ' if features.mean_tone < -3 else ''}negative)")
    elif features.mean_tone > 0.5:
        drivers.append(f"Average tone {features.mean_tone:.2f} (positive — reduces risk pressure)")
    if features.concentration_score > 0.5:
        drivers.append(f"Coverage {round(features.concentration_score * 100)}% concentrated around dominant theme")
    top_themes = sorted(features.theme_counts.items(), key=lambda x: x[1][0], reverse=True)[:2]
    if top_themes:
        drivers.append("Top themes: " + " and ".join(t for t, _ in top_themes))
    if regime != "Normal":
        drivers.append(
            f"MC model adjusted: {'\u03bc\u22120.0003, \u03c3\u00d71.12' if regime == 'Elevated' else '\u03bc\u22120.0010, \u03c3\u00d71.30'}"
        )
    return drivers
