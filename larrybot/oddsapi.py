"""Pull live esports lines from Odds-API.io (free tier: 2 bookmakers, 100 requests/hour).

Get a key at https://odds-api.io and set ODDS_API_KEY. Each fetch costs
1 request for the event list plus 1 per 10 events for odds.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from larrybot import odds as oddsmath
from larrybot.picks import Line

API = os.environ.get("ODDS_API_BASE", "https://api.odds-api.io/v3")

# Matched against league/sport names and slugs, in order.
GAME_PATTERNS = [
    ("cs2", r"counter[\s-]?strike|\bcs2\b|\bcs:?go\b"),
    ("lol", r"league[\s-]of[\s-]legends|\blol\b|\blck\b|\blpl\b|\blec\b|\blcs\b"),
    ("valorant", r"valorant|\bvct\b"),
    ("dota2", r"dota"),
    ("cod", r"call[\s-]of[\s-]duty|\bcod\b"),
    ("r6", r"rainbow[\s-]?six|\br6\b"),
]

ML_NAMES = {"ml", "moneyline", "match winner", "winner", "match result"}
HCAP_NAMES = {"spread", "handicap", "map handicap", "maps handicap"}
TOTAL_NAMES = {"totals", "total", "total maps", "maps total", "over/under"}


def _get(path: str, params: dict, key: str) -> list | dict:
    url = f"{API}{path}?{urllib.parse.urlencode({**params, 'apiKey': key})}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "larrybot"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(2**attempt)
    return []


def detect_game(event: dict) -> str | None:
    parts = []
    for field in ("league", "sport"):
        v = event.get(field)
        if isinstance(v, dict):
            parts += [v.get("name", ""), v.get("slug", "")]
        elif v:
            parts.append(str(v))
    text = " ".join(parts).lower()
    for game, pattern in GAME_PATTERNS:
        if re.search(pattern, text):
            return game
    return None


def _kind(market_name: str) -> str | None:
    n = market_name.strip().lower()
    if n in ML_NAMES:
        return "ml"
    if n in HCAP_NAMES:
        return "hcap"
    if n in TOTAL_NAMES:
        return "total"
    return None


def _quotes(book_markets: list[dict]) -> dict[tuple, tuple[float, float]]:
    """One book's two-way prices: {(market, line): (side_1, side_2)}.

    Sides are (home, away) for ml/hcap, with hcap line given from the home team's view,
    and (over, under) for totals.
    """
    out = {}
    for m in book_markets:
        kind = _kind(m.get("name", ""))
        if kind is None:
            continue
        for o in m.get("odds") or []:
            try:
                if kind == "ml":
                    if o.get("draw") not in (None, "", "0"):
                        continue  # three-way market, not a series winner line
                    out[("ml", None)] = (float(o["home"]), float(o["away"]))
                elif kind == "hcap":
                    out[("hcap", float(o["hdp"]))] = (float(o["home"]), float(o["away"]))
                else:
                    line = float(o.get("hdp", o.get("line")))
                    out[("total", line)] = (float(o["over"]), float(o["under"]))
            except (KeyError, TypeError, ValueError):
                continue
    return {k: v for k, v in out.items() if min(v) > 1}


def infer_best_of(quotes: list[dict[tuple, tuple[float, float]]]) -> int | None:
    """Guess series length from map-handicap and map-total lines when nothing else says."""
    seen = {(market, abs(line)) for q in quotes for (market, line) in q if line is not None}
    if ("total", 3.5) in seen or ("total", 4.5) in seen or ("hcap", 2.5) in seen:
        return 5
    if ("total", 2.5) in seen or ("hcap", 1.5) in seen:
        return 3
    return None


def _valid_line(market: str, line: float | None, best_of: int) -> bool:
    """Drop round/kill handicaps and totals that share the market name with map lines."""
    if market == "ml":
        return True
    if line is None or abs(line) % 1 != 0.5:
        return False
    return abs(line) < best_of if market == "hcap" else 0 < line < best_of


def event_lines(
    event: dict,
    odds_event: dict,
    books: list[str],
    sharp: str | None,
    best_of: int | None,
    devig: str = "power",
    assume_best_of: int | None = None,
) -> tuple[list[Line], str | None]:
    """Turn one event's odds into Lines (best price across `books` per side).

    Returns (lines, reason) where reason explains why nothing was produced.
    """
    game = detect_game(event)
    if game is None:
        return [], "unrecognised game"
    by_book = {b: _quotes(ms) for b, ms in (odds_event.get("bookmakers") or {}).items()}
    bet_books = {b: q for b, q in by_book.items() if b in books}
    if not bet_books:
        return [], "none of your books price it"
    best_of = best_of or infer_best_of(list(by_book.values())) or assume_best_of
    if best_of is None:
        return [], "unknown series length (use --assume-best-of)"
    home, away = event["home"], event["away"]
    sharp_q = by_book.get(sharp, {}) if sharp else {}
    start = event.get("date", "")
    out = []
    keys = {k for q in bet_books.values() for k in q}
    for key in sorted(keys, key=lambda k: (k[0], k[1] if k[1] is not None else 0)):
        market, line = key
        if not _valid_line(market, line, best_of):
            continue
        fair = oddsmath.devig_two_way(*sharp_q[key], devig) if key in sharp_q else None
        for side in (0, 1):
            book, prices = max(((b, q[key]) for b, q in bet_books.items() if key in q), key=lambda bp: bp[1][side])
            if market == "total":
                selection, sel_line = ("over", "under")[side], line
            else:
                selection = (home, away)[side]
                sel_line = None if line is None else (line if side == 0 else -line)
            out.append(Line(
                game=game, team_a=home, team_b=away, best_of=best_of, market=market,
                selection=selection, line=sel_line, odds=prices[side], opp_odds=prices[1 - side],
                start=start, book=book, market_prob=None if fair is None else fair[side],
                event_id=str(event.get("id", "")),
            ))
    return out, None if out else "no map-level markets"


def fetch_lines(
    books: list[str],
    sharp: str | None = None,
    key: str | None = None,
    sport: str = "esports",
    games: set[str] | None = None,
    best_of_lookup: Callable[[str, str, str], int | None] | None = None,
    assume_best_of: int | None = None,
    max_events: int = 100,
) -> tuple[list[Line], list[str]]:
    """Fetch upcoming esports events and their odds. Returns (lines, skipped reasons)."""
    key = key or os.environ["ODDS_API_KEY"]
    events = _get("/events", {"sport": sport, "status": "pending", "limit": max_events}, key)
    events = [e for e in events if e.get("status", "pending") == "pending" and detect_game(e)]
    if games:
        events = [e for e in events if detect_game(e) in games]
    events = events[:max_events]
    wanted = ",".join(dict.fromkeys([*books, *([sharp] if sharp else [])]))
    odds_by_id: dict[str, dict] = {}
    ids = [str(e["id"]) for e in events]
    for i in range(0, len(ids), 10):
        chunk = _get("/odds/multi", {"eventIds": ",".join(ids[i : i + 10]), "bookmakers": wanted}, key)
        for o in chunk if isinstance(chunk, list) else [chunk]:
            odds_by_id[str(o.get("id", o.get("eventId")))] = o
    lines, skipped = [], []
    for e in events:
        o = odds_by_id.get(str(e["id"]))
        name = f"{e.get('home')} vs {e.get('away')}"
        if o is None:
            skipped.append(f"{name}: no odds returned")
            continue
        bo = best_of_lookup(detect_game(e), e["home"], e["away"]) if best_of_lookup else None
        got, reason = event_lines(e, o, books, sharp, bo, assume_best_of=assume_best_of)
        if reason:
            skipped.append(f"{name}: {reason}")
        lines.extend(got)
    return lines, skipped
