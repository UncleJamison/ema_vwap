"""
Paper Trading Transaction Ledger and Accounting Module.
Implements double-entry ledger auditing, virtual balances, position lifecycles, and trade settlement.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from src.database import CandleDatabase
from src.database import db as default_db
from src.paper.models import (
    LedgerTransaction,
    PaperPosition,
    PaperTradeRecord,
)


class PaperLedger:
    """Double-entry transaction ledger and virtual portfolio balance manager."""

    def __init__(self, db: CandleDatabase | None = None) -> None:
        self.db = db or default_db

    def get_balance(self, account_id: str = "default") -> float:
        """Get the current cash balance for the paper account."""
        return self.db.get_account_balance(account_id=account_id)

    def deposit(
        self,
        account_id: str = "default",
        amount: float = 10000.0,
        notes: str = "Initial deposit",
    ) -> LedgerTransaction:
        """Deposit cash into the virtual paper trading account."""
        current_balance = self.get_balance(account_id)
        new_balance = current_balance + amount
        tx_id = f"tx_dep_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        self.db.record_ledger_transaction(
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

    def withdraw(
        self,
        account_id: str = "default",
        amount: float = 1000.0,
        notes: str = "Withdrawal",
    ) -> LedgerTransaction:
        """Withdraw cash from the paper trading account."""
        current_balance = self.get_balance(account_id)
        if amount > current_balance:
            raise ValueError(
                f"Insufficient paper balance ({current_balance}) for withdrawal of {amount}"
            )

        new_balance = current_balance - amount
        tx_id = f"tx_wth_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        self.db.record_ledger_transaction(
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

    def record_buy(
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
        current_balance = self.get_balance(account_id)
        total_cost = (quantity * price) + fee
        if total_cost > current_balance:
            raise ValueError(
                f"Insufficient funds: cost {total_cost:.2f} exceeds available balance {current_balance:.2f}"
            )

        new_balance = current_balance - total_cost
        tx_id = f"tx_buy_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {})

        self.db.record_ledger_transaction(
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

    def record_sell(
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
        current_balance = self.get_balance(account_id)
        net_proceeds = (quantity * price) - fee
        new_balance = current_balance + net_proceeds
        tx_id = f"tx_sel_{uuid.uuid4().hex[:10]}"
        now_iso = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {})

        self.db.record_ledger_transaction(
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

    def record_close_position(
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
        current_balance = self.get_balance(account_id)
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
        now_iso = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata or {})

        self.db.record_ledger_transaction(
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

    def save_position(self, pos: PaperPosition) -> int:
        """Upsert open paper trading position state."""
        meta_json = json.dumps(pos.metadata or {})
        return self.db.save_paper_position(
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
            metadata=meta_json,
        )

    def get_position(self, exchange: str, symbol: str) -> PaperPosition | None:
        """Get active open position for an asset."""
        row = self.db.get_paper_position(exchange=exchange, symbol=symbol)
        if not row:
            return None
        return self._row_to_position(row)

    def list_positions(self, exchange: str | None = None) -> list[PaperPosition]:
        """List all active open positions."""
        rows = self.db.list_paper_positions(exchange=exchange)
        return [self._row_to_position(r) for r in rows]

    def close_position(self, exchange: str, symbol: str) -> bool:
        """Remove open position from active list upon exit."""
        return self.db.close_paper_position(exchange=exchange, symbol=symbol)

    def record_completed_trade(self, trade: PaperTradeRecord) -> int:
        """Record completed round-trip trade into history."""
        params_json = json.dumps(trade.params or {})
        meta_json = json.dumps(trade.metadata or {})
        return self.db.record_paper_trade_history(
            trade_id=trade.trade_id,
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
            params_json=params_json,
            metadata=meta_json,
        )

    def list_trade_history(
        self,
        exchange: str | None = None,
        symbol: str | None = None,
        exit_reason: str | None = None,
        limit: int = 100,
    ) -> list[PaperTradeRecord]:
        """Query completed round-trip trades."""
        rows = self.db.list_paper_trade_history(
            exchange=exchange, symbol=symbol, exit_reason=exit_reason, limit=limit
        )
        return [self._row_to_trade_record(r) for r in rows]

    def get_statistics(
        self, exchange: str | None = None, symbol: str | None = None
    ) -> dict[str, Any]:
        """Get aggregate performance statistics for paper trading."""
        return self.db.get_paper_trade_statistics(exchange=exchange, symbol=symbol)

    def list_transactions(
        self,
        account_id: str = "default",
        exchange: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[LedgerTransaction]:
        """List transaction audit log."""
        rows = self.db.list_ledger_transactions(
            account_id=account_id, exchange=exchange, symbol=symbol, limit=limit
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
                    type=r.get("type", ""),
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

    @staticmethod
    def _row_to_position(row: dict[str, Any]) -> PaperPosition:
        try:
            meta = json.loads(row.get("metadata", "{}"))
        except Exception:
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
            metadata=meta,
        )

    @staticmethod
    def _row_to_trade_record(row: dict[str, Any]) -> PaperTradeRecord:
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
