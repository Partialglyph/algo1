from __future__ import annotations

"""
summarizer.py

Generates deterministic English text for:
  - individual featured articles (summary_english, why_it_matters)
  - the overall risk narrative (risk_summary, top_drivers)
  - English translation of non-English article titles

Translation strategy (in order of preference):
  1. Title is already English  -> return as-is
  2. googletrans available     -> translate via Google Translate (free, no key needed)
  3. Fallback                  -> strip non-ASCII, append [translated]
"""

from .event_features import EventFeatureSet, DISRUPTION_KEYWORDS, HIGH_SEVERITY_KEYWORDS

_NON_ASCII_THRESHOLD = 0.15


def _looks_english(text: str) -> bool:
    if not text:
        return True
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return (non_ascii / len(text)) < _NON_ASCII_THRESHOLD


def _translate_with_googletrans(title: str) -> str | None:
    """
    Attempt translation via googletrans. Returns translated string or None on failure.
    googletrans is an unofficial wrapper around Google Translate — no API key required.
    """
    try:
        from googletrans import Translator
        translator = Translator()
        result = translator.translate(title, dest="en")
        translated = result.text.strip()
        if translated and translated.lower() != title.lower():
            return translated
        return None
    except Exception:
        return None


def _ascii_fallback(title: str) -> str:
    cleaned = "".join(c if ord(c) < 128 else "" for c in title).strip()
    if not cleaned:
        return "[Title in non-English script — see original URL]"
    return cleaned + " [translated]"


def ensure_english_title(title: str) -> tuple[str, str]:
    """
    Return (title_english, language_tag).
    Tries real translation first; falls back gracefully.
    """
    if _looks_english(title):
        return title, "en"

    translated = _translate_with_googletrans(title)
    if translated:
        return translated, "non-en"

    return _ascii_fallback(title), "non-en"


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------

def _severity_label(title: str) -> str:
    t = title.lower()
    if any(k.lower() in t for k in HIGH_SEVERITY_KEYWORDS):
        return "high-severity"
    if any(k.lower() in t for k in DISRUPTION_KEYWORDS):
        return "disruption-related"
    return "background"


# ---------------------------------------------------------------------------
# Per-article text generation
# ---------------------------------------------------------------------------

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
            f"Events of this type are a direct signal for elevated freight-rate volatility "
            f"and are weighted heavily in the net risk score."
        )
    if severity == "disruption-related":
        return (
            f"Coverage from {source or 'this outlet'} describes a disruption in "
            f"{theme_str} with {tone_label} sentiment. "
            f"Disruption-language articles raise the disruption component of the composite "
            f"risk score and can widen Monte Carlo volatility bands."
        )
    return (
        f"Article from {source or 'this outlet'} provides background context on "
        f"{theme_str}. Tone is {tone_label}. Shipping-market impact is indirect — "
        f"this article contributes primarily to volume signal rather than severity signal."
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
            f"Its shipping-relevance score is {relevance_pct}%, meaning the algorithm "
            f"treats it as operationally significant. "
            f"High-severity articles increase the Monte Carlo model's volatility multiplier "
            f"and apply a downward drift adjustment to the forecast."
        )
    if severity == "disruption-related":
        return (
            f"Disruption language in this headline — port closures, rerouting, "
            f"strikes, or canal events — increases the disruption component of the risk "
            f"score for {theme_str}. "
            f"Relevance score: {relevance_pct}%. "
            f"Disruption articles push the net risk score toward the Elevated regime "
            f"when they cluster within a short time window."
        )
    return (
        f"This article contributes background signal to {theme_str} coverage volume. "
        f"Volume signal accounts for 25% of the net risk score weighting. "
        f"At {relevance_pct}% relevance, this article has limited direct impact on the "
        f"disruption or severity components, but a sustained increase in background "
        f"volume can shift the score toward Elevated."
    )


# ---------------------------------------------------------------------------
# Lane-level risk narrative
# ---------------------------------------------------------------------------

