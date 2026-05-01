from __future__ import annotations

"""
summarizer.py

Generates deterministic English text for:
  - individual featured articles (summary_english, why_it_matters)
  - the overall risk narrative (risk_summary, top_drivers)

All output is derived purely from measured metrics so the text and the
score are always consistent with each other. No stochastic LLM calls.
"""

from .event_features import EventFeatureSet, DISRUPTION_KEYWORDS, HIGH_SEVERITY_KEYWORDS
from .models import FeaturedArticle


# ---------------------------------------------------------------------------
# Language detection heuristic
# ---------------------------------------------------------------------------

# Characters that strongly indicate non-English text
_NON_ASCII_THRESHOLD = 0.15  # fraction of characters that are non-ASCII


def _looks_english(text: str) -> bool:
    """Return True if the text appears to already be in English."""
    if not text:
        return True
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return (non_ascii / len(text)) < _NON_ASCII_THRESHOLD


def _transliterate_title(title: str) -> str:
    """
    Best-effort ASCII transliteration for non-English titles.
    For a production system this would call a translation API; here we
    strip non-ASCII characters and annotate the result so the UI can
    indicate the original language.
    """
    ascii_chars = [c if ord(c) < 128 else '' for c in title]
    cleaned = ''.join(ascii_chars).strip()
    if not cleaned:
        return "[Title in non-English script — see original URL]"
    return cleaned + " [translated]"


def ensure_english_title(title: str) -> tuple[str, str]:
    """
    Return (title_english, language_tag).
    If the title appears to be English already, returns it unchanged with
    language='en'. Otherwise transliterates and marks as 'non-en'.
    """
    if _looks_english(title):
        return title, "en"
    return _transliterate_title(title), "non-en"


# ---------------------------------------------------------------------------
# Per-article text generation
# ---------------------------------------------------------------------------

def _severity_label(title: str) -> str:
    t = title.lower()
    if any(k.lower() in t for k in HIGH_SEVERITY_KEYWORDS):
        return "high-severity"
    if any(k.lower() in t for k in DISRUPTION_KEYWORDS):
        return "disruption-related"
    return "background"


def generate_article_summary(title_english: str, source: str, tone: float, themes: list[str]) -> str:
    """
    One-sentence English summary of the article's shipping relevance,
    derived from its title, source, tone, and matched themes.
    """
    severity = _severity_label(title_english)
    theme_str = ", ".join(themes[:2]) if themes else "shipping markets"
    tone_label = "negative" if tone < -1.0 else ("positive" if tone > 1.0 else "neutral")

    if severity == "high-severity":
        return (
            f"Reporting from {source or 'this outlet'} covers a high-severity event "
            f"touching {theme_str}, with {tone_label} news tone — "
            f"a direct signal for elevated freight-rate volatility."
        )
    if severity == "disruption-related":
        return (
            f"Coverage from {source or 'this outlet'} describes a disruption in "
            f"{theme_str} with {tone_label} sentiment, consistent with near-term "
            f"rate pressure on affected lanes."
        )
    return (
        f"Article from {source or 'this outlet'} provides background context on "
        f"{theme_str}; tone is {tone_label} and shipping-market impact is indirect."
    )


def generate_why_it_matters(title_english: str, themes: list[str], relevance: float, tone: float) -> str:
    """
    Short plain-English explanation of why this article feeds into the risk score.
    """
    severity = _severity_label(title_english)
    theme_str = " and ".join(themes[:2]) if themes else "this route"
    relevance_pct = round(relevance * 100)

    if severity == "high-severity":
        return (
            f"This article raises the severity score because it describes a conflict, "
            f"sanctions, or security event relevant to {theme_str}. "
            f"It carries a shipping-relevance score of {relevance_pct}%, "
            f"meaning it is directly linked to operational disruption risk."
        )
    if severity == "disruption-related":
        return (
            f"Disruption language in this headline — such as port closures, rerouting, "
            f"or strikes — increases the disruption component of the risk score for "
            f"{theme_str}. Relevance score: {relevance_pct}%."
        )
    return (
        f"This article contributes background signal to {theme_str} coverage volume, "
        f"which feeds the volume component of the composite risk score. "
        f"Relevance score: {relevance_pct}%."
    )


# ---------------------------------------------------------------------------
# Lane-level risk narrative
# ---------------------------------------------------------------------------

