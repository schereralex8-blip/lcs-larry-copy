"""Odds conversion, de-vigging, expected value and Kelly staking."""

from __future__ import annotations


def american_to_decimal(american: float) -> float:
    if american == 0 or -100 < american < 100:
        raise ValueError(f"invalid American odds: {american}")
    return 1 + american / 100 if american > 0 else 1 + 100 / -american


def decimal_to_american(dec: float) -> int:
    if dec <= 1:
        raise ValueError(f"invalid decimal odds: {dec}")
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def parse_odds(text: str | float) -> float:
    """Parse odds given as American (+150 / -120) or decimal (2.50); returns decimal."""
    s = str(text).strip()
    value = float(s)
    if s.startswith(("+", "-")) or abs(value) >= 100:
        return american_to_decimal(value)
    if value <= 1:
        raise ValueError(f"invalid decimal odds: {text}")
    return value


def implied_prob(dec: float) -> float:
    return 1 / dec


def devig_two_way(dec_a: float, dec_b: float, method: str = "power") -> tuple[float, float]:
    """Remove the bookmaker margin from a two-way market, returning fair probabilities.

    "multiplicative" scales implied probabilities proportionally. "power" finds k with
    pa**k + pb**k == 1, which shades more vig onto the longshot (closer to how books price).
    """
    pa, pb = implied_prob(dec_a), implied_prob(dec_b)
    if method == "multiplicative":
        total = pa + pb
        return pa / total, pb / total
    if method != "power":
        raise ValueError(f"unknown devig method: {method}")
    lo, hi = 0.01, 100.0
    for _ in range(200):
        k = (lo + hi) / 2
        if pa**k + pb**k > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    fa = pa**k
    return fa, 1 - fa


def expected_value(prob: float, dec: float) -> float:
    """Expected profit per 1 unit staked."""
    return prob * dec - 1


def kelly_fraction(prob: float, dec: float, multiplier: float = 0.25, cap: float = 0.03) -> float:
    """Fractional Kelly stake as a share of bankroll, capped. Returns 0 when there is no edge."""
    b = dec - 1
    full = (prob * b - (1 - prob)) / b
    if full <= 0:
        return 0.0
    return min(full * multiplier, cap)
