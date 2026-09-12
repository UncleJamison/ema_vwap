"""
Regression tests for 3kt (Strategy Versioning & A/B Testing).
Covers version creation, DB persistence, A/B runner, and promotion.
"""

from src.config import StrategyParams
from src.data_loader import DataLoader
from src.three_kt import AbTestRunner, StrategyVersion, StrategyVersionDB


def test_version_save_and_retrieve():
    db = StrategyVersionDB(db_path="beads/test_3kt.db")
    sv = StrategyVersion.from_config(
        name="test-v1", params=StrategyParams(strategy_mode="crossover")
    )
    sv._db = db  # Tie the version to this DB instance
    sid = sv.save()
    assert sid > 0
    retrieved = db.get_version(sid)
    assert retrieved is not None
    assert retrieved["name"] == "test-v1"
    db.close()


def test_ab_runner_runs_both():
    df = DataLoader.generate_synthetic_candles(num_bars=300, seed=42)
    champion = StrategyVersion.from_config(
        name="champion", params=StrategyParams(strategy_mode="multi_ema")
    )
    challenger = StrategyVersion.from_config(
        name="challenger", params=StrategyParams(strategy_mode="pullback")
    )
    runner = AbTestRunner(champion, challenger, df, warmup_bars=50, metric="sharpe_ratio")
    result = runner.run()
    assert "champion" in result
    assert "challenger" in result
    assert "analysis" in result
    assert result["metric"] == "sharpe_ratio"
    assert result["analysis"]["p_value"] >= 0.0
    assert result["recommendation"] == "manual_promotion_required"
    # Winner must be either champion, challenger, or inconclusive
    assert result["analysis"]["winner"] in {champion.name, challenger.name, "inconclusive"}
