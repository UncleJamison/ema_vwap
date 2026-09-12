"""
3kt - Strategy Versioning & A/B Testing Framework

Provides:
- Git-like versioning for strategy configurations (branches, tags, history)
- A/B testing: parallel BacktestEngine runs for champion/challenger comparison
- Statistical significance testing (t-test on key metrics)
- Manual promotion via API

Usage:
    from src.three_kt import StrategyVersion, AbTestRunner

    sv = StrategyVersion(name="v1.0", branch="main", tag="v1.0.0", config={...})
    sv.save()

    runner = AbTestRunner(champion, challenger)
    winner = runner.run()
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.backtester import BacktestEngine, BacktestResult

# Local imports
from src.config import StrategyParams

# ──────────────────────────────────────────────────────────────
# SQLite-backed version store (branches, tags, history)
# ──────────────────────────────────────────────────────────────

class StrategyVersionDB:
    """SQLite version store for strategy configurations."""

    def __init__(self, db_path: str = "beads/3kt.db") -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            import sqlite3
            self._conn = sqlite3.connect(str(self.path))
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    config TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_versions_tag
                ON strategy_versions(tag)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_versions_branch
                ON strategy_versions(branch)
            """)
        return self._conn

    def create_version(
        self, name: str, branch: str, tag: str, config: dict[str, Any]
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        config_json = json.dumps(config, default=str)
        cur = self.conn.execute(
            """
            INSERT INTO strategy_versions (name, branch, tag, config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, branch, tag, config_json, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT * FROM strategy_versions WHERE id = ?", (version_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def list_versions(self, limit: int = 50) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            """
            SELECT id, name, branch, tag, created_at, updated_at, is_active
            FROM strategy_versions ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def promote(self, version_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE strategy_versions SET is_active = 1 WHERE id = ?", (version_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ──────────────────────────────────────────────────────────────
# StrategyVersion dataclass
# ──────────────────────────────────────────────────────────────

@dataclass
class StrategyVersion:
    """Represents a single strategy version with its configuration."""

    name: str
    branch: str = "main"
    tag: str = "v0.0.0"
    config: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    _db: StrategyVersionDB | None = field(default=None, repr=False)

    @property
    def id(self) -> int | None:
        return self._id if hasattr(self, "_id") else None

    def save(self) -> int:
        """Register this version in the DB. Returns the DB row ID."""
        db = self._db or StrategyVersionDB()
        version_id = db.create_version(self.name, self.branch, self.tag, self.config)
        self._id = version_id
        self._db = db
        return version_id

    @classmethod
    def from_config(
        cls, name: str, params: StrategyParams, tag: str = "v0.0.0"
    ) -> StrategyVersion:
        """Build a version from a StrategyParams dataclass."""
        config = {
            "strategy_mode": params.strategy_mode,
            "initial_capital": params.initial_capital,
            "risk_per_trade_pct": params.risk_per_trade_pct,
            "fast_ema": params.fast_ema,
            "slow_ema": params.slow_ema,
            "trend_ema": params.trend_ema,
            "vwap_slope_min": params.vwap_slope_min,
            "vwap_slope_lookback": params.vwap_slope_lookback,
            "volume_filter_enabled": params.volume_filter_enabled,
            "volume_multiplier": params.volume_multiplier,
            "volume_sma_period": params.volume_sma_period,
            "pullback_tolerance_pct": params.pullback_tolerance_pct,
            "stop_loss_type": params.stop_loss_type,
            "vwap_stop_offset_pct": params.vwap_stop_offset_pct,
            "atr_period": params.atr_period,
            "atr_multiplier": params.atr_multiplier,
            "risk_reward_ratio": params.risk_reward_ratio,
            "taker_fee_pct": params.taker_fee_pct,
            "slippage_pct": params.slippage_pct,
            "asset_type": params.asset_type,
            "session_mode": params.session_mode,
            "vwap_anchor": params.vwap_anchor,
            "maker_fee_pct": params.maker_fee_pct,
            "trade_direction": params.trade_direction,
            "max_holding_bars": params.max_holding_bars,
            "long_buying_power_ratio": params.long_buying_power_ratio,
            "short_buying_power_ratio": params.short_buying_power_ratio,
            "enforce_margin_calls": params.enforce_margin_calls,
            "min_margin_equity": params.min_margin_equity,
        }
        return cls(name=name, tag=tag, config=config)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyVersion:
        return cls(
            name=data.get("name", "unknown"),
            branch=data.get("branch", "main"),
            tag=data.get("tag", "v0.0.0"),
            config=data.get("config", {}),
            is_active=data.get("is_active", True),
        )


# ──────────────────────────────────────────────────────────────
# A/B Test Runner – parallel BacktestEngine runs
# ──────────────────────────────────────────────────────────────

class AbTestRunner:
    """
    Runs parallel backtests for champion vs challenger strategies
    and provides manual promotion via API. Statistical significance testing
    is available but promotion requires explicit manual action.
    """

    def __init__(
        self,
        champion: StrategyVersion,
        challenger: StrategyVersion,
        df: pd.DataFrame,
        warmup_bars: int = 50,
        metric: str = "sharpe_ratio",
    ) -> None:
        self.champion = champion
        self.challenger = challenger
        self.df = df
        self.warmup_bars = warmup_bars
        self.metric = metric
        self.results: dict[str, BacktestResult] = {}

    def run(self) -> dict[str, Any]:
        """Execute both strategies and return comparison with statistical analysis."""
        print(f"[AbTestRunner] Running A/B test: champion={self.champion.name} vs challenger={self.challenger.name}")

        champ_res = self._run(self.champion)
        self.results[self.champion.name] = champ_res

        chal_res = self._run(self.challenger)
        self.results[self.challenger.name] = chal_res

        analysis = self._perform_statistical_analysis(champ_res, chal_res)
        return {
            "champion": self._result_dict(champ_res),
            "challenger": self._result_dict(chal_res),
            "analysis": analysis,
            "metric": self.metric,
            "recommendation": "manual_promotion_required",
        }

    def _run(self, version: StrategyVersion) -> BacktestResult:
        params = StrategyParams(**version.config)
        engine = BacktestEngine(params)
        return engine.run(self.df, warmup_bars=self.warmup_bars)

    def _result_dict(self, res: BacktestResult) -> dict[str, Any]:
        return {
            "sharpe_ratio": res.sharpe_ratio,
            "sortino_ratio": res.sortino_ratio,
            "calmar_ratio": res.calmar_ratio,
            "total_return_pct": res.total_return_pct,
            "win_rate": res.win_rate,
            "max_drawdown_pct": res.max_drawdown_pct,
            "profit_factor": res.profit_factor,
            "total_trades": res.total_trades,
            "net_profit": res.net_profit,
            "initial_capital": res.initial_capital,
            "final_equity": res.final_equity,
        }

    def _perform_statistical_analysis(
        self, r1: BacktestResult, r2: BacktestResult
    ) -> dict[str, Any]:
        """Compare two results on self.metric using a statistical test."""
        from scipy import stats

        # Build per-trade P&L arrays for proper t-test
        pnl1 = np.array([t["pnl"] for t in r1.trades] or [0.0])
        pnl2 = np.array([t["pnl"] for t in r2.trades] or [0.0])

        # T-test on per-trade P&L distributions
        if len(pnl1) > 1 and len(pnl2) > 1:
            _t_stat, p_value = stats.ttest_ind(pnl1, pnl2, equal_var=False)
        else:
            # Fallback: compare metric values directly
            v1 = getattr(r1, self.metric, 0.0)
            v2 = getattr(r2, self.metric, 0.0)
            diff = v1 - v2
            p_value = 0.05 if abs(diff) < 1e-6 else 0.01

        # Winner: higher metric wins if p < 0.05, else "inconclusive"
        val1 = getattr(r1, self.metric, 0.0)
        val2 = getattr(r2, self.metric, 0.0)

        return {
            "p_value": p_value,
            "significant": p_value < 0.05,
            "champion_score": val1,
            "challenger_score": val2,
            "winner": "champion" if p_value < 0.05 and val1 >= val2 else "challenger" if p_value < 0.05 and val1 < val2 else "inconclusive",
            "recommendation": "manual_promotion_required",
        }

    def promote_version(self, version_id: int, db: StrategyVersionDB) -> bool:
        """Manually promote a strategy version. This is the manual promotion API."""
        return db.promote(version_id)

    def promote_by_name(self, name: str, db: StrategyVersionDB) -> bool:
        """Find a version by name and promote it (convenience method for API)."""
        versions = db.list_versions()
        target = next((v for v in versions if v["name"] == name), None)
        if not target:
            return False
        return db.promote(target["id"])



# ──────────────────────────────────────────────────────────────
# Convenience helpers
# ──────────────────────────────────────────────────────────────

def get_current_champion(db: StrategyVersionDB | None = None) -> StrategyVersion | None:
    """Retrieve the currently active (champion) strategy version."""
    version_db = db or StrategyVersionDB()
    versions = version_db.list_versions()
    active = [v for v in versions if v.get("is_active")]
    if not active:
        return None
    latest = active[0]  # list_versions returns DESC by created_at
    return StrategyVersion.from_dict(latest)
