import random
from datetime import datetime, timedelta, timezone

import pytest

from larrybot import backtest, odds, picks, series
from larrybot.ratings import EloConfig, EloModel, normalize_team
from larrybot.storage import Match, Store


def test_odds_conversion_roundtrip():
    assert odds.american_to_decimal(150) == pytest.approx(2.5)
    assert odds.american_to_decimal(-200) == pytest.approx(1.5)
    assert odds.decimal_to_american(2.5) == 150
    assert odds.decimal_to_american(1.5) == -200
    assert odds.parse_odds("+150") == pytest.approx(2.5)
    assert odds.parse_odds("-120") == pytest.approx(1 + 100 / 120)
    assert odds.parse_odds("1.91") == pytest.approx(1.91)


@pytest.mark.parametrize("method", ["power", "multiplicative"])
def test_devig_sums_to_one_and_favors_favorite(method):
    a, b = odds.devig_two_way(1.4, 2.9, method)
    assert a + b == pytest.approx(1)
    assert a > 1 / 2.9 and a < 1 / 1.4


def test_ev_and_kelly():
    assert odds.expected_value(0.55, 2.0) == pytest.approx(0.10)
    assert odds.kelly_fraction(0.55, 2.0, multiplier=1, cap=1) == pytest.approx(0.10)
    assert odds.kelly_fraction(0.45, 2.0) == 0
    assert odds.kelly_fraction(0.9, 2.0) == 0.03  # capped


def test_series_math():
    assert series.series_win_prob(0.6, 1) == pytest.approx(0.6)
    assert series.series_win_prob(0.6, 3) == pytest.approx(0.6**2 + 2 * 0.6**2 * 0.4)
    for bo in (1, 3, 5):
        assert sum(series.score_distribution(0.63, bo).values()) == pytest.approx(1)
    assert series.handicap_prob(0.6, 3, -1.5) == pytest.approx(0.36)
    assert series.handicap_prob(0.6, 3, 1.5) == pytest.approx(1 - 0.4**2)
    assert series.total_maps_over_prob(0.6, 3, 2.5) == pytest.approx(2 * 0.6 * 0.4)
    with pytest.raises(ValueError):
        series.score_distribution(0.5, 2)


def test_normalize_team():
    assert normalize_team("Team Liquid") == normalize_team("Liquid")
    assert normalize_team("G2 Esports") == "g2"
    assert normalize_team("FaZe Clan") == "fazeclan"


def test_elo_winner_gains_and_sweep_counts_more():
    m = EloModel(EloConfig(provisional_maps=0))
    m.update("A", "B", 2, 0)
    m2 = EloModel(EloConfig(provisional_maps=0))
    m2.update("A", "B", 2, 1)
    assert m.team("A").rating > m2.team("A").rating > 1500
    assert m.team("A").rating + m.team("B").rating == pytest.approx(3000)


def test_inactivity_decay_pulls_toward_mean():
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    m = EloModel(EloConfig(decay_per_30d=0.5))
    for _ in range(10):
        m.update("A", "B", 2, 0, t0)
    fresh = m.map_prob("A", "B", t0)
    stale = m.map_prob("A", "B", t0 + timedelta(days=90))
    assert 0.5 < stale < fresh


def simulate(n_teams=16, n_matches=3000, seed=7):
    rng = random.Random(seed)
    strength = {f"Team{i}": rng.gauss(0, 150) for i in range(n_teams)}
    names = list(strength)
    t = datetime(2025, 1, 1, tzinfo=timezone.utc)
    out = []
    for i in range(n_matches):
        a, b = rng.sample(names, 2)
        p = 1 / (1 + 10 ** ((strength[b] - strength[a]) / 400))
        bo = rng.choice([1, 3, 3, 5])
        need, wa, wb = bo // 2 + 1, 0, 0
        while wa < need and wb < need:
            if rng.random() < p:
                wa += 1
            else:
                wb += 1
        t += timedelta(hours=6)
        out.append(Match(f"sim{i}", "cs2", t, a, b, wa, wb, bo))
    return out, strength


def test_backtest_on_simulated_league_is_calibrated():
    matches, strength = simulate()
    rep = backtest.run(matches, EloConfig(k=12))
    assert rep.n > 1500
    assert rep.accuracy > 0.6
    assert rep.log_loss < 0.66  # better than a coin flip (0.693)
    for _, n, pred, actual in rep.calibration:
        if n >= 100:
            assert abs(pred - actual) < 0.08
    assert backtest.tune(matches)[0].log_loss <= rep.log_loss
    model = EloModel()
    for m in matches:
        model.update(m.team_a, m.team_b, m.score_a, m.score_b, m.played_at)
    best = max(strength, key=strength.get)
    assert model.table()[0][0] in sorted(strength, key=strength.get)[-3:]
    assert best in [r[0] for r in model.table()[:3]]


def test_scan_finds_mispriced_line(tmp_path):
    matches, strength = simulate()
    store = Store(tmp_path / "t.db")
    store.upsert_matches(matches)
    fav, dog = max(strength, key=strength.get), min(strength, key=strength.get)
    csv_path = tmp_path / "odds.csv"
    csv_path.write_text(
        "game,team_a,team_b,best_of,market,selection,line,odds,opp_odds\n"
        f"cs2,{fav},{dog},3,ml,{fav},,+150,-180\n"  # favourite badly underpriced
        f"cs2,{fav},{dog},3,ml,{dog},,+900,-2000\n"
        f"cs2,{fav},Unknown Team,3,ml,{fav},,+100,-120\n"
        f"cs2,{fav},{dog},3,hcap,{fav},-1.5,+300,-400\n"
        f"cs2,{fav},{dog},3,total,over,2.5,+120,-150\n"
    )
    lines = picks.read_lines(csv_path)
    found, skipped = picks.evaluate({"cs2": picks.build_model(store, "cs2")}, lines, market_weight=0.0)
    labels = [p.line.label for p in found]
    assert f"{fav} ML" in labels
    assert f"{dog} ML" not in labels
    assert any("Unknown Team" in s for s in skipped)
    assert all(p.ev >= 0.03 and 0 < p.stake_frac <= 0.03 for p in found)
    store.close()


def test_bet_tracker(tmp_path):
    store = Store(tmp_path / "t.db")
    bid = store.add_bet("cs2", "A vs B", "ml", "A ML", 2.5, 0.5, 10)
    assert store.settle_bet(bid, "win") == pytest.approx(15)
    assert store.bets(open_only=True) == []
    store.close()
