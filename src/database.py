"""
PostgreSQL-compatible CandleDatabase compatibility layer.
Provides the same interface as the old SQLite CandleDatabase but uses PostgreSQL.
"""

import json
from datetime import datetime

import pandas as pd


def normalize_symbol_key(symbol: str) -> str:
    """Normalize equivalent exchange symbol spellings to BASE/QUOTE."""
    clean = symbol.strip().upper().replace("-", "/").replace("_", "/")
    if "/" not in clean:
        for quote in ("USDT", "USDC", "USD"):
            if clean.endswith(quote):
                return f"{clean[:-len(quote)]}/{quote}"
        return f"{clean}/USD"
    return clean


from src.pg_database import (
    acknowledge_alert_pg,
    delete_alert_pg,
    delete_batch_results_pg,
    get_account_balance_pg,
    get_active_paper_profiles_pg,
    get_alerts_pg,
    get_background_task_pg,
    get_batch_result_pg,
    get_paper_ledger_transactions_pg,
    get_paper_position_pg,
    get_paper_positions_pg,
    get_paper_profile_pg,
    get_paper_trade_history_pg,
    get_paper_trade_statistics_pg,
    get_portfolio_balances_pg,
    get_setting_pg,
    list_background_tasks_pg,
    list_paper_profiles_pg,
    load_all_settings_pg,
    load_batch_results_pg,
    load_candles_pg,
    purge_batch_results_pg,
    record_ledger_transaction_pg,
    remove_paper_position_pg,
    save_alert_pg,
    save_background_task_pg,
    save_batch_result_pg,
    save_candles_pg,
    save_paper_profile_pg,
    save_paper_trade_record_pg,
    save_setting_pg,
    set_batch_result_favorite_pg,
    set_paper_profile_active_pg,
    update_batch_result_validation_pg,
    update_paper_position_pg,
)
from src.sync_bridge import run_async


