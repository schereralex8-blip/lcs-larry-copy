import pytest

from larrybot import cli, oddsapi, picks
from larrybot.storage import Store
from test_core import simulate


def event(eid, home, away, league="Counter-Strike - BLAST Premier", slug="counter-strike-blast-premier"):
    return {
        "id": eid, "home": home, "away": away, "date": "2026-10-08T17:00:00Z", "status": "pending",
        "sport": {"name": "Esports", "slug": "esports"}, "league": {"name": league, "slug": slug},
    }


def book(ml=None, spread=(), totals=(), extra=()):
    ms = []
    if ml:
        ms.append({"name": "ML", "odds": [{"home": str(ml[0]), "away": str(ml[1])}]})
    if spread:
        ms.append({"name": "Spread", "odds": [{"hdp": h, "home": str(a), "away": str(b)} for h, a, b in spread]})
    if totals:
        ms.append({"name": "Totals", "odds": [{"hdp": h, "over": str(a), "under": str(b)} for h, a, b in totals]})
    return ms + list(extra)


@pytest.mark.parametrize("league,slug,game", [
    ("Counter-Strike - IEM Cologne", "counter-strike-iem", "cs2"),
    ("League of Legends - LCK", "league-of-legends-lck", "lol"),
    ("Valorant - VCT Americas", "valorant-vct", "valorant"),
    ("Dota 2 - The International", "dota-2-ti", "dota2"),
    ("Call of Duty League", "call-of-duty-league", "cod"),
    ("Esports - FIFA", "esports-fifa", None),
])
def test_detect_game(league, slug, game):
    assert oddsapi.detect_game(event(1, "A", "B", league, slug)) == game


def test_event_lines_best_price_sharp_fair_and_line_filtering():
    odds_event = {"id": 1, "bookmakers": {
        "Bet365": book(ml=(1.50, 2.60), spread=[(-1.5, 2.40, 1.55), (-4.5, 1.9, 1.9)], totals=[(2.5, 2.10, 1.70), (26.5, 1.9, 1.9)]),
        "DraftKings": book(ml=(1.55, 2.45), extra=[{"name": "Map 1 Winner", "odds": [{"home": "1.6", "away": "2.2"}]}]),
        "Pinnacle": book(ml=(1.57, 2.55), spread=[(-1.5, 2.55, 1.55)]),
    }}
    lines, reason = oddsapi.event_lines(event(1, "Vitality", "NAVI"), odds_event, ["Bet365", "DraftKings"], "Pinnacle", None)
    assert reason is None
    by_label = {ln.label: ln for ln in lines}
    # Bo3 inferred from the 1.5 map spread / 2.5 total; round lines (-4.5, 26.5) dropped.
    assert all(ln.best_of == 3 for ln in lines)
    assert set(by_label) == {"Vitality ML", "NAVI ML", "Vitality -1.5 maps", "NAVI +1.5 maps", "Over 2.5 maps", "Under 2.5 maps"}
    assert by_label["Vitality ML"].odds == 1.55 and by_label["Vitality ML"].book == "DraftKings"
    assert by_label["NAVI ML"].odds == 2.60 and by_label["NAVI ML"].book == "Bet365"
    fair = by_label["Vitality ML"].market_prob
    assert fair == pytest.approx(1 - by_label["NAVI ML"].market_prob)
    assert fair < 1 / 1.57 and 1 - fair < 1 / 2.55  # vig removed from both sides
    assert by_label["Over 2.5 maps"].market_prob is None  # sharp has no totals; falls back to same-book devig
    assert by_label["Over 2.5 maps"].opp_odds == 1.70


def test_event_lines_skips_unknown_series_length_unless_assumed():
    odds_event = {"id": 2, "bookmakers": {"Bet365": book(ml=(1.8, 2.0))}}
    ev = event(2, "A", "B")
    assert oddsapi.event_lines(ev, odds_event, ["Bet365"], None, None) == ([], "unknown series length (use --assume-best-of)")
    lines, _ = oddsapi.event_lines(ev, odds_event, ["Bet365"], None, None, assume_best_of=1)
    assert [ln.best_of for ln in lines] == [1, 1]
    lines, _ = oddsapi.event_lines(ev, odds_event, ["Bet365"], None, 5, assume_best_of=1)
    assert lines[0].best_of == 5  # schedule wins over the assumption


def test_three_way_ml_is_ignored():
    odds_event = {"id": 3, "bookmakers": {"Bet365": [{"name": "ML", "odds": [{"home": "2", "draw": "3.5", "away": "3"}]}]}}
    assert oddsapi.event_lines(event(3, "A", "B"), odds_event, ["Bet365"], None, 3) == ([], "no map-level markets")


def test_live_command_end_to_end(tmp_path, monkeypatch, capsys):
    matches, strength = simulate()
    db = tmp_path / "t.db"
    store = Store(db)
    store.upsert_matches(matches)
    store.close()
    fav, dog = max(strength, key=strength.get), min(strength, key=strength.get)
    events = [event(10, fav, dog), event(11, "Some Team", dog), event(12, "X", "Y", "Esports - FIFA", "fifa")]
    odds_rows = [
        {"id": 10, "bookmakers": {"Bet365": book(ml=(2.50, 1.55), spread=[(-1.5, 4.0, 1.25)]), "Pinnacle": book(ml=(1.30, 3.60))}},
        {"id": 11, "bookmakers": {"Bet365": book(ml=(1.9, 1.9), spread=[(-1.5, 3, 1.4)])}},
    ]
    calls = []

    def fake_get(path, params, key):
        calls.append((path, params))
        return events if path == "/events" else odds_rows

    monkeypatch.setattr(oddsapi, "_get", fake_get)
    monkeypatch.setenv("ODDS_API_KEY", "test")
    monkeypatch.delenv("PANDASCORE_TOKEN", raising=False)
    argv = ["--db", str(db), "--aliases", str(tmp_path / "none.json"), "live", "--books", "Bet365",
            "--sharp", "Pinnacle", "--market-weight", "0", "-v"]
    cli.main(argv)
    out, err = capsys.readouterr()
    assert f"{fav} ML @ +150 (2.50) [Bet365]" in out
    assert "not enough data for Some Team" in err
    assert calls[1][1]["bookmakers"] == "Bet365,Pinnacle"
    assert calls[1][1]["eventIds"] == "10,11"  # FIFA event filtered before the odds request

    # --new-only (used by the scheduled job) stays quiet on a play it already reported.
    cli.main(argv + ["--new-only"])
    assert " 0 new" not in capsys.readouterr().out
    cli.main(argv + ["--new-only"])
    assert "0 new +EV plays" in capsys.readouterr().out

    # Watch mode only alerts on plays it hasn't sent before.
    store = Store(db)
    found, _ = picks.evaluate({"cs2": picks.build_model(store, "cs2")}, oddsapi.fetch_lines(["Bet365"], "Pinnacle")[0], market_weight=0)
    keys = [picks.pick_key(p) for p in found]
    assert keys and store.new_alert_keys(keys) == set()  # already recorded by the --new-only run
    assert store.new_alert_keys(["fresh"]) == {"fresh"}
    store.close()
