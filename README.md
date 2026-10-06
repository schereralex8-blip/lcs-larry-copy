# larrybot — DIY esports +EV model

A self-hosted esports betting model for CS2, League of Legends, Valorant, Dota 2,
CoD and R6. It rates teams from match history, turns ratings into prices for
moneylines, map handicaps and map totals, compares those prices to your
sportsbook's lines, and flags the +EV plays with a Kelly-sized stake. Picks can
go to a Discord channel and get logged to a tracker that reports your ROI.

This is an original model. It is **not** a copy of LCSLarry / Juiced Bets. Their
model is private and paid, and nothing here comes from it. The approach is the
same general one any "probability vs. price" service uses.

Pure Python 3.10+ standard library. Nothing to install except `pytest` to run the tests.

## How it works

1. **Ratings** (`larrybot/ratings.py`): Elo per game, updated once per *map*, so
   a 2-0 counts for more than a 2-1. New teams get a higher K until their rating
   settles. Inactive teams drift back toward average.
2. **Series pricing** (`larrybot/series.py`): a single-map win probability becomes
   the exact score distribution for Bo1/Bo3/Bo5, which gives ML, ±1.5 map
   handicaps and over/under map totals.
3. **Edge finding** (`larrybot/picks.py`): your line is de-vigged (power method)
   when you supply the other side's odds. The model probability is blended with
   the market's fair probability (`--market-weight`, default 0.5), and a play is
   kept only if EV ≥ `--min-edge`. Teams with thin history are skipped.
4. **Staking** (`larrybot/odds.py`): quarter-Kelly, capped at 3% of bankroll by default.
5. **Validation** (`larrybot/backtest.py`): a walk-forward backtest reports
   accuracy, log loss, Brier score and calibration, and grid-searches the Elo settings.

## Setup

```bash
# 1. Match history: free PandaScore token from https://pandascore.co
export PANDASCORE_TOKEN=...
python -m larrybot fetch cs2 lol valorant --pages 30

#    ...or import your own CSV: game,date,team_a,team_b,score_a,score_b[,best_of][,id]
python -m larrybot import my_matches.csv

# 2. Tune and validate per game (saves the best settings to elo_params.json)
python -m larrybot backtest cs2 --tune --save

# 3. Team names differ between data sources and books: map them here
cp aliases.example.json aliases.json
```

## Automatic odds (no typing)

Lines come from [Odds-API.io](https://odds-api.io). The free tier covers esports
with 2 bookmakers and 100 requests/hour.

```bash
export ODDS_API_KEY=...
# One scan: best price across the books you bet at, de-vigged against a sharp book
python -m larrybot live --books Bet365 --sharp Pinnacle --bankroll 1000 -v

# Keep running: re-check every 15 minutes, post only NEW plays to Discord, and log them
python -m larrybot live --books Bet365 --sharp Pinnacle --bankroll 1000 --watch 15 --discord --log
```

- `--books`: the books you can actually bet at. The bot takes the best price per side across them.
- `--sharp`: optional. Its de-vigged price becomes the market probability, which
  works better than de-vigging your own book's line. On the free tier,
  `--books` plus `--sharp` together can only name 2 books.
- Bookmaker names must match Odds-API.io's spelling (for example `Bet365`, `Pinnacle`).
- **Series length:** the odds feed doesn't say whether a match is Bo1, Bo3 or Bo5.
  The bot reads it from PandaScore's schedule when `PANDASCORE_TOKEN` is set.
  Otherwise it infers it from map lines (±1.5 maps or 2.5 total → Bo3; 3.5/4.5 → Bo5).
  If neither works, it skips the match unless you pass `--assume-best-of`.
  Guessing wrong between Bo1 and Bo3 produces fake edges, so leave that flag off when you can.
- Only series moneylines, map handicaps and map totals are used. Round handicaps,
  kill lines and "Map 1 winner" markets are ignored.
- Each pass costs about 1 request plus 1 per 10 matches. `--watch 15` stays well
  inside the free limit.
- Add the odds feed's team spellings to `aliases.json` when `-v` shows "not enough data" for a team you know.

## Manual odds

```bash
python -m larrybot ratings cs2 --top 20
python -m larrybot predict cs2 "Team Vitality" "Natus Vincere" --best-of 3

# Put today's lines in odds.csv (format: odds_example.csv; American or decimal odds)
python -m larrybot scan odds.csv --bankroll 1000 -v
python -m larrybot scan odds.csv --bankroll 1000 --discord --log   # DISCORD_WEBHOOK_URL must be set

python -m larrybot bets            # tracker + ROI
python -m larrybot settle 3 win    # win | loss | push
```

`odds.csv` columns: `game,team_a,team_b,best_of,market,selection,line,odds,opp_odds,start`

- `market`: `ml`, `hcap` (with `line` like `-1.5`) or `total` (selection `over`/`under`, `line` like `2.5`)
- `opp_odds`: the other side's price. Optional, but without it the bot can't
  de-vig or blend with the market, so edges come out much noisier.

## Honest expectations

- Elo on results alone is a baseline. Sharp esports lines already price in
  rosters, stand-ins, map pools, patches and LAN vs. online. Run the backtest
  and look at calibration before you bet real money. If the model is
  overconfident, raise `--market-weight`.
- Big "edges" against a sharp line usually point to a data problem (wrong team
  match, roster change), not free money. Check before you bet.
- Track every bet with `--log` / `settle` and judge it on a few hundred bets,
  not a hot week.

Ideas for improving it: per-map ratings (CS2/Valorant map pools), roster-change
resets, and a LAN/online adjustment.

## Tests

```bash
pip install pytest && python -m pytest -q
```
