"""Turn a single-map win probability into series-level market probabilities."""

from __future__ import annotations

from math import comb


def score_distribution(p_map: float, best_of: int) -> dict[tuple[int, int], float]:
    """Probability of each final series score (maps_a, maps_b), assuming independent maps."""
    if best_of < 1 or best_of % 2 == 0:
        raise ValueError(f"best_of must be a positive odd number, got {best_of}")
    need = best_of // 2 + 1
    q = 1 - p_map
    dist = {}
    for lost in range(need):
        ways = comb(need - 1 + lost, lost)
        dist[(need, lost)] = ways * p_map**need * q**lost
        dist[(lost, need)] = ways * q**need * p_map**lost
    return dist


def series_win_prob(p_map: float, best_of: int) -> float:
    return sum(p for (a, b), p in score_distribution(p_map, best_of).items() if a > b)


def handicap_prob(p_map: float, best_of: int, line: float) -> float:
    """P(team A covers a map handicap), e.g. line=-1.5 means A must win by 2+ maps."""
    return sum(p for (a, b), p in score_distribution(p_map, best_of).items() if a - b + line > 0)


def total_maps_over_prob(p_map: float, best_of: int, line: float) -> float:
    return sum(p for (a, b), p in score_distribution(p_map, best_of).items() if a + b > line)
