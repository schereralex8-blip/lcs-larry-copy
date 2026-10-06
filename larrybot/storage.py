"""SQLite storage for match history and the bet tracker."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    played_at TEXT NOT NULL,
    team_a TEXT NOT NULL,
    team_b TEXT NOT NULL,
    score_a INTEGER NOT NULL,
    score_b INTEGER NOT NULL,
    best_of INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS matches_game_time ON matches (game, played_at);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    game TEXT NOT NULL,
    event TEXT NOT NULL,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    odds REAL NOT NULL,
    model_prob REAL NOT NULL,
    stake REAL NOT NULL,
    result TEXT,
    profit REAL,
    settled_at TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    key TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
);
"""


@dataclass
class Match:
    id: str
    game: str
    played_at: datetime
    team_a: str
    team_b: str
    score_a: int
    score_b: int
    best_of: int


def parse_time(text: str) -> datetime:
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def upsert_matches(self, matches: list[Match]) -> int:
        with self.conn:
            cur = self.conn.executemany(
                "INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?)",
                [(m.id, m.game, m.played_at.isoformat(), m.team_a, m.team_b, m.score_a, m.score_b, m.best_of) for m in matches],
            )
        return cur.rowcount

    def matches(self, game: str, before: datetime | None = None) -> list[Match]:
        sql, args = "SELECT * FROM matches WHERE game = ?", [game]
        if before is not None:
            sql += " AND played_at < ?"
            args.append(before.isoformat())
        rows = self.conn.execute(sql + " ORDER BY played_at, id", args).fetchall()
        return [
            Match(r["id"], r["game"], parse_time(r["played_at"]), r["team_a"], r["team_b"], r["score_a"], r["score_b"], r["best_of"])
            for r in rows
        ]

    def games(self) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self.conn.execute("SELECT game, COUNT(*) FROM matches GROUP BY game ORDER BY game")]

    def add_bet(self, game: str, event: str, market: str, selection: str, odds: float, model_prob: float, stake: float) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO bets (created_at, game, event, market, selection, odds, model_prob, stake) VALUES (?,?,?,?,?,?,?,?)",
                (now_iso(), game, event, market, selection, odds, model_prob, stake),
            )
        return cur.lastrowid

    def settle_bet(self, bet_id: int, result: str) -> float:
        row = self.conn.execute("SELECT odds, stake FROM bets WHERE id = ?", (bet_id,)).fetchone()
        if row is None:
            raise KeyError(f"no bet with id {bet_id}")
        profit = {"win": row["stake"] * (row["odds"] - 1), "loss": -row["stake"], "push": 0.0}[result]
        with self.conn:
            self.conn.execute("UPDATE bets SET result = ?, profit = ?, settled_at = ? WHERE id = ?", (result, profit, now_iso(), bet_id))
        return profit

    def new_alert_keys(self, keys: list[str]) -> set[str]:
        """Record alert keys; return the ones not seen before."""
        fresh = set()
        with self.conn:
            for k in keys:
                if self.conn.execute("INSERT OR IGNORE INTO alerts VALUES (?, ?)", (k, now_iso())).rowcount:
                    fresh.add(k)
        return fresh

    def bets(self, open_only: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT * FROM bets" + (" WHERE result IS NULL" if open_only else "") + " ORDER BY id"
        return self.conn.execute(sql).fetchall()