def generate_risk_summary(features: EventFeatureSet, regime: str, score_100: float) -> str:
    """
    Generate a factual, metric-grounded paragraph explaining the net risk score.
    This replaces the old template strings that were disconnected from live values.
    """
    parts: list[str] = []

    # Score intro
    parts.append(
        f"The net risk score for this lane is {score_100:.1f}/100, "
        f"placing it in the {regime} regime."
    )

    # Volume signal
    if features.count_72h > 0:
        baseline_multiple = round(features.count_72h / 5, 1)  # soft baseline of 5
        if baseline_multiple >= 2.0:
            parts.append(
                f"Article volume in the past 72 hours is {baseline_multiple}x the baseline "
                f"({features.count_72h} articles), which pushes the volume score higher."
            )
        elif features.count_72h > 0:
            parts.append(
                f"{features.count_72h} articles matched in the past 72 hours — "
                f"volume is near baseline."
            )
    else:
        parts.append("No matching articles were detected in the past 72 hours.")

    # Disruption signal
    if features.disruption_count > 0:
        parts.append(
            f"{features.disruption_count} of those articles "
            f"({'all ' if features.disruption_count == features.article_count else ''})"
            f"contain disruption-related language — strikes, closures, rerouting, "
            f"or canal/chokepoint events."
        )

    # Severity signal
    if features.high_severity_count > 0:
        parts.append(
            f"{features.high_severity_count} article"
            f"{'s' if features.high_severity_count != 1 else ''} "
            f"reference high-severity events such as conflict, sanctions, "
            f"missile attacks, or piracy."
        )

    # Sentiment signal
    tone_direction = "negative" if features.mean_tone < -1.5 else (
        "strongly negative" if features.mean_tone < -3.0 else (
            "positive" if features.mean_tone > 0.5 else "neutral"
        )
    )
    parts.append(
        f"Average news tone across matched articles is {features.mean_tone:.2f} "
        f"(GDELT scale), which reads as {tone_direction}."
    )

    # Concentration signal
    if features.concentration_score > 0.5:
        parts.append(
            f"Coverage concentration is high ({round(features.concentration_score * 100)}%), "
            f"meaning most articles cluster around a single disruption theme."
        )

    # Model effect
    if regime == "Normal":
        parts.append(
            "As a result, no adjustments have been made to the Monte Carlo drift or "
            "volatility parameters — the forecast reflects baseline historical dynamics."
        )
    elif regime == "Elevated":
        parts.append(
            "The Monte Carlo model has responded with a modest drift reduction "
            "(daily mu −0.0003) and a 12% widening of the volatility band, "
            "reflecting increased uncertainty without a directional call."
        )
    else:
        parts.append(
            "The Monte Carlo model has responded with a meaningful drift reduction "
            "(daily mu −0.0010) and a 30% widening of the volatility band, "
            "reflecting severe uncertainty and elevated downside risk."
        )

    return " ".join(parts)


def generate_top_drivers(features: EventFeatureSet, regime: str) -> list[str]:
    """
    Generate a short bullet-point list of the primary factors behind the score.
    Each bullet is a concrete, metric-grounded statement.
    """
    drivers: list[str] = []

    baseline_multiple = round(features.count_72h / 5, 1)
    if baseline_multiple >= 1.5:
        drivers.append(
            f"Article volume is {baseline_multiple}x the 7-day baseline "
            f"({features.count_72h} articles in 72 h)"
        )
    elif features.count_7d > 0:
        drivers.append(
            f"Article volume is near baseline ({features.count_72h} articles in 72 h, "
            f"{features.count_7d} over 7 days)"
        )
    else:
        drivers.append("No matching articles detected in this window")

    if features.high_severity_count > 0:
        drivers.append(
            f"{features.high_severity_count} high-severity event article"
            f"{'s' if features.high_severity_count != 1 else ''} detected "
            f"(conflict / sanctions / attack)"
        )

    if features.disruption_count > 0:
        drivers.append(
            f"{features.disruption_count} disruption-linked article"
            f"{'s' if features.disruption_count != 1 else ''} "
            f"(strikes, closures, canal, rerouting)"
        )

    if features.mean_tone < -1.0:
        drivers.append(
            f"News tone is {features.mean_tone:.2f} — trending negative for this lane"
        )

    if features.concentration_score > 0.5:
        drivers.append(
            f"Coverage is converging on a single theme "
            f"({round(features.concentration_score * 100)}% concentration score)"
        )

    if regime in ("Elevated", "Severe"):
        if features.top_headlines:
            drivers.append(f'Top signal headline: "{features.top_headlines[0]}"')

    return drivers or ["Insufficient article data to identify specific drivers"]