class CandleDatabase:
    """
    PostgreSQL-compatible database interface.
    Provides the same interface as the old SQLite CandleDatabase but uses PostgreSQL.
    """

    def __init__(self, db_path: str | None = None, domain: str | None = None):
        """
        Initialize the database connection.

        Args:
            db_path: Ignored (kept for compatibility)
            domain: Database domain (candles, batch_results, paper_trading, system)
        """
        self.db_path = db_path
        self.domain = domain
        # Tables are initialized by init_all_domains() in pg_database.py
        # No need to call init_db() here

    # ============================================================
    # Candle Operations
    # ============================================================

    def save_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
    ) -> int:
        """Save candles to PostgreSQL (upsert)."""
        return run_async(
            save_candles_pg(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                df=df,
            )
        )

    def load_candles(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
        start_date: str | datetime | None = None,
        end_date: str | datetime | None = None,
    ) -> pd.DataFrame:
        """Load candles from PostgreSQL."""
        # asyncpg TIMESTAMPTZ columns need datetime objects, not ISO strings
        import pandas as pd

        if isinstance(start_date, str):
            start_date = pd.to_datetime(start_date, utc=True).to_pydatetime()
        if isinstance(end_date, str):
            end_date = pd.to_datetime(end_date, utc=True).to_pydatetime()

        return run_async(
            load_candles_pg(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
            )
        )

    def get_candle_count(self, exchange: str, symbol: str, timeframe: str) -> int:
        """Get total cached candle count for given symbol/timeframe."""

        async def _count() -> int:
            from src.pg_database import _ensure_pg_manager

            mgr = await _ensure_pg_manager()
            pool = mgr.get_pool("candles")
            async with pool.acquire() as conn:
                # Count both raw and normalized spellings — save_candles
                # stores the symbol exactly as passed by the caller.
                return await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM candles
                    WHERE exchange = $1 AND timeframe = $3
                      AND (symbol = $2 OR symbol = $4)
                """,
                    exchange.lower(),
                    symbol,
                    timeframe.lower(),
                    normalize_symbol_key(symbol),
                )

        return run_async(_count())

    def export_candles_json(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> str:
        """Export candles to JSON string (OHLCV format)."""

        async def _export() -> list[dict]:
            from src.pg_database import _ensure_pg_manager

            mgr = await _ensure_pg_manager()
            pool = mgr.get_pool("candles")
            query = (
                "SELECT timestamp, open, high, low, close, volume FROM candles"
                " WHERE 1=1"
            )
            params: list = []
            if exchange:
                params.append(exchange.lower())
                query += f" AND exchange = ${len(params)}"
            if symbol:
                params.append(symbol)
                query += f" AND symbol = ${len(params)}"
            if timeframe:
                params.append(timeframe.lower())
                query += f" AND timeframe = ${len(params)}"
            query += " ORDER BY timestamp ASC"
            if limit:
                params.append(limit)
                query += f" LIMIT ${len(params)}"
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            out = []
            for r in rows:
                d = dict(r)
                ts = d.get("timestamp")
                d["timestamp"] = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                out.append(d)
            return out

        return json.dumps(run_async(_export()))

    def export_candles_csv(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> str:
        """Export candles to CSV string (OHLCV format)."""
        import csv
        from io import StringIO

        data = json.loads(self.export_candles_json(exchange, symbol, timeframe, limit))
        output = StringIO()
        if data:
            writer = csv.DictWriter(
                output,
                fieldnames=["timestamp", "open", "high", "low", "close", "volume"],
            )
            writer.writeheader()
            writer.writerows(data)
        return output.getvalue()

    def clear_cache(self) -> int:
        """Clear all cached candles from PostgreSQL.

        Returns:
            Number of rows deleted.
        """

        async def _clear() -> int:
            from src.pg_database import _ensure_pg_manager

            mgr = await _ensure_pg_manager()
            pool = mgr.get_pool("candles")
            async with pool.acquire() as conn:
                # Get count before truncate
                count = await conn.fetchval("SELECT COUNT(*) FROM candles")
                await conn.execute("TRUNCATE TABLE candles")
                return count

        return run_async(_clear())

    # ============================================================
    # Batch Results Operations
    # ============================================================

    def save_batch_result(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str,
        target_metric: str,
        best_value: float,
        trade_count: int,
        sharpe_ratio: float | None,
        calmar_ratio: float | None,
        win_rate: float | None,
        max_drawdown_pct: float | None,
        total_return_pct: float | None,
        best_params: dict,
        run_timestamp: str,
        checkpoint_fingerprint: str | None = None,
    ) -> None:
        """Save batch optimization result."""

        #        if isinstance(run_timestamp, str):
        #            run_timestamp = pd.to_datetime(run_timestamp, utc=True).to_pydatetime()
        run_async(
            save_batch_result_pg(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                strategy_mode=strategy_mode,
                target_metric=target_metric,
                best_value=best_value,
                trade_count=trade_count,
                sharpe_ratio=sharpe_ratio,
                calmar_ratio=calmar_ratio,
                win_rate=win_rate,
                max_drawdown_pct=max_drawdown_pct,
                total_return_pct=total_return_pct,
                # Callers may pass a dict OR a pre-serialized JSON string;
                # dumping again would double-encode into a JSONB string scalar.
                best_params=(
                    best_params
                    if isinstance(best_params, str)
                    else json.dumps(best_params)
                ),
                run_timestamp=run_timestamp,
                checkpoint_fingerprint=checkpoint_fingerprint,
            )
        )

    def load_batch_results(
        self,
        target_metric: str | None = None,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Load batch results from PostgreSQL."""
        symbols = [symbol] if symbol else None
        timeframes = [timeframe] if timeframe else None
        return run_async(
            load_batch_results_pg(
                target_metric=target_metric,
                exchange=exchange,
                symbols=symbols,
                timeframes=timeframes,
                limit=limit,
            )
        )

    def get_batch_result(self, result_id: int) -> dict | None:
        """Get a specific batch result by ID."""
        return run_async(get_batch_result_pg(result_id))

    def delete_batch_results(self, ids: list[int]) -> int:
        """Delete batch results by IDs."""
        return run_async(delete_batch_results_pg(ids=ids))

    def set_batch_result_favorite(self, result_id: int, favorite: bool) -> bool:
        """Set favorite flag on batch result."""
        return run_async(
            set_batch_result_favorite_pg(result_id=result_id, favorite=favorite)
        )

    def purge_batch_results(
        self, older_than_days: int, target_metric: str | None = None
    ) -> int:
        """Purge stale batch results by age."""
        return run_async(
            purge_batch_results_pg(
                older_than_days=older_than_days, target_metric=target_metric
            )
        )

    def update_batch_result_validation(
        self, result_id: int, status: str, report_json: str | None = None
    ) -> bool:
        """Update validation status and report JSON for a batch result."""
        return run_async(
            update_batch_result_validation_pg(
                result_id=result_id, status=status, report_json=report_json
            )
        )

    # ============================================================
    # Paper Profile Operations
    # ============================================================

    def save_paper_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str,
        target_metric: str,
        params_json: str,
        optuna_score: float = 0.0,
        is_active: bool = True,
        deployed: bool = False,
        deployed_at: str | None = None,
        validation_report_json: str | None = None,
    ) -> int:
        """Save or update paper trading profile."""
        return run_async(
            save_paper_profile_pg(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                strategy_mode=strategy_mode,
                target_metric=target_metric,
                params=params_json,
                optuna_score=optuna_score,
                is_active=is_active,
                is_deployed=deployed,
                validation_report=validation_report_json,
            )
        )

    def get_paper_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str | None = None,
        target_metric: str | None = None,
    ) -> dict | None:
        """Get a paper trading profile."""
        return run_async(
            get_paper_profile_pg(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe or "5m",
                strategy_mode=strategy_mode or "crossover",
                target_metric=target_metric or "sharpe_ratio",
            )
        )

    def list_paper_profiles(
        self,
        exchange: str | None = None,
        active_only: bool = False,
        deployed_only: bool = False,
    ) -> list[dict]:
        """List paper profiles."""
        return run_async(
            list_paper_profiles_pg(
                exchange=exchange,
                active_only=active_only,
                deployed_only=deployed_only,
            )
        )

    def get_active_profiles(self) -> list[dict]:
        """Get all active deployed profiles."""
        return run_async(get_active_paper_profiles_pg())

    def set_active(
        self, profile_id: int, is_active: bool, deployed: bool | None = None
    ) -> bool:
        """Enable or disable a profile."""
        return run_async(
            set_paper_profile_active_pg(profile_id, is_active, deployed=deployed)
        )

    def delete_paper_profile(self, profile_id: int) -> bool:
        """Delete a profile by ID."""
        from src.pg_database import delete_paper_profile_pg

        return run_async(delete_paper_profile_pg(profile_id))

    def update_trade_direction(self, profile_id: int, trade_direction: str) -> bool:
        """Update trade direction for a profile."""
        from src.pg_database import update_paper_profile_trade_direction_pg

        return run_async(
            update_paper_profile_trade_direction_pg(profile_id, trade_direction)
        )

    # ============================================================
    # Paper Ledger/Position Operations (deprecated - use PaperLedger directly)
    # ============================================================

    def get_account_balance(self, account_id: str = "default") -> float:
        """Get account balance."""
        return run_async(get_account_balance_pg(account_id=account_id))

    def list_paper_positions(
        self,
        exchange: str | None = None,
        account_id: str = "default",
    ) -> list[dict]:
        """List paper positions."""
        return run_async(get_paper_positions_pg(account_id=account_id))

    def get_paper_position(
        self,
        exchange: str,
        symbol: str,
        account_id: str = "default",
    ) -> dict | None:
        """Get a specific paper position."""
        return run_async(
            get_paper_position_pg(
                account_id=account_id, exchange=exchange, symbol=symbol
            )
        )

    def save_paper_position(
        self,
        exchange: str,
        symbol: str,
        side: str,
        entry_price: float,
        current_price: float,
        quantity: float,
        cost_basis: float,
        stop_loss: float | None,
        take_profit: float | None,
        unrealized_pnl: float,
        unrealized_pnl_pct: float,
        entry_time: str,
        last_bar_time: str | None,
        metadata: dict | None = None,
        account_id: str = "default",
    ) -> int:
        """Save paper position."""
        run_async(
            update_paper_position_pg(
                account_id=account_id,
                exchange=exchange,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                current_price=current_price,
                quantity=quantity,
                cost_basis=cost_basis,
                stop_loss=stop_loss,
                take_profit=take_profit,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
                entry_time=entry_time,
                last_bar_time=last_bar_time,
                metadata=metadata,
            )
        )

    def close_paper_position(
        self,
        exchange: str,
        symbol: str,
        account_id: str = "default",
    ) -> bool:
        """Close a paper position."""
        return run_async(
            remove_paper_position_pg(
                account_id=account_id, exchange=exchange, symbol=symbol
            )
        )

    def record_ledger_transaction(
        self,
        transaction_id: str,
        account_id: str,
        exchange: str,
        symbol: str,
        tx_type: str,
        amount: float,
        asset_qty: float,
        price: float,
        fee: float,
        balance_after: float,
        timestamp: str,
        notes: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Record a ledger transaction."""
        run_async(
            record_ledger_transaction_pg(
                transaction_id=transaction_id,
                account_id=account_id,
                exchange=exchange,
                symbol=symbol,
                tx_type=tx_type,
                amount=amount,
                asset_qty=asset_qty,
                price=price,
                fee=fee,
                balance_after=balance_after,
                timestamp=timestamp,
                notes=notes,
                metadata=metadata,
            )
        )

    def get_paper_trade_history(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        exit_reason: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get paper trade history."""
        return run_async(
            get_paper_trade_history_pg(
                account_id="default",
                limit=limit,
                offset=0,
            )
        )

    def record_paper_trade_history(
        self,
        trade_id: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        entry_time: str,
        exit_time: str,
        holding_period_bars: int,
        realized_pnl: float,
        realized_pnl_pct: float,
        fees: float,
        exit_reason: str,
        params_json: str,
        metadata: str | None = None,
        account_id: str = "default",
    ) -> int:
        """Record completed trade in history."""
        return run_async(
            save_paper_trade_record_pg(
                trade_id=trade_id,
                account_id=account_id,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                entry_time=entry_time,
                exit_time=exit_time,
                holding_period_bars=holding_period_bars,
                realized_pnl=realized_pnl,
                realized_pnl_pct=realized_pnl_pct,
                fees=fees,
                exit_reason=exit_reason,
                params=params_json,
                metadata=metadata,
            )
        )
        return 1

    def get_paper_trade_statistics(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
    ) -> dict:
        """Get trade statistics."""
        return run_async(
            get_paper_trade_statistics_pg(exchange=exchange, symbol=symbol)
        )

    def list_ledger_transactions(
        self,
        account_id: str = "default",
        exchange: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List ledger transactions."""
        return run_async(
            get_paper_ledger_transactions_pg(
                account_id=account_id, limit=limit, offset=0
            )
        )

    def list_paper_trade_history(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        exit_reason: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List paper trade history."""
        return run_async(
            get_paper_trade_history_pg(
                account_id="default",
                limit=limit,
                offset=0,
            )
        )

    # ============================================================
    # Settings Operations
    # ============================================================

    def save_setting(
        self,
        key: str,
        value: str,
        is_secret: bool = False,
    ) -> None:
        """Save a setting."""
        run_async(save_setting_pg(key, value, is_secret))

    def get_setting(self, key: str) -> dict | None:
        """Get a setting."""
        return run_async(get_setting_pg(key))

    def load_all_settings(self) -> list[dict]:
        """Load all settings."""
        return run_async(load_all_settings_pg())

    # ============================================================
    # Alert Operations
    # ============================================================

    def save_alert(self, entry: dict) -> None:
        """Save alert to system DB."""
        run_async(save_alert_pg(entry))

    def get_alerts(self, limit: int = 50) -> list[dict]:
        """Get recent alerts."""
        return run_async(get_alerts_pg(limit=limit))

    def acknowledge_alert(self, alert_id: int) -> bool:
        """Acknowledge an alert."""
        return run_async(acknowledge_alert_pg(alert_id))

    def delete_alert(self, alert_id: int) -> bool:
        """Delete an alert by ID."""
        return run_async(delete_alert_pg(alert_id))

    # ============================================================
    # Background Task Operations
    # ============================================================

    def save_background_task(self, task_data: dict) -> None:
        """Save background task."""
        run_async(save_background_task_pg(task_data))

    def get_background_task(self, task_id: str) -> dict | None:
        """Get background task by ID."""
        return run_async(get_background_task_pg(task_id))

    def list_background_tasks(
        self,
        limit: int = 50,
        status: str | None = None,
    ) -> list[dict]:
        """List background tasks."""
        return run_async(list_background_tasks_pg(limit=limit, status=status))

    def prune_background_tasks(self, keep_limit: int = 50) -> int:
        """Prune old background tasks."""
        from src.pg_database import prune_background_tasks_pg

        return run_async(prune_background_tasks_pg(keep_limit))

    # ============================================================
    # Portfolio Balances
    # ============================================================

    def get_portfolio_balances(self, account_id: str = "default") -> list[dict]:
        """Get portfolio balances."""
        return run_async(get_portfolio_balances_pg(account_id=account_id))

    # ============================================================
    # Utility Methods
    # ============================================================

    def get_connection(self):
        """Deprecated: Return a context manager for compatibility."""

        class MockConnection:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def execute(self, *args, **kwargs):
                pass

            def commit(self):
                pass

            def cursor(self):
                """Return self so conn.cursor().execute().fetchall() works in tests."""
                return self

            def fetchall(self):
                return []

            def fetchone(self):
                return None

        return MockConnection()

    def init_db(self):
        """No-op - PostgreSQL tables initialized by init_all_domains()."""

    def close(self):
        """No-op - connection pooling managed by pg_connection_manager."""


# ============================================================
# Helper functions for backward compatibility
# ============================================================


def get_database(db_key: str) -> CandleDatabase:
    """Get a CandleDatabase instance for the named domain."""
    return CandleDatabase(domain=db_key)


# Singleton instance for backward compatibility
db = CandleDatabase(domain="candles")
