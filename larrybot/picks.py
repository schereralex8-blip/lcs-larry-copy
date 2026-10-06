"""Price bookmaker lines with the model and keep the +EV ones."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from larrybot import odds as oddsmath
from larrybot import series
from larrybot.ratings import EloConfig, EloModel, normalize_team
from larrybot.storage import Store


def load_aliases(path: str | Path | None) -> dict[str, str]:
    """JSON of {"any spelling": "canonical name"}; both sides are normalized."""
    if not path or not Path(path).exists():
        return {}
    raw = json.loads(Path(path).read_text())
    return {normalize_team(k): normalize_team(v) for k, v in raw.items()}


def build_model(store: Store, game: str, config: EloConfig | None = None, aliases: dict[str, str] | None = None) -> EloModel:
    model = EloModel(config=config or EloConfig(), aliases=aliases or {})
    for m in store.matches(game):
        model.update(m.team_a, m.team_b, m.score_a, m.score_b, m.played_at)
    return model


@dataclass
class Line:
    game: str
    team_a: str
    team_b: str
    best_of: int
    market: str  # ml | hcap | total
    selection: str  # a team name, or over/under for totals
    line: float | None
    odds: float  # decimal
    opp_odds: float | None  # decimal odds of the other side, used to de-vig
    start: str = ""

    @property
    def event(self) -> str:
        return f"{self.team_a} vs {self.team_b} (Bo{self.best_of})"

    @property
    def label(self) -> str:
        if self.market == "ml":
            return f"{self.selection} ML"
        if self.market == "hcap":
            return f"{self.selection} {self.line:+g} maps"
        return f"{self.selection.title()} {self.line:g} maps"


@dataclass
class Pick:
    line: Line
    model_prob: float
    market_prob: float | None
    prob: float  # final probability used for EV (model blended with market)
    ev: float
    stake_frac: float


def read_lines(path: str | Path) -> list[Line]:
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("odds"):
                continue
            out.append(Line(
                game=row["game"].lower(),
                team_a=row["team_a"],
                team_b=row["team_b"],
                best_of=int(row.get("best_of") or 1),
                market=(row.get("market") or "ml").lower(),
                selection=row.get("selection") or row["team_a"],
                line=float(row["line"]) if row.get("line") else None,
                odds=oddsmath.parse_odds(row["odds"]),
                opp_odds=oddsmath.parse_odds(row["opp_odds"]) if row.get("opp_odds") else None,
                start=row.get("start", ""),
            ))
    return out


def model_prob(model: EloModel, ln: Line, when: datetime | None = None) -> float:
    p_a = model.map_prob(ln.team_a, ln.team_b, when)
    if ln.market == "total":
        if ln.line is None:
            raise ValueError(f"total needs a line: {ln.event}")
        over = series.total_maps_over_prob(p_a, ln.best_of, ln.line)
        return over if ln.selection.lower().startswith("o") else 1 - over
    sel = model.key(ln.selection)
    if sel == model.key(ln.team_a):
        p_sel = p_a
    elif sel == model.key(ln.team_b):
        p_sel = 1 - p_a
    else:
        raise ValueError(f"selection {ln.selection!r} is neither team in {ln.event}")
    if ln.market == "ml":
        return series.series_win_prob(p_sel, ln.best_of)
    if ln.market == "hcap":
        if ln.line is None:
            raise ValueError(f"handicap needs a line: {ln.event}")
        return series.handicap_prob(p_sel, ln.best_of, ln.line)
    raise ValueError(f"unknown market {ln.market!r}")


def evaluate(
    models: dict[str, EloModel],
    lines: list[Line],
    min_maps: int = 20,
    min_edge: float = 0.03,
    market_weight: float = 0.5,
    kelly: float = 0.25,
    max_stake: float = 0.03,
    devig: str = "power",
) -> tuple[list[Pick], list[str]]:
    """Return (+EV picks sorted best first, list of skipped-line reasons)."""
    picks, skipped = [], []
    now = datetime.now(timezone.utc)
    for ln in lines:
        model = models.get(ln.game)
        if model is None:
            skipped.append(f"{ln.event}: no match history for game {ln.game!r}")
            continue
        thin = [t for t in (ln.team_a, ln.team_b) if (model.known(t) is None or model.known(t).maps_played < min_maps)]
        if thin:
            skipped.append(f"{ln.event}: not enough data for {', '.join(thin)}")
            continue
        try:
            mp = model_prob(model, ln, now)
        except ValueError as e:
            skipped.append(str(e))
            continue
        market = oddsmath.devig_two_way(ln.odds, ln.opp_odds, devig)[0] if ln.opp_odds else None
        prob = mp if market is None else market_weight * market + (1 - market_weight) * mp
        ev = oddsmath.expected_value(prob, ln.odds)
        if ev >= min_edge:
            picks.append(Pick(ln, mp, market, prob, ev, oddsmath.kelly_fraction(prob, ln.odds, kelly, max_stake)))
    picks.sort(key=lambda p: p.ev, reverse=True)
    return picks, skipped


def format_pick(p: Pick, bankroll: float | None = None) -> str:
    mkt = f"mkt {p.market_prob:5.1%}" if p.market_prob is not None else "mkt   n/a"
    stake = f"{p.stake_frac:.2%} br" if bankroll is None else f"${bankroll * p.stake_frac:,.2f}"
    return (
        f"[{p.line.game.upper()}] {p.line.event}: {p.line.label} @ {oddsmath.decimal_to_american(p.line.odds):+d} "
        f"({p.line.odds:.2f}) | model {p.model_prob:5.1%} {mkt} -> EV {p.ev:+.1%} | stake {stake}"
    )
