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
    await sv.save()

    runner = AbTestRunner(champion, challenger, df)
    winner = runner.run()
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from src.backtester import BacktestEngine, BacktestResult

# Local imports
from src.config import StrategyParams

# ──────────────────────────────────────────────────────────────
# PostgreSQL-backed version store (branches, tags, history)
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# Conn-injected cores (shared by pool-backed *_pg funcs and StrategyVersionDB)
# ──────────────────────────────────────────────────────────────


async def _ensure_table_core(conn) -> None:
    """Create the strategy_versions table if it doesn't exist."""
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_versions (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            branch TEXT NOT NULL DEFAULT 'main',
            tag TEXT NOT NULL DEFAULT 'v0.0.0',
            config JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active BOOLEAN NOT NULL DEFAULT FALSE
        )
    """
    )


async def _save_core(conn, name, branch, tag, config, is_active) -> int:
    import json as _json

    now = datetime.now(UTC)
    row = await conn.fetchrow(
        """
        INSERT INTO strategy_versions (name, branch, tag, config, created_at, updated_at, is_active)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
    """,
        name,
        branch,
        tag,
        _json.dumps(config, default=str),
        now,
        now,
        is_active,
    )
    return row["id"]


async def _get_core(conn, version_id: int) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT * FROM strategy_versions WHERE id = $1", version_id
    )
    return dict(row) if row else None


async def _list_core(conn, limit: int) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, name, branch, tag, created_at, updated_at, is_active
        FROM strategy_versions ORDER BY created_at DESC LIMIT $1
    """,
        limit,
    )
    return [dict(r) for r in rows]


async def _promote_core(conn, version_id: int) -> bool:
    async with conn.transaction():
        await conn.execute(
            "UPDATE strategy_versions SET is_active = FALSE WHERE id != $1",
            version_id,
        )
        result = await conn.execute(
            "UPDATE strategy_versions SET is_active = TRUE WHERE id = $1",
            version_id,
        )
        return result != "UPDATE 0"


async def _active_core(conn) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        "SELECT * FROM strategy_versions WHERE is_active = TRUE "
        "ORDER BY created_at DESC LIMIT 1"
    )
    return dict(row) if row else None


# ──────────────────────────────────────────────────────────────
# Pool-backed public API (for async callers in the app)
# ──────────────────────────────────────────────────────────────


async def _system_conn():
    """Acquire a connection from the shared system-domain pool."""
    from src.pg_connection_manager import get_pg_manager

    return get_pg_manager().get_pool("system").acquire()


async def save_strategy_version_pg(
    name: str,
    branch: str,
    tag: str,
    config: dict[str, Any],
    is_active: bool = True,
) -> int:
    """Save a strategy version to PostgreSQL (shared pool)."""
    async with _system_conn() as conn:
        await _ensure_table_core(conn)
        return await _save_core(conn, name, branch, tag, config, is_active)


async def get_strategy_version_pg(version_id: int) -> dict[str, Any] | None:
    """Get a strategy version by ID (shared pool)."""
    async with _system_conn() as conn:
        await _ensure_table_core(conn)
        return await _get_core(conn, version_id)


async def list_strategy_versions_pg(limit: int = 50) -> list[dict[str, Any]]:
    """List strategy versions (shared pool)."""
    async with _system_conn() as conn:
        await _ensure_table_core(conn)
        return await _list_core(conn, limit)


async def promote_strategy_version_pg(version_id: int) -> bool:
    """Promote a strategy version; demotes all others (shared pool)."""
    async with _system_conn() as conn:
        await _ensure_table_core(conn)
        return await _promote_core(conn, version_id)


async def get_active_strategy_version_pg() -> dict[str, Any] | None:
    """Get the currently active strategy version (shared pool)."""
    async with _system_conn() as conn:
        await _ensure_table_core(conn)
        return await _active_core(conn)


def _run_with_conn(coro_fn):
    """Run ``coro_fn(conn)`` to completion from synchronous code.

    Two modes:
    - App context (main loop running): submit to the main loop and use the
      shared system-domain pool (pools are bound to that loop).
    - Standalone (scripts/tests, no app loop): run in a fresh event loop with
      a dedicated short-lived connection — fully self-contained, so shared
      pool / event-loop binding issues cannot arise.
    """
    from src.sync_bridge import get_main_loop, run_async

    main_loop = get_main_loop()
    if main_loop is not None and main_loop.is_running():

        async def _app_side():
            from src.pg_connection_manager import get_pg_manager

            mgr = get_pg_manager()
            async with mgr.get_pool("system").acquire() as conn:
                return await coro_fn(conn)

        return run_async(_app_side())

    # Standalone mode: fresh loop + dedicated connection
    import asyncio
    import os

    import asyncpg

    async def _standalone():
        from src.pg_connection_manager import PostgresConfig

        cfg = PostgresConfig.from_env()
        db_name = os.environ.get("PG_DATABASE_SYSTEM") or "ema_vwap_system"
        conn = await asyncpg.connect(
            host=cfg.host,
            port=cfg.port,
            database=db_name,
            user=cfg.user,
            password=cfg.password,
        )
        try:
            return await coro_fn(conn)
        finally:
            await conn.close()

    return asyncio.run(_standalone())


