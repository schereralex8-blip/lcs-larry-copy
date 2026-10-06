"""Walk-forward evaluation: predict every series before updating on its result."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from math import log

from larrybot import series
from larrybot.ratings import EloConfig, EloModel
from larrybot.storage import Match


@dataclass
class Report:
    config: EloConfig
    n: int
    accuracy: float
    log_loss: float
    brier: float
    calibration: list[tuple[str, int, float, float]]  # bucket, count, mean predicted, actual win rate

    def summary(self) -> str:
        c = self.config
        return (
            f"k={c.k:g} prov_k={c.provisional_k:g} decay={c.decay_per_30d:g} | n={self.n} "
            f"acc={self.accuracy:.1%} logloss={self.log_loss:.4f} brier={self.brier:.4f}"
        )


def run(matches: list[Match], config: EloConfig, min_maps: int = 20, warmup: float = 0.3) -> Report:
    """Score predictions on the matches after the first `warmup` share (ratings need time to settle)."""
    model = EloModel(config=config)
    start = int(len(matches) * warmup)
    preds: list[tuple[float, int]] = []
    for i, m in enumerate(matches):
        ta, tb = model.known(m.team_a), model.known(m.team_b)
        if i >= start and ta and tb and ta.maps_played >= min_maps and tb.maps_played >= min_maps:
            p = series.series_win_prob(model.map_prob(m.team_a, m.team_b, m.played_at), m.best_of)
            preds.append((p, int(m.score_a > m.score_b)))
        model.update(m.team_a, m.team_b, m.score_a, m.score_b, m.played_at)
    return _score(config, preds)


def _score(config: EloConfig, preds: list[tuple[float, int]]) -> Report:
    n = len(preds)
    if n == 0:
        return Report(config, 0, 0.0, float("inf"), 1.0, [])
    eps = 1e-9
    acc = sum((p > 0.5) == bool(y) for p, y in preds) / n
    ll = -sum(y * log(max(p, eps)) + (1 - y) * log(max(1 - p, eps)) for p, y in preds) / n
    brier = sum((p - y) ** 2 for p, y in preds) / n
    calib = []
    for lo in range(0, 100, 10):
        bucket = [(p, y) for p, y in preds if lo / 100 <= p < (lo + 10) / 100 or (lo == 90 and p == 1)]
        if bucket:
            calib.append((f"{lo}-{lo + 10}%", len(bucket), sum(p for p, _ in bucket) / len(bucket), sum(y for _, y in bucket) / len(bucket)))
    return Report(config, n, acc, ll, brier, calib)


def tune(matches: list[Match], min_maps: int = 20) -> list[Report]:
    """Grid-search Elo settings; returns reports sorted best (lowest log loss) first."""
    reports = []
    for k, prov, decay in itertools.product((12, 18, 24, 32, 40), (32, 48, 64), (0.0, 0.05, 0.10, 0.20)):
        reports.append(run(matches, EloConfig(k=k, provisional_k=prov, decay_per_30d=decay), min_maps))
    return sorted(reports, key=lambda r: r.log_loss)
