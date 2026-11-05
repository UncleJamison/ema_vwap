"""
Paper Trading Transaction Ledger and Accounting Module.
Implements double-entry ledger auditing, virtual balances, position lifecycles, and trade settlement.
"""

import json
import math
import uuid
from datetime import UTC, datetime
from typing import Any

from src.paper.models import (
    LedgerTransaction,
    PaperPosition,
    PaperTradeRecord,
)
from src.pg_database import (
    get_account_balance_pg,
    get_paper_ledger_transactions_pg,
    get_paper_position_pg,
    get_paper_positions_pg,
    get_paper_trade_history_pg,
    get_paper_trade_statistics_pg,
    record_ledger_transaction_pg,
    remove_paper_position_pg,
    reset_paper_account,
    save_paper_trade_record_pg,
    update_paper_position_pg,
)


class PaperLedger:
    """Double-entry transaction ledger and virtual portfolio balance manager."""

    def __init__(self, db=None) -> None:
        # Uses async PG functions directly (paper_trading domain is PostgreSQL)
        # db parameter accepted for backward compatibility but ignored
        pass

    async def get_balance(self, account_id: str = "default") -> float:
        """Get the current cash balance for the paper account."""
        return await get_account_balance_pg(account_id=account_id)

    async def get_total_equity(self, account_id: str = "default") -> float:
        """Get total equity (cash + positions) for the paper account."""
        cash = await self.get_balance(account_id)
        positions = await self.list_positions(account_id=account_id)
        position_value = sum(p.cost_basis + p.unrealized_pnl for p in positions)
        return cash + position_value

    async def deposit(
        self,
        account_id: str = "default",
        amount: float = 10000.0,
        notes: str = "Initial deposit",
    ) -> LedgerTransaction:
        """Deposit cash into the virtual paper trading account."""
        if (
            not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or amount <= 0
        ):
            raise ValueError(
                f"Deposit amount must be a finite positive number, got: {amount}"
            )
        current_balance = await self.get_balance(account_id)
        new_balance = current_balance + amount
        tx_id = f"tx_dep_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(UTC).isoformat()

        await record_ledger_transaction_pg(
            transaction_id=tx_id,
            account_id=account_id,
            exchange="system",
            symbol="",
            tx_type="DEPOSIT",
            amount=amount,
            asset_qty=0.0,
            price=0.0,
            fee=0.0,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
        )

        return LedgerTransaction(
            transaction_id=tx_id,
            account_id=account_id,
            exchange="system",
            symbol="",
            type="DEPOSIT",
            amount=amount,
            asset_qty=0.0,
            price=0.0,
            fee=0.0,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
        )

    async def withdraw(
        self,
        account_id: str = "default",
        amount: float = 1000.0,
        notes: str = "Withdrawal",
    ) -> LedgerTransaction:
        """Withdraw cash from the paper trading account."""
        if (
            not isinstance(amount, (int, float))
            or not math.isfinite(amount)
            or amount <= 0
        ):
            raise ValueError(
                f"Withdrawal amount must be a finite positive number, got: {amount}"
            )
        current_balance = await self.get_balance(account_id)
        if amount > current_balance:
            raise ValueError(
                f"Insufficient paper balance ({current_balance}) for withdrawal of {amount}"
            )

        new_balance = current_balance - amount
        tx_id = f"tx_wth_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(UTC).isoformat()

        await record_ledger_transaction_pg(
            transaction_id=tx_id,
            account_id=account_id,
            exchange="system",
            symbol="",
            tx_type="WITHDRAWAL",
            amount=-amount,
            asset_qty=0.0,
            price=0.0,
            fee=0.0,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
        )

        return LedgerTransaction(
            transaction_id=tx_id,
            account_id=account_id,
            exchange="system",
            symbol="",
            type="WITHDRAWAL",
            amount=-amount,
            asset_qty=0.0,
            price=0.0,
            fee=0.0,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
        )

    async def record_buy(
        self,
        account_id: str,
        exchange: str,
        symbol: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LedgerTransaction:
        """Record buy order execution, deduct cost basis + fees from cash balance."""
        current_balance = await self.get_balance(account_id)
        total_cost = (quantity * price) + fee
        if total_cost > current_balance:
            raise ValueError(
                f"Insufficient funds: cost {total_cost:.2f} exceeds available balance {current_balance:.2f}"
            )

        new_balance = current_balance - total_cost
        tx_id = f"tx_buy_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(UTC).isoformat()
        meta_json = json.dumps(metadata or {})

        await record_ledger_transaction_pg(
            transaction_id=tx_id,
            account_id=account_id,
            exchange=exchange,
            symbol=symbol,
            tx_type="BUY",
            amount=-total_cost,
            asset_qty=quantity,
            price=price,
            fee=fee,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
            metadata=meta_json,
        )

        return LedgerTransaction(
            transaction_id=tx_id,
            account_id=account_id,
            exchange=exchange,
            symbol=symbol,
            type="BUY",
            amount=-total_cost,
            asset_qty=quantity,
            price=price,
            fee=fee,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
            metadata=metadata or {},
        )

    async def record_sell(
        self,
        account_id: str,
        exchange: str,
        symbol: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LedgerTransaction:
        """Record sell order execution, add proceeds minus fee to cash balance."""
        current_balance = await self.get_balance(account_id)
        net_proceeds = (quantity * price) - fee
        new_balance = current_balance + net_proceeds
        tx_id = f"tx_sel_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(UTC).isoformat()
        meta_json = json.dumps(metadata or {})

        await record_ledger_transaction_pg(
            transaction_id=tx_id,
            account_id=account_id,
            exchange=exchange,
            symbol=symbol,
            tx_type="SELL",
            amount=net_proceeds,
            asset_qty=-quantity,
            price=price,
            fee=fee,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
            metadata=meta_json,
        )

        return LedgerTransaction(
            transaction_id=tx_id,
            account_id=account_id,
            exchange=exchange,
            symbol=symbol,
            type="SELL",
            amount=net_proceeds,
            asset_qty=-quantity,
            price=price,
            fee=fee,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
            metadata=metadata or {},
        )

    async def record_sell_short(
        self,
        account_id: str,
        exchange: str,
        symbol: str,
        quantity: float,
        price: float,
        cost_basis: float,
        fee: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LedgerTransaction:
        """Record short sell order execution, add borrowed asset and record cost basis."""
        current_balance = await self.get_balance(account_id)
        total_cost = cost_basis + fee
        if total_cost > current_balance:
            raise ValueError(
                f"Insufficient funds: cost {total_cost:.2f} exceeds available balance {current_balance:.2f}"
            )

        new_balance = current_balance - total_cost
        tx_id = f"tx_sellshort_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(UTC).isoformat()
        meta_json = json.dumps(metadata or {})

        await record_ledger_transaction_pg(
            transaction_id=tx_id,
            account_id=account_id,
            exchange=exchange,
            symbol=symbol,
            tx_type="SELL_SHORT",
            amount=-cost_basis,
            asset_qty=-quantity,
            price=price,
            fee=fee,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
            metadata=meta_json,
        )

        return LedgerTransaction(
            transaction_id=tx_id,
            account_id=account_id,
            exchange=exchange,
            symbol=symbol,
            type="SELL_SHORT",
            amount=-cost_basis,
            asset_qty=-quantity,
            price=price,
            fee=fee,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
            metadata=metadata or {},
        )

    async def record_close_position(
        self,
        account_id: str,
        pos: PaperPosition,
        exit_price: float,
        fee: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LedgerTransaction:
        """
        Record order execution to close an open position (SELL for LONG, BUY_TO_COVER for SHORT).
        Accurately credits cash balance with collateral returned plus realized PnL.
        """
        current_balance = await self.get_balance(account_id)
        if pos.side == "LONG":
            net_proceeds = (pos.quantity * exit_price) - fee
            new_balance = current_balance + net_proceeds
            tx_type = "SELL"
            asset_qty = -pos.quantity
            amount = net_proceeds
        else:
            # For SHORT position:
            # Cost basis was reserved from cash on entry.
            # Realized gross PnL is (pos.entry_price - exit_price) * pos.quantity.
            # Cash returned = pos.cost_basis + (pos.entry_price - exit_price) * pos.quantity - fee
            net_returned = (
                pos.cost_basis + ((pos.entry_price - exit_price) * pos.quantity) - fee
            )
            new_balance = current_balance + net_returned
            tx_type = "BUY_TO_COVER"
            asset_qty = pos.quantity
            amount = net_returned

        tx_id = f"tx_cls_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(UTC).isoformat()
        meta_json = json.dumps(metadata or {})

        await record_ledger_transaction_pg(
            transaction_id=tx_id,
            account_id=account_id,
            exchange=pos.exchange,
            symbol=pos.symbol,
            tx_type=tx_type,
            amount=amount,
            asset_qty=asset_qty,
            price=exit_price,
            fee=fee,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
            metadata=meta_json,
        )

        return LedgerTransaction(
            transaction_id=tx_id,
            account_id=account_id,
            exchange=pos.exchange,
            symbol=pos.symbol,
            type=tx_type,
            amount=amount,
            asset_qty=asset_qty,
            price=exit_price,
            fee=fee,
            balance_after=new_balance,
            timestamp=now_iso,
            notes=notes,
            metadata=metadata or {},
        )

    async def save_position(self, pos: PaperPosition) -> None:
        """Upsert open paper trading position state."""
        await update_paper_position_pg(
            account_id=pos.account_id,
            exchange=pos.exchange,
            symbol=pos.symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            current_price=pos.current_price,
            quantity=pos.quantity,
            cost_basis=pos.cost_basis,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            unrealized_pnl=pos.unrealized_pnl,
            unrealized_pnl_pct=pos.unrealized_pnl_pct,
            entry_time=pos.entry_time,
            last_bar_time=pos.last_bar_time,
            metadata=pos.metadata
            or {},  # Pass dict; update_paper_position_pg handles serialization
        )

    async def get_position(
        self, exchange: str, symbol: str, account_id: str = "default"
    ) -> PaperPosition | None:
        """Get active open position for an asset."""
        row = await get_paper_position_pg(
            exchange=exchange, symbol=symbol, account_id=account_id
        )
        if not row:
            return None
        return self._row_to_position(row)

    async def list_positions(
        self, exchange: str | None = None, account_id: str = "default"
    ) -> list[PaperPosition]:
        """List all active open positions."""
        rows = await get_paper_positions_pg(account_id=account_id)
        if exchange:
            rows = [
                r for r in rows if r.get("exchange", "").lower() == exchange.lower()
            ]
        return [self._row_to_position(r) for r in rows]

    async def close_position(
        self, exchange: str, symbol: str, account_id: str = "default"
    ) -> bool:
        """Remove open position from active list upon exit."""
        return await remove_paper_position_pg(
            exchange=exchange, symbol=symbol, account_id=account_id
        )

    async def record_completed_trade(
        self, trade: PaperTradeRecord, account_id: str = "default"
    ) -> int:
        """Record completed round-trip trade into history."""
        return await save_paper_trade_record_pg(
            trade_id=trade.trade_id,
            account_id=account_id,
            exchange=trade.exchange,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
            side=trade.side,
            entry_price=trade.entry_price,
            exit_price=trade.exit_price,
            quantity=trade.quantity,
            entry_time=trade.entry_time,
            exit_time=trade.exit_time,
            holding_period_bars=trade.holding_period_bars,
            realized_pnl=trade.realized_pnl,
            realized_pnl_pct=trade.realized_pnl_pct,
            fees=trade.fees,
            exit_reason=trade.exit_reason,
            params=trade.params or {},
            metadata=trade.metadata or {},
        )

    async def list_trade_history(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        exit_reason: str | None = None,
        limit: int = 100,
    ) -> list[PaperTradeRecord]:
        """Query completed round-trip trades."""
        rows = await get_paper_trade_history_pg(
            exchange=exchange, symbol=symbol, exit_reason=exit_reason, limit=limit
        )
        return [self._row_to_trade_record(r) for r in rows]

    def list_trade_history_sync(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        exit_reason: str | None = None,
        limit: int = 100,
    ) -> list:
        """Sync wrapper for list_trade_history."""
        from src.sync_bridge import run_async

        return run_async(
            self.list_trade_history(
                exchange=exchange,
                symbol=symbol,
                exit_reason=exit_reason,
                limit=limit,
            ),
            timeout=600.0,
        )

    async def get_statistics(
        self, exchange: str | None = None, symbol: str | None = None
    ) -> dict[str, Any]:
        """Get aggregate performance statistics for paper trading."""
        return await get_paper_trade_statistics_pg(exchange=exchange, symbol=symbol)

    async def list_transactions(
        self,
        account_id: str = "default",
        exchange: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[LedgerTransaction]:
        """List transaction audit log."""
        rows = await get_paper_ledger_transactions_pg(
            account_id=account_id, limit=limit, offset=0
        )
        txs = []
        for r in rows:
            try:
                meta = json.loads(r.get("metadata", "{}"))
            except Exception:
                meta = {}
            txs.append(
                LedgerTransaction(
                    transaction_id=r.get("transaction_id", ""),
                    account_id=r.get("account_id", ""),
                    exchange=r.get("exchange", ""),
                    symbol=r.get("symbol", ""),
                    type=r.get("tx_type", ""),  # DB column is tx_type
                    amount=float(r.get("amount", 0.0)),
                    asset_qty=float(r.get("asset_qty", 0.0)),
                    price=float(r.get("price", 0.0)),
                    fee=float(r.get("fee", 0.0)),
                    balance_after=float(r.get("balance_after", 0.0)),
                    timestamp=r.get("timestamp"),
                    notes=r.get("notes", ""),
                    metadata=meta,
                )
            )
        return txs

    def list_transactions_sync(
        self,
        account_id: str = "default",
        exchange: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list:
        """Sync wrapper for list_transactions."""
        from src.sync_bridge import run_async

        return run_async(
            self.list_transactions(
                account_id=account_id, exchange=exchange, symbol=symbol, limit=limit
            ),
            timeout=600.0,
        )

    async def reset(
        self, account_id: str = "default", initial_deposit: float = 10000.0
    ) -> dict[str, Any]:
        """Reset all paper trading data for an account to initial state ($10,000 balance, no positions, no history)."""
        return await reset_paper_account(account_id, initial_deposit=initial_deposit)

    def reset_sync(
        self, account_id: str = "default", initial_deposit: float = 10000.0
    ) -> dict[str, Any]:
        """Sync wrapper for reset."""
        from src.sync_bridge import run_async

        return run_async(
            self.reset(account_id=account_id, initial_deposit=initial_deposit),
            timeout=600.0,
        )

    # Async wrapper methods for use from main event loop (run_async from background thread)
    async def aget_balance(self, account_id: str = "default") -> float:
        """Async wrapper for get_balance."""
        return await self.get_balance(account_id=account_id)

    async def aget_total_equity(self, account_id: str = "default") -> float:
        """Async wrapper for get_total_equity."""
        return await self.get_total_equity(account_id=account_id)

    async def alist_positions(
        self, exchange: str | None = None, account_id: str = "default"
    ) -> list:
        """Async wrapper for list_positions."""
        return await self.list_positions(exchange=exchange, account_id=account_id)

    async def aget_statistics(
        self, exchange: str | None = None, symbol: str | None = None
    ) -> dict:
        """Async wrapper for get_statistics."""
        return await self.get_statistics(exchange=exchange, symbol=symbol)

    @classmethod
    def _row_to_position(cls, row: dict[str, Any]) -> PaperPosition:
        meta_raw = row.get("metadata", {})
        if isinstance(meta_raw, str):
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {}
        elif isinstance(meta_raw, dict):
            meta = meta_raw
        else:
            meta = {}

        return PaperPosition(
            exchange=row.get("exchange", ""),
            symbol=row.get("symbol", ""),
            side=row.get("side", ""),
            entry_price=float(row.get("entry_price", 0.0)),
            current_price=float(row.get("current_price", 0.0)),
            quantity=float(row.get("quantity", 0.0)),
            cost_basis=float(row.get("cost_basis", 0.0)),
            stop_loss=(
                float(row["stop_loss"]) if row.get("stop_loss") is not None else None
            ),
            take_profit=(
                float(row["take_profit"])
                if row.get("take_profit") is not None
                else None
            ),
            unrealized_pnl=float(row.get("unrealized_pnl", 0.0)),
            unrealized_pnl_pct=float(row.get("unrealized_pnl_pct", 0.0)),
            entry_time=row.get("entry_time"),
            updated_time=row.get("updated_time"),
            last_bar_time=row.get("last_bar_time"),
            metadata=meta,
            account_id=row.get("account_id", "default"),
        )

    @classmethod
    def _row_to_trade_record(cls, row: dict[str, Any]) -> PaperTradeRecord:
        try:
            params = json.loads(row.get("params_json", "{}"))
        except Exception:
            params = {}
        try:
            meta = json.loads(row.get("metadata", "{}"))
        except Exception:
            meta = {}

        return PaperTradeRecord(
            trade_id=row.get("trade_id", ""),
            exchange=row.get("exchange", ""),
            symbol=row.get("symbol", ""),
            timeframe=row.get("timeframe", ""),
            side=row.get("side", ""),
            entry_price=float(row.get("entry_price", 0.0)),
            exit_price=float(row.get("exit_price", 0.0)),
            quantity=float(row.get("quantity", 0.0)),
            entry_time=row.get("entry_time", ""),
            exit_time=row.get("exit_time", ""),
            holding_period_bars=int(row.get("holding_period_bars", 0)),
            realized_pnl=float(row.get("realized_pnl", 0.0)),
            realized_pnl_pct=float(row.get("realized_pnl_pct", 0.0)),
            fees=float(row.get("fees", 0.0)),
            exit_reason=row.get("exit_reason", "MANUAL"),
            params=params,
            metadata=meta,
        )

    # Sync wrapper methods for background thread use (polling thread)
    def list_positions_sync(
        self, exchange: str | None = None, account_id: str = "default"
    ) -> list[PaperPosition]:
        """Sync wrapper for list_positions."""
        from src.sync_bridge import run_async

        return run_async(
            self.list_positions(exchange=exchange, account_id=account_id),
            timeout=600.0,
        )

    def get_balance_sync(self, account_id: str = "default") -> float:
        """Sync wrapper for get_balance."""
        from src.sync_bridge import run_async

        return run_async(self.get_balance(account_id=account_id), timeout=600.0)

    def get_total_equity_sync(self, account_id: str = "default") -> float:
        """Sync wrapper for get_total_equity."""
        from src.sync_bridge import run_async

        return run_async(self.get_total_equity(account_id=account_id), timeout=600.0)

    def get_statistics_sync(
        self, exchange: str | None = None, symbol: str | None = None
    ) -> dict:
        """Sync wrapper for get_statistics."""
        from src.sync_bridge import run_async

        return run_async(
            self.get_statistics(exchange=exchange, symbol=symbol), timeout=600.0
        )

    def get_position_sync(
        self, exchange: str, symbol: str, account_id: str = "default"
    ) -> PaperPosition | None:
        """Sync wrapper for get_position."""
        from src.sync_bridge import run_async

        return run_async(
            self.get_position(exchange=exchange, symbol=symbol, account_id=account_id),
            timeout=600.0,
        )

    def deposit_sync(
        self,
        account_id: str = "default",
        amount: float = 10000.0,
        notes: str = "Initial deposit",
    ) -> LedgerTransaction:
        """Sync wrapper for deposit."""
        from src.sync_bridge import run_async

        return run_async(
            self.deposit(account_id=account_id, amount=amount, notes=notes),
            timeout=600.0,
        )

    def withdraw_sync(
        self,
        account_id: str = "default",
        amount: float = 1000.0,
        notes: str = "Withdrawal",
    ) -> LedgerTransaction:
        """Sync wrapper for withdraw."""
        from src.sync_bridge import run_async

        return run_async(
            self.withdraw(account_id=account_id, amount=amount, notes=notes),
            timeout=600.0,
        )

    def close_position_sync(
        self, exchange: str, symbol: str, account_id: str = "default"
    ) -> bool:
        """Sync wrapper for close_position."""
        from src.sync_bridge import run_async

        return run_async(
            self.close_position(
                exchange=exchange, symbol=symbol, account_id=account_id
            ),
            timeout=600.0,
        )

    def record_completed_trade_sync(
        self, trade: PaperTradeRecord, account_id: str = "default"
    ) -> int:
        """Sync wrapper for record_completed_trade."""
        from src.sync_bridge import run_async

        return run_async(
            self.record_completed_trade(trade, account_id=account_id),
            timeout=600.0,
        )

    def record_buy_sync(
        self,
        account_id: str,
        exchange: str,
        symbol: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LedgerTransaction:
        """Sync wrapper for record_buy."""
        from src.sync_bridge import run_async

        return run_async(
            self.record_buy(
                account_id=account_id,
                exchange=exchange,
                symbol=symbol,
                quantity=quantity,
                price=price,
                fee=fee,
                notes=notes,
                metadata=metadata,
            ),
            timeout=600.0,
        )

    def record_sell_sync(
        self,
        account_id: str,
        exchange: str,
        symbol: str,
        quantity: float,
        price: float,
        fee: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LedgerTransaction:
        """Sync wrapper for record_sell."""
        from src.sync_bridge import run_async

        return run_async(
            self.record_sell(
                account_id=account_id,
                exchange=exchange,
                symbol=symbol,
                quantity=quantity,
                price=price,
                fee=fee,
                notes=notes,
                metadata=metadata,
            ),
            timeout=600.0,
        )

    def record_sell_short_sync(
        self,
        account_id: str,
        exchange: str,
        symbol: str,
        quantity: float,
        price: float,
        cost_basis: float,
        fee: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LedgerTransaction:
        """Sync wrapper for record_sell_short."""
        from src.sync_bridge import run_async

        return run_async(
            self.record_sell_short(
                account_id=account_id,
                exchange=exchange,
                symbol=symbol,
                quantity=quantity,
                price=price,
                cost_basis=cost_basis,
                fee=fee,
                notes=notes,
                metadata=metadata,
            ),
            timeout=600.0,
        )

    def record_close_position_sync(
        self,
        account_id: str,
        pos: PaperPosition,
        exit_price: float,
        fee: float = 0.0,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> LedgerTransaction:
        """Sync wrapper for record_close_position."""
        from src.sync_bridge import run_async

        return run_async(
            self.record_close_position(
                account_id=account_id,
                pos=pos,
                exit_price=exit_price,
                fee=fee,
                notes=notes,
                metadata=metadata,
            ),
            timeout=600.0,
        )

    def save_position_sync(
        self,
        pos: PaperPosition,
    ) -> int:
        """Sync wrapper for save_position."""
        from src.sync_bridge import run_async

        return run_async(self.save_position(pos), timeout=600.0)