def generate_risk_summary(
    features: EventFeatureSet, regime: str, score_100: float
) -> str:
    """
    Generate a factual, metric-grounded multi-sentence paragraph explaining
    the net risk score. Each sentence is tied to a specific measured signal.
    """
    parts: list[str] = []

    # 1. Lead sentence — score and regime
    parts.append(
        f"The net risk score for this lane is {score_100:.1f} out of 100, "
        f"placing it in the {regime} regime."
    )

    # 2. Volume signal
    if features.count_72h == 0:
        parts.append(
            "No matching articles were detected in the past 72 hours, "
            "so the volume component of the score is at baseline."
        )
    else:
        baseline_multiple = round(features.count_72h / 5, 1)
        if baseline_multiple >= 2.0:
            parts.append(
                f"Article volume over the past 72 hours is {baseline_multiple}x the 7-day baseline "
                f"({features.count_72h} articles matched), which pushes the volume component "
                f"of the score significantly higher — volume signal accounts for 25% of the "
                f"composite weighting."
            )
        else:
            parts.append(
                f"{features.count_72h} articles matched in the past 72 hours "
                f"({baseline_multiple}x baseline). Volume is near normal levels and "
                f"contributes limited upward pressure to the score."
            )

    # 3. Disruption signal
    if features.disruption_count > 0:
        parts.append(
            f"{features.disruption_count} of those articles contain disruption-related language "
            f"— strikes, port closures, rerouting, or canal and chokepoint events. "
            f"The disruption component carries 40% weight in the composite score."
        )
    else:
        parts.append(
            "None of the matched articles contained disruption-specific language, "
            "so the disruption component is near zero."
        )

    # 4. Severity signal
    if features.high_severity_count > 0:
        parts.append(
            f"{features.high_severity_count} article"
            f"{'s' if features.high_severity_count != 1 else ''} "
            f"reference high-severity events — conflict, sanctions, missile attacks, "
            f"or piracy. These articles carry elevated individual risk weights "
            f"and feed the severity component of the score."
        )

    # 5. Tone signal
    if features.mean_tone < -3.0:
        tone_desc = "strongly negative"
    elif features.mean_tone < -1.0:
        tone_desc = "negative"
    elif features.mean_tone > 0.5:
        tone_desc = "positive"
    else:
        tone_desc = "neutral"

    parts.append(
        f"Average news tone across matched articles is {features.mean_tone:.2f} "
        f"on the GDELT scale ({tone_desc}). "
        f"Tone deterioration — moving from neutral toward negative — "
        f"feeds the sentiment component, which carries 20% of the composite weight."
    )

    # 6. Concentration
    if features.concentration_score > 0.5:
        parts.append(
            f"Coverage concentration is high ({round(features.concentration_score * 100)}%), "
            f"meaning most matched articles cluster around a single disruption narrative. "
            f"Narrative concentration contributes 15% of the composite weighting and "
            f"increases the score when coverage is not diffuse."
        )
    else:
        parts.append(
            f"Coverage is distributed across multiple themes "
            f"(concentration: {round(features.concentration_score * 100)}%), "
            f"which limits the concentration component's upward contribution to the score."
        )

    # 7. Monte Carlo model response
    if regime == "Normal":
        parts.append(
            "Because the score falls below the Elevated threshold, the Monte Carlo model "
            "runs with unmodified drift and volatility — the forecast reflects "
            "baseline historical dynamics with no news-driven adjustment."
        )
    elif regime == "Elevated":
        parts.append(
            "In response to the Elevated regime, the Monte Carlo model applies "
            "a modest downward drift adjustment (daily mu \u22120.0003) and widens "
            "the volatility band by 12%. The forecast cone will be slightly broader "
            "and skewed toward the downside compared to a baseline run."
        )
    else:
        parts.append(
            "In response to the Severe regime, the Monte Carlo model applies "
            "a meaningful downward drift adjustment (daily mu \u22120.0010) and widens "
            "the volatility band by 30%. The forecast cone will be materially broader "
            "with significant downside exposure — treat percentile ranges with caution "
            "in this regime."
        )

    return " ".join(parts)


def generate_top_drivers(features: EventFeatureSet, regime: str) -> list[str]:
    """
    Bullet-point list of the primary factors behind the score.
    Each bullet is a concrete, metric-grounded statement.
    """
    drivers: list[str] = []

    baseline_multiple = round(features.count_72h / 5, 1)
    if features.count_72h == 0:
        drivers.append("No matching articles detected in the 72-hour window")
    elif baseline_multiple >= 1.5:
        drivers.append(
            f"Article volume is {baseline_multiple}x the 7-day baseline "
            f"({features.count_72h} articles in 72 h)"
        )
    else:
        drivers.append(
            f"Article volume is near baseline "
            f"({features.count_72h} articles in 72 h, {features.count_7d} over 7 days)"
        )

    if features.high_severity_count > 0:
        drivers.append(
            f"{features.high_severity_count} high-severity article"
            f"{'s' if features.high_severity_count != 1 else ''} detected "
            f"(conflict / sanctions / attack / piracy)"
        )

    if features.disruption_count > 0:
        drivers.append(
            f"{features.disruption_count} disruption-linked article"
            f"{'s' if features.disruption_count != 1 else ''} "
            f"(strikes, closures, canal / chokepoint events, rerouting)"
        )

    if features.mean_tone < -1.0:
        drivers.append(
            f"Average news tone is {features.mean_tone:.2f} "
            f"({'strongly ' if features.mean_tone < -3 else ''}negative for this lane)"
        )
    elif features.mean_tone > 0.5:
        drivers.append(
            f"Average news tone is {features.mean_tone:.2f} (positive — reduces risk pressure)"
        )

    if features.concentration_score > 0.5:
        pct = round(features.concentration_score * 100)
        drivers.append(
            f"Coverage is {pct}% concentrated around a dominant disruption theme"
        )

    dominant_themes = sorted(
        features.theme_counts.items(),
        key=lambda x: x[1][0],
        reverse=True,
    )[:2]
    if dominant_themes:
        theme_names = " and ".join(t for t, _ in dominant_themes)
        drivers.append(f"Top coverage themes: {theme_names}")

    if regime != "Normal":
        drivers.append(
            f"Monte Carlo model adjusted: "
            f"{'mu \u22120.0003, \u03c3 \u00d71.12' if regime == 'Elevated' else 'mu \u22120.0010, \u03c3 \u00d71.30'}"
        )

    return drivers