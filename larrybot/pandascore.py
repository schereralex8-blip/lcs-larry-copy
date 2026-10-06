"""Fetch finished and upcoming matches from the PandaScore API (free tier works).

Get a token at https://pandascore.co and set PANDASCORE_TOKEN.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from larrybot.storage import Match, parse_time

API = "https://api.pandascore.co"
# Our game names -> PandaScore slugs.
GAMES = {"cs2": "csgo", "lol": "lol", "valorant": "valorant", "dota2": "dota2", "cod": "codmw", "r6": "r6siege"}


def _get(path: str, params: dict, token: str) -> list[dict]:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(2**attempt)
    return []


def _to_match(game: str, m: dict) -> Match | None:
    opps = [o.get("opponent") or {} for o in m.get("opponents") or []]
    if len(opps) != 2 or m.get("forfeit") or not m.get("end_at"):
        return None
    scores = {r["team_id"]: r["score"] for r in m.get("results") or []}
    a, b = opps
    if a.get("id") not in scores or b.get("id") not in scores:
        return None
    sa, sb = scores[a["id"]], scores[b["id"]]
    if sa == sb == 0:
        return None
    best_of = m.get("number_of_games") or max(2 * max(sa, sb) - 1, 1)
    return Match(f"ps-{m['id']}", game, parse_time(m["end_at"]), a["name"], b["name"], sa, sb, best_of)


def fetch_past(game: str, pages: int = 10, token: str | None = None) -> list[Match]:
    token = token or os.environ["PANDASCORE_TOKEN"]
    out: list[Match] = []
    for page in range(1, pages + 1):
        rows = _get(f"/{GAMES[game]}/matches/past", {"page[size]": 100, "page[number]": page, "sort": "-end_at"}, token)
        out.extend(m for m in (_to_match(game, r) for r in rows) if m)
        if len(rows) < 100:
            break
    return out


def fetch_upcoming(game: str, token: str | None = None) -> list[dict]:
    token = token or os.environ["PANDASCORE_TOKEN"]
    rows = _get(f"/{GAMES[game]}/matches/upcoming", {"page[size]": 100, "sort": "begin_at"}, token)
    out = []
    for m in rows:
        opps = [o.get("opponent") or {} for o in m.get("opponents") or []]
        if len(opps) == 2:
            out.append({
                "begin_at": m.get("begin_at"),
                "team_a": opps[0]["name"],
                "team_b": opps[1]["name"],
                "best_of": m.get("number_of_games") or 1,
                "league": (m.get("league") or {}).get("name", ""),
            })
    return out
