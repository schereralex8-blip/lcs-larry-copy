"""Map-level Elo ratings with inactivity decay and provisional K for new teams.

Each series updates ratings once per map played, so a 2-0 moves ratings more than a 2-1.
Ratings are kept separately per game (CS2, LoL, Valorant, ...).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

BASE = 1500.0


def normalize_team(name: str) -> str:
    """Canonical key for matching team names across data sources and sportsbooks."""
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for suffix in ("esports", "esport", "gaming", "team"):
        if key.endswith(suffix) and len(key) > len(suffix) + 1:
            key = key[: -len(suffix)]
    if key.startswith("team") and len(key) > 5:
        key = key[4:]
    return key


@dataclass
class TeamState:
    name: str
    rating: float = BASE
    maps_played: int = 0
    series_played: int = 0
    last_played: datetime | None = None


@dataclass
class EloConfig:
    k: float = 24.0
    provisional_k: float = 48.0
    provisional_maps: int = 15
    # Share of the distance to BASE recovered per 30 days of inactivity (roster churn, form loss).
    decay_per_30d: float = 0.10
    scale: float = 400.0


@dataclass
class EloModel:
    config: EloConfig = field(default_factory=EloConfig)
    teams: dict[str, TeamState] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    def key(self, name: str) -> str:
        k = normalize_team(name)
        return self.aliases.get(k, k)

    def team(self, name: str) -> TeamState:
        k = self.key(name)
        if k not in self.teams:
            self.teams[k] = TeamState(name=name)
        return self.teams[k]

    def known(self, name: str) -> TeamState | None:
        return self.teams.get(self.key(name))

    def _decayed(self, t: TeamState, when: datetime | None) -> float:
        if when is None or t.last_played is None:
            return t.rating
        days = max((when - t.last_played).total_seconds() / 86400, 0)
        keep = (1 - self.config.decay_per_30d) ** (days / 30)
        return BASE + (t.rating - BASE) * keep

    def map_prob(self, team_a: str, team_b: str, when: datetime | None = None) -> float:
        """P(team A wins a single map vs team B)."""
        ta, tb = self.team(team_a), self.team(team_b)
        ra, rb = self._decayed(ta, when), self._decayed(tb, when)
        return 1 / (1 + 10 ** ((rb - ra) / self.config.scale))

    def _k(self, t: TeamState) -> float:
        c = self.config
        return c.provisional_k if t.maps_played < c.provisional_maps else c.k

    def update(self, team_a: str, team_b: str, maps_a: int, maps_b: int, when: datetime | None = None) -> None:
        ta, tb = self.team(team_a), self.team(team_b)
        ta.rating, tb.rating = self._decayed(ta, when), self._decayed(tb, when)
        # Replay the series map by map, in an order-neutral way: alternate results so the
        # outcome does not depend on which maps happened first.
        results = _interleave(maps_a, maps_b)
        for a_won in results:
            p = 1 / (1 + 10 ** ((tb.rating - ta.rating) / self.config.scale))
            delta = (1.0 if a_won else 0.0) - p
            ta.rating += self._k(ta) * delta
            tb.rating -= self._k(tb) * delta
            ta.maps_played += 1
            tb.maps_played += 1
        ta.series_played += 1
        tb.series_played += 1
        if when is not None:
            ta.last_played = tb.last_played = when

    def table(self, when: datetime | None = None, min_maps: int = 0) -> list[tuple[str, float, int]]:
        rows = [(t.name, self._decayed(t, when), t.maps_played) for t in self.teams.values() if t.maps_played >= min_maps]
        return sorted(rows, key=lambda r: r[1], reverse=True)


def _interleave(wins: int, losses: int) -> list[bool]:
    out = []
    while wins or losses:
        if wins:
            out.append(True)
            wins -= 1
        if losses:
            out.append(False)
            losses -= 1
    return out
