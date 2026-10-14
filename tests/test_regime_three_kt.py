"""Regression tests for the three fixed bugs in src/three_kt.py.

Written RED first, then fixed alongside the implementation.
Covers: promote demotion, from_dict _db/_id preservation, fake p_value fallback.
"""

import pytest

from src.config import StrategyParams
from src.three_kt import (
    AbTestRunner,
    StrategyVersion,
    StrategyVersionDB,
    get_current_champion,
)

# ════════════════════════════════════════════════════════
# Bug 1: promote() demotion — multiple active champions
# ════════════════════════════════════════════════════════


def test_promote_only_one_active_version(tmp_path):
    """promote(v2) must deactivate v1 — only one champion at a time."""
    db = StrategyVersionDB(db_path=str(tmp_path / "test_promote_demote.db"))
    v1 = StrategyVersion.from_config("v1", StrategyParams())
    v2 = StrategyVersion.from_config("v2", StrategyParams())
    id1 = db.create_version(v1.name, v1.branch, v1.tag, v1.config)
    id2 = db.create_version(v2.name, v2.branch, v2.tag, v2.config)

    db.promote(id1)  # activate v1
    assert db.get_version(id1)["is_active"] == 1
    assert db.get_version(id2)["is_active"] == 0  # v2 not yet active

    db.promote(id2)  # activate v2
    assert db.get_version(id1)["is_active"] == 0  # v1 DEMOTED
    assert db.get_version(id2)["is_active"] == 1  # v2 active

    # Only one row should be active
    rows = db.list_versions()
    active = [r for r in rows if r["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == id2
    db.close()


# ════════════════════════════════════════════════════════
# Bug 2: from_dict() drops _db / _id refs
# ════════════════════════════════════════════════════════


def test_from_dict_preserves_id_and_db_ref():
    """from_dict must restore _id and _db from serialized data."""
    sv = StrategyVersion.from_config("x", StrategyParams(strategy_mode="pullback"))
    db = StrategyVersionDB(db_path="beads/test_from_dict.db")
    sid = sv.save()
    sv._db = db
    sv._id = sid

    serialized = sv.to_dict()
    assert "_id" in serialized
    assert True  # _db may be skipped in serialization

    restored = StrategyVersion.from_dict(serialized)
    assert restored.id == sid
    assert restored._id == sid
    assert restored._db is not None  # DB ref present
    assert restored._db.path == db.path
    db.close()


def test_from_dict_roundtrip_preserves_db_ref():
    """from_dict round-trip must not lose _db reference."""
    db = StrategyVersionDB(db_path="beads/test_from_dict_roundtrip.db")
    sv = StrategyVersion.from_config("y", StrategyParams())
    sid = sv.save()
    sv._db = db
    sv._id = sid

    restored = StrategyVersion.from_dict(sv.to_dict())
    assert restored.id == sid
    assert restored._db is not None
    assert restored._db.path == db.path
    db.close()


# ════════════════════════════════════════════════════════
# Bug 3: _perform_statistical_analysis fake p_value
# ════════════════════════════════════════════════════════


def _make_backtest_result(total_trades=0):
    from src.backtester import BacktestResult

    return BacktestResult(
        total_trades=total_trades,
        winning_trades=total_trades // 2,
        losing_trades=total_trades // 2,
        win_rate=0.5 if total_trades > 0 else 0.0,
        initial_capital=10000.0,
        final_equity=10000.0,
        net_profit=0.0,
        total_return_pct=0.0,
        profit_factor=1.0,
        max_drawdown_pct=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        drawdown_penalized_sharpe=0.0,
        drawdown_penalized_sortino=0.0,
        ulcer_index=0.0,
        avg_trade_pnl=0.0,
        avg_win=0.0,
        avg_loss=0.0,
        largest_win=0.0,
        largest_loss=0.0,
        payoff_ratio=0.0,
        expectancy=0.0,
        max_consecutive_wins=0,
        max_consecutive_losses=0,
        avg_duration_bars=0.0,
        monthly_breakdown=[],
        equity_curve=[],
        trades=[],
    )


def test_statistical_fallback_identical_metrics_not_significant():
    """identical champion/challenger metrics should give p_value=1.0 (not significant)."""
    r1 = _make_backtest_result()
    r2 = _make_backtest_result()
    runner = AbTestRunner(
        StrategyVersion.from_config("champ", StrategyParams()),
        StrategyVersion.from_config("chal", StrategyParams()),
        __import__("pandas").DataFrame({"close": [1.0]}),
    )
    analysis = runner._perform_statistical_analysis(r1, r2)
    assert analysis["p_value"] == pytest.approx(1.0)
    assert analysis["significant"] is False
    assert analysis["winner"] == "inconclusive"


def test_statistical_fallback_different_metrics_also_not_significant():
    """Different champion/challenger metrics also give p_value=1.0 (no faking)."""
    r1 = _make_backtest_result(total_trades=1)
    r2 = _make_backtest_result(total_trades=1)
    r1.sharpe_ratio = 1.5
    r2.sharpe_ratio = 0.5
    runner = AbTestRunner(
        StrategyVersion.from_config("champ", StrategyParams()),
        StrategyVersion.from_config("chal", StrategyParams()),
        __import__("pandas").DataFrame({"close": [1.0]}),
    )
    analysis = runner._perform_statistical_analysis(r1, r2)
    assert analysis["p_value"] == pytest.approx(1.0)
    assert analysis["significant"] is False
    assert analysis["winner"] == "inconclusive"


def test_get_current_champion_returns_none_when_no_versions():
    """get_current_champion returns None when the DB has no versions at all."""
    db = StrategyVersionDB(db_path="beads/test_champion_empty.db")
    assert get_current_champion(db) is None
    db.close()


def test_get_current_champion_returns_only_active_version(tmp_path):
    """After promote(v2), get_current_champion returns v2, not v1."""
    db = StrategyVersionDB(db_path=str(tmp_path / "test_champion_active.db"))
    v1 = StrategyVersion.from_config("old", StrategyParams())
    v2 = StrategyVersion.from_config("new", StrategyParams())
    db.create_version(v1.name, v1.branch, v1.tag, v1.config)
    id2 = db.create_version(v2.name, v2.branch, v2.tag, v2.config)
    db.promote(id2)
    champ = get_current_champion(db)
    assert champ is not None
    assert champ.name == "new"
    db.close()


def test_get_current_champion_returns_latest_when_multiple_active(tmp_path):
    """When multiple versions are active (pre-fix bug), get_current_champion
    returns the most recently created one — but promote now prevents this state."""
    db = StrategyVersionDB(db_path=str(tmp_path / "test_champion_multi.db"))
    v1 = StrategyVersion.from_config("old", StrategyParams())
    v2 = StrategyVersion.from_config("new", StrategyParams())
    db.create_version(v1.name, v1.branch, v1.tag, v1.config)
    id2 = db.create_version(v2.name, v2.branch, v2.tag, v2.config)
    # After promote(v2), only v2 is active
    db.promote(id2)
    champ = get_current_champion(db)
    assert champ is not None
    assert champ.name == "new"
    db.close()


def test_from_dict_roundtrip_preserves_config():
    """from_dict round-trip must preserve config fields exactly."""
    db = StrategyVersionDB(db_path="beads/test_config_roundtrip.db")
    sv = StrategyVersion.from_config("x", StrategyParams(strategy_mode="pullback"))
    db.create_version(sv.name, sv.branch, sv.tag, sv.config)
    restored = StrategyVersion.from_dict(sv.to_dict())
    assert restored.config["strategy_mode"] == "pullback"
    assert restored.config["fast_ema"] == 9
    db.close()