# ──────────────────────────────────────────────────────────────
# StrategyVersionDB — sync PG-backed store (SQLite-era surface)
# ──────────────────────────────────────────────────────────────


class StrategyVersionDB:
    """PostgreSQL-backed strategy version store with a sync (SQLite-era) surface.

    ``db_path`` is accepted for backward compatibility and stored as ``.path``,
    but persistence goes through the shared ``system`` PG domain pool.
    """

    def __init__(self, db_path: str | None = None):
        self.path = db_path
        self._tables_ready = False

    def _run(self, coro_fn):
        """Run ``coro_fn(conn)`` to completion (see _run_with_conn)."""
        return _run_with_conn(coro_fn)

    def create_version(
        self, name: str, branch: str, tag: str, config: dict[str, Any]
    ) -> int:
        """Insert a new strategy version; returns its row ID."""

        async def _create(conn):
            await _ensure_table_core(conn)
            return await _save_core(conn, name, branch, tag, config, True)

        return self._run(_create)

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        """Fetch a version row by ID."""

        async def _get(conn):
            await _ensure_table_core(conn)
            return await _get_core(conn, version_id)

        return self._run(_get)

    def list_versions(self, limit: int = 50) -> list[dict[str, Any]]:
        """List versions (newest first)."""

        async def _list(conn):
            await _ensure_table_core(conn)
            return await _list_core(conn, limit)

        return self._run(_list)

    def promote(self, version_id: int) -> bool:
        """Promote one version; demotes all others."""

        async def _promote(conn):
            await _ensure_table_core(conn)
            return await _promote_core(conn, version_id)

        return self._run(_promote)

    def close(self) -> None:
        """No-op under PostgreSQL (the shared pool outlives this object)."""


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
    _id: int | None = field(default=None, repr=False)
    _db: StrategyVersionDB | None = field(default=None, repr=False)

    @property
    def id(self) -> int | None:
        return self._id

    def save(self) -> int:
        """Register this version in PostgreSQL. Returns the DB row ID."""

        async def _save(conn):
            await _ensure_table_core(conn)
            return await _save_core(
                conn, self.name, self.branch, self.tag, self.config, self.is_active
            )

        version_id = _run_with_conn(_save)
        self._id = version_id
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
        d = asdict(self)
        d["_id"] = self.id
        d["_db_path"] = self._db.path if getattr(self, "_db", None) else None
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyVersion:
        config = data.get("config", {})
        if isinstance(config, str):  # asyncpg returns JSONB as str without codec
            try:
                config = json.loads(config)
            except (json.JSONDecodeError, TypeError):
                config = {}

        version = cls(
            name=data.get("name", "unknown"),
            branch=data.get("branch", "main"),
            tag=data.get("tag", "v0.0.0"),
            config=config,
            is_active=bool(data.get("is_active", True)),
        )
        version._id = data.get("_id", data.get("id"))
        db_path = data.get("_db_path")
        if db_path:
            version._db = StrategyVersionDB(db_path=db_path)
        return version


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
        print(
            f"[AbTestRunner] Running A/B test: champion={self.champion.name} vs challenger={self.challenger.name}"
        )

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
            # Fallback: compare metric values directly (values unused; p_value = 1.0)
            p_value = 1.0

        # Winner: higher metric wins if p < 0.05, else "inconclusive"
        val1 = getattr(r1, self.metric, 0.0)
        val2 = getattr(r2, self.metric, 0.0)

        return {
            "p_value": p_value,
            "significant": p_value < 0.05,
            "champion_score": val1,
            "challenger_score": val2,
            "winner": (
                "champion"
                if p_value < 0.05 and val1 >= val2
                else "challenger" if p_value < 0.05 and val1 < val2 else "inconclusive"
            ),
            "recommendation": "manual_promotion_required",
        }

    async def promote_version(self, version_id: int) -> bool:
        """Manually promote a strategy version. This is the manual promotion API."""
        return await promote_strategy_version_pg(version_id)

    async def promote_by_name(self, name: str) -> bool:
        """Find a version by name and promote it (convenience method for API)."""
        versions = await list_strategy_versions_pg()
        target = next((v for v in versions if v["name"] == name), None)
        if not target:
            return False
        return await promote_strategy_version_pg(target["id"])


# ──────────────────────────────────────────────────────────────
# Convenience helpers
# ──────────────────────────────────────────────────────────────


def get_current_champion(db: StrategyVersionDB | None = None) -> StrategyVersion | None:
    """Retrieve the currently active (champion) strategy version.

    Returns a StrategyVersion object, or None if no active version exists.
    """

    async def _get(conn):
        await _ensure_table_core(conn)
        return await _active_core(conn)

    row = _run_with_conn(_get)
    if not row:
        return None
    return StrategyVersion.from_dict(row)
