"""
Paper Trading Domain Models and Data Structures.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PaperProfile:
    """Per-asset Optuna strategy parameter profile."""

    exchange: str
    symbol: str
    timeframe: str
    strategy_mode: str
    target_metric: str
    params: dict[str, Any]
    optuna_score: float = 0.0
    is_active: bool = True
    profile_id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def get_strategy_params(self) -> Any:
        """Parse profile params dictionary into validated StrategyParams dataclass."""
        from src.config import StrategyParams

        return StrategyParams.from_dict(self.params or {})


@dataclass
class PaperPosition:
    """Active open paper trading position."""

    exchange: str
    symbol: str
    side: str  # 'LONG' or 'SHORT'
    entry_price: float
    current_price: float
    quantity: float
    cost_basis: float
    stop_loss: float | None = None
    take_profit: float | None = None
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    entry_time: str | None = None
    updated_time: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "quantity": self.quantity,
            "cost_basis": self.cost_basis,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "entry_time": self.entry_time,
            "updated_time": self.updated_time,
            "metadata": self.metadata,
        }


@dataclass
class LedgerTransaction:
    """Double-entry transaction audit record."""

    transaction_id: str
    account_id: str
    exchange: str
    symbol: str
    type: str  # 'DEPOSIT', 'WITHDRAWAL', 'BUY', 'SELL', 'FEE', 'PNL_ADJUST'
    amount: float  # Cash delta
    asset_qty: float = 0.0  # Asset delta
    price: float = 0.0
    fee: float = 0.0
    balance_after: float = 0.0
    timestamp: str | None = None
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaperTradeRecord:
    """Completed round-trip paper trade record."""

    trade_id: str
    exchange: str
    symbol: str
    timeframe: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: str
    exit_time: str
    holding_period_bars: int
    realized_pnl: float
    realized_pnl_pct: float
    fees: float = 0.0
    exit_reason: str = (
        "MANUAL"  # 'TAKE_PROFIT', 'STOP_LOSS', 'SIGNAL', 'TIMEOUT', 'MANUAL'
    )
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
