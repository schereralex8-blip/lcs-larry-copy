"""Command-line entry point: python -m larrybot <command> ..."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

from larrybot import backtest, picks, series
from larrybot.ratings import EloConfig
from larrybot.storage import Match, Store, parse_time

PARAMS_FILE = "elo_params.json"


def _load_config(game: str, path: str = PARAMS_FILE) -> EloConfig:
    p = Path(path)
    if p.exists():
        saved = json.loads(p.read_text()).get(game)
        if saved:
            return EloConfig(**saved)
    return EloConfig()


def _model(store: Store, game: str, aliases_path: str | None):
    return picks.build_model(store, game, _load_config(game), picks.load_aliases(aliases_path))


def cmd_fetch(args, store: Store) -> None:
    from larrybot import pandascore

    for game in args.games:
        matches = pandascore.fetch_past(game, pages=args.pages)
        store.upsert_matches(matches)
        print(f"{game}: stored {len(matches)} matches")


def cmd_import(args, store: Store) -> None:
    """CSV columns: game,date,team_a,team_b,score_a,score_b[,best_of][,id]"""
    rows = []
    with open(args.csv, newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            sa, sb = int(r["score_a"]), int(r["score_b"])
            bo = int(r.get("best_of") or 0) or max(2 * max(sa, sb) - 1, 1)
            mid = r.get("id") or f"csv-{r['game']}-{r['date']}-{r['team_a']}-{r['team_b']}-{i}"
            rows.append(Match(mid, r["game"].lower(), parse_time(r["date"]), r["team_a"], r["team_b"], sa, sb, bo))
    store.upsert_matches(rows)
    print(f"imported {len(rows)} matches")


def cmd_ratings(args, store: Store) -> None:
    model = _model(store, args.game, args.aliases)
    from datetime import datetime, timezone

    for i, (name, rating, maps) in enumerate(model.table(datetime.now(timezone.utc), args.min_maps)[: args.top], 1):
        print(f"{i:3}. {name:<28} {rating:7.1f}  ({maps} maps)")


def cmd_predict(args, store: Store) -> None:
    model = _model(store, args.game, args.aliases)
    from datetime import datetime, timezone

    p = model.map_prob(args.team_a, args.team_b, datetime.now(timezone.utc))
    print(f"{args.team_a} vs {args.team_b} (Bo{args.best_of})")
    print(f"  map win:    {p:6.1%} / {1 - p:6.1%}")
    print(f"  series win: {series.series_win_prob(p, args.best_of):6.1%} / {series.series_win_prob(1 - p, args.best_of):6.1%}")
    for (a, b), q in sorted(series.score_distribution(p, args.best_of).items(), key=lambda kv: -kv[1]):
        print(f"  {a}-{b}: {q:6.1%}")
    for t in (args.team_a, args.team_b):
        s = model.known(t)
        if s is None or s.maps_played < 20:
            print(f"  warning: only {s.maps_played if s else 0} maps of history for {t}")


def cmd_scan(args, store: Store) -> None:
    lines = picks.read_lines(args.odds)
    models = {g: _model(store, g, args.aliases) for g in {ln.game for ln in lines} if store.matches(g)}
    found, skipped = picks.evaluate(
        models, lines, min_maps=args.min_maps, min_edge=args.min_edge, market_weight=args.market_weight,
        kelly=args.kelly, max_stake=args.max_stake,
    )
    out = [picks.format_pick(p, args.bankroll) for p in found] or ["no +EV plays found"]
    print("\n".join(out))
    if skipped and args.verbose:
        print("\nskipped:\n  " + "\n  ".join(skipped), file=sys.stderr)
    if args.discord and found:
        from larrybot.alerts import send_discord

        send_discord(out)
        print("sent to Discord")
    if args.log:
        for p in found:
            stake = p.stake_frac * (args.bankroll or 100)
            store.add_bet(p.line.game, p.line.event, p.line.market, p.line.label, p.line.odds, p.prob, stake)
        print(f"logged {len(found)} bets")


def cmd_backtest(args, store: Store) -> None:
    matches = store.matches(args.game)
    if args.tune:
        reports = backtest.tune(matches, args.min_maps)
        for r in reports[:10]:
            print(r.summary())
        best = reports[0]
        if args.save:
            p = Path(PARAMS_FILE)
            saved = json.loads(p.read_text()) if p.exists() else {}
            saved[args.game] = asdict(best.config)
            p.write_text(json.dumps(saved, indent=2) + "\n")
            print(f"saved best params for {args.game} to {PARAMS_FILE}")
    else:
        best = backtest.run(matches, _load_config(args.game), args.min_maps)
        print(best.summary())
    print("calibration (predicted vs actual):")
    for bucket, n, pred, actual in best.calibration:
        print(f"  {bucket:>8}: n={n:5}  predicted {pred:6.1%}  actual {actual:6.1%}")


def cmd_bets(args, store: Store) -> None:
    rows = store.bets(open_only=args.open)
    staked = profit = 0.0
    for r in rows:
        res = r["result"] or "open"
        print(f"#{r['id']:<4} {r['created_at'][:10]} {r['game']:<8} {r['selection']:<28} @ {r['odds']:.2f}  stake {r['stake']:.2f}  {res}")
        if r["result"]:
            staked += r["stake"]
            profit += r["profit"]
    if staked:
        print(f"settled: staked {staked:.2f}, profit {profit:+.2f}, ROI {profit / staked:+.1%}")


def cmd_settle(args, store: Store) -> None:
    print(f"bet #{args.id}: {args.result}, profit {store.settle_bet(args.id, args.result):+.2f}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="larrybot", description="Esports +EV betting model")
    ap.add_argument("--db", default="larrybot.db", help="SQLite database path")
    ap.add_argument("--aliases", default="aliases.json", help="team-name alias JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch", help="download match history from PandaScore")
    s.add_argument("games", nargs="+", choices=["cs2", "lol", "valorant", "dota2", "cod", "r6"])
    s.add_argument("--pages", type=int, default=20, help="100 matches per page")
    s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("import", help="import match history from a CSV")
    s.add_argument("csv")
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("ratings", help="show the rating table")
    s.add_argument("game")
    s.add_argument("--top", type=int, default=30)
    s.add_argument("--min-maps", type=int, default=20)
    s.set_defaults(fn=cmd_ratings)

    s = sub.add_parser("predict", help="price a single matchup")
    s.add_argument("game")
    s.add_argument("team_a")
    s.add_argument("team_b")
    s.add_argument("--best-of", type=int, default=3)
    s.set_defaults(fn=cmd_predict)

    s = sub.add_parser("scan", help="find +EV plays in an odds CSV")
    s.add_argument("odds", help="CSV of lines (see odds_example.csv)")
    s.add_argument("--min-edge", type=float, default=0.03, help="minimum EV per unit, e.g. 0.03 = 3%%")
    s.add_argument("--market-weight", type=float, default=0.5, help="how much to trust the de-vigged line (0-1)")
    s.add_argument("--min-maps", type=int, default=20, help="skip teams with fewer maps of history")
    s.add_argument("--kelly", type=float, default=0.25, help="Kelly multiplier")
    s.add_argument("--max-stake", type=float, default=0.03, help="max stake as share of bankroll")
    s.add_argument("--bankroll", type=float, help="show stakes in dollars")
    s.add_argument("--discord", action="store_true", help="post picks to DISCORD_WEBHOOK_URL")
    s.add_argument("--log", action="store_true", help="record picks in the bet tracker")
    s.add_argument("-v", "--verbose", action="store_true", help="show skipped lines")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("backtest", help="walk-forward accuracy and calibration")
    s.add_argument("game")
    s.add_argument("--min-maps", type=int, default=20)
    s.add_argument("--tune", action="store_true", help="grid-search Elo parameters")
    s.add_argument("--save", action="store_true", help="with --tune, save best params for scan/predict")
    s.set_defaults(fn=cmd_backtest)

    s = sub.add_parser("bets", help="list tracked bets and ROI")
    s.add_argument("--open", action="store_true")
    s.set_defaults(fn=cmd_bets)

    s = sub.add_parser("settle", help="settle a tracked bet")
    s.add_argument("id", type=int)
    s.add_argument("result", choices=["win", "loss", "push"])
    s.set_defaults(fn=cmd_settle)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(args.db)
    try:
        args.fn(args, store)
    finally:
        store.close()
    return 0
