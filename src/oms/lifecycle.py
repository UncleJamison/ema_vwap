"""
Order Lifecycle Management (ema_vwap-6a1).

Implements a complete order state machine with transitions:
NEW -> PENDING -> PARTIALLY_FILLED -> FILLED
           -> REJECTED
           -> CANCELLED
           -> EXPIRED

Each transition is validated and auditable.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.logger import logger


class OrderStatus(enum.Enum):
    """Order lifecycle states."""

    NEW = "new"  # Created locally, not yet submitted
    PENDING = "pending"  # Submitted to venue, awaiting response
    PARTIALLY_FILLED = "partially_filled"  # Some quantity filled
    FILLED = "filled"  # Fully filled
    CANCELLED = "cancelled"  # Cancelled by user/system
    REJECTED = "rejected"  # Rejected by venue or risk checks
    EXPIRED = "expired"  # Time-in-force expired
    PENDING_CANCEL = "pending_cancel"  # Cancel request submitted


class OrderSide(enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(enum.Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(enum.Enum):
    GTC = "gtc"  # Good Till Cancelled
    IOC = "ioc"  # Immediate Or Cancel
    FOK = "fok"  # Fill Or Kill
    DAY = "day"  # End of trading day


# Valid state transitions
VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.NEW: {OrderStatus.PENDING, OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.PENDING: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
        OrderStatus.PENDING_CANCEL,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED,  # Additional partial fills
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.PENDING_CANCEL,
        OrderStatus.EXPIRED,
    },
    OrderStatus.FILLED: set(),  # Terminal
    OrderStatus.CANCELLED: set(),  # Terminal
    OrderStatus.REJECTED: set(),  # Terminal
    OrderStatus.EXPIRED: set(),  # Terminal
    OrderStatus.PENDING_CANCEL: {
        OrderStatus.CANCELLED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.REJECTED,  # Cancel rejected
    },
}


@dataclass
class OrderEvent:
    """Audit event for order state transitions."""

    to_status: OrderStatus
    from_status: OrderStatus | None = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: float = field(default_factory=time.time)
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "from_status": self.from_status.value if self.from_status else None,
            "to_status": self.to_status.value,
            "reason": self.reason,
            "metadata": self.metadata,
        }


@dataclass
class Order:
    """
    Complete order with lifecycle tracking.

    Extends VenueOrder with full audit trail, risk metadata, and TCA fields.
    """

    # Identity
    order_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    client_order_id: str | None = None  # User-provided ID

    # Core order params
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: float = 0.0
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.GTC

    # Execution venue
    venue: str = "auto"
    account_id: str = "default"

    # Lifecycle
    status: OrderStatus = OrderStatus.NEW
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    submitted_at: float | None = None
    filled_at: float | None = None

    # Fill tracking
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    remaining_quantity: float = 0.0
    fills: list[dict[str, Any]] = field(default_factory=list)

    # Fees & costs
    commission_usd: float = 0.0
    exchange_fees_usd: float = 0.0

    # Risk & TCA
    risk_checks_passed: bool = False
    risk_check_details: dict[str, Any] = field(default_factory=dict)
    tca_slippage_bps: float = 0.0
    tca_market_impact_bps: float = 0.0
    arrival_price: float | None = None
    implementation_shortfall_bps: float = 0.0

    # Audit trail
    events: list[OrderEvent] = field(default_factory=list)
    error_message: str | None = None

    # Execution algorithm
    algorithm: str | None = None  # TWAP, VWAP, POV, IS
    algorithm_params: dict[str, Any] = field(default_factory=dict)

    # Metadata
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.remaining_quantity = self.quantity
        if self.client_order_id is None:
            self.client_order_id = f"cli_{self.order_id}"
        # Initial event
        self.events.append(
            OrderEvent(
                to_status=OrderStatus.NEW,
                reason="Order created",
                metadata={"venue": self.venue, "side": self.side.value},
            )
        )

    def transition_to(
        self, new_status: OrderStatus, reason: str = "", metadata: dict | None = None
    ) -> bool:
        """Attempt state transition with validation."""
        if new_status not in VALID_TRANSITIONS.get(self.status, set()):
            logger.warning(
                f"[OrderLifecycle] Invalid transition {self.status.value} -> {new_status.value} "
                f"for order {self.order_id}: {reason}"
            )
            return False

        old_status = self.status
        self.status = new_status
        self.updated_at = time.time()

        if new_status == OrderStatus.PENDING and self.submitted_at is None:
            self.submitted_at = time.time()
        if new_status == OrderStatus.FILLED:
            self.filled_at = time.time()

        self.events.append(
            OrderEvent(
                from_status=old_status,
                to_status=new_status,
                reason=reason,
                metadata=metadata or {},
            )
        )

        logger.info(
            f"[OrderLifecycle] Order {self.order_id}: {old_status.value} -> {new_status.value} "
            f"({reason})"
        )
        return True

    def add_fill(
        self,
        fill_qty: float,
        fill_price: float,
        fill_time: float | None = None,
        fee_usd: float = 0.0,
        exchange_fee_usd: float = 0.0,
        fill_id: str | None = None,
    ) -> None:
        """Record a partial or full fill."""
        if fill_qty <= 0:
            return

        fill_qty = min(fill_qty, self.remaining_quantity)
        if fill_qty <= 0:
            return

        # Update average fill price (VWAP of fills)
        if self.filled_quantity > 0:
            total_value = (
                self.avg_fill_price * self.filled_quantity + fill_price * fill_qty
            )
            self.avg_fill_price = total_value / (self.filled_quantity + fill_qty)
        else:
            self.avg_fill_price = fill_price

        self.filled_quantity += fill_qty
        self.remaining_quantity = self.quantity - self.filled_quantity
        self.commission_usd += fee_usd
        self.exchange_fees_usd += exchange_fee_usd

        fill = {
            "fill_id": fill_id or str(uuid.uuid4())[:12],
            "quantity": fill_qty,
            "price": fill_price,
            "timestamp": fill_time or time.time(),
            "fee_usd": fee_usd,
            "exchange_fee_usd": exchange_fee_usd,
        }
        self.fills.append(fill)

        # Update status based on fill
        if self.remaining_quantity <= 1e-8:
            self.transition_to(OrderStatus.FILLED, "Fully filled")
        elif self.filled_quantity > 0:
            self.transition_to(
                OrderStatus.PARTIALLY_FILLED, f"Partial fill: {fill_qty}"
            )

        self.updated_at = time.time()

    def request_cancel(self, reason: str = "User requested") -> bool:
        """Request order cancellation."""
        if self.status in {OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED}:
            return self.transition_to(OrderStatus.PENDING_CANCEL, reason)
        return False

    def confirm_cancel(self, reason: str = "Cancelled") -> bool:
        """Confirm cancellation (called when venue confirms)."""
        if self.status == OrderStatus.PENDING_CANCEL:
            return self.transition_to(OrderStatus.CANCELLED, reason)
        return False

    def reject(self, reason: str, metadata: dict | None = None) -> bool:
        """Mark order as rejected."""
        if self.status in {
            OrderStatus.NEW,
            OrderStatus.PENDING,
            OrderStatus.PENDING_CANCEL,
        }:
            return self.transition_to(OrderStatus.REJECTED, reason, metadata)
        return False

    def expire(self, reason: str = "Time in force expired") -> bool:
        """Mark order as expired."""
        if self.status in {OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED}:
            return self.transition_to(OrderStatus.EXPIRED, reason)
        return False

    def is_terminal(self) -> bool:
        """Check if order is in a terminal state."""
        return self.status in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }

    def is_active(self) -> bool:
        """Check if order is still active (can be filled/cancelled)."""
        return self.status in {
            OrderStatus.NEW,
            OrderStatus.PENDING,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.PENDING_CANCEL,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "time_in_force": self.time_in_force.value,
            "venue": self.venue,
            "account_id": self.account_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "submitted_at": self.submitted_at,
            "filled_at": self.filled_at,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "remaining_quantity": self.remaining_quantity,
            "fills": self.fills,
            "commission_usd": self.commission_usd,
            "exchange_fees_usd": self.exchange_fees_usd,
            "risk_checks_passed": self.risk_checks_passed,
            "risk_check_details": self.risk_check_details,
            "tca_slippage_bps": self.tca_slippage_bps,
            "tca_market_impact_bps": self.tca_market_impact_bps,
            "arrival_price": self.arrival_price,
            "implementation_shortfall_bps": self.implementation_shortfall_bps,
            "events": [e.to_dict() for e in self.events],
            "error_message": self.error_message,
            "algorithm": self.algorithm,
            "algorithm_params": self.algorithm_params,
            "tags": self.tags,
            "metadata": self.metadata,
        }


class OrderManager:
    """Manages order lifecycle, persistence, and queries."""

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._client_id_index: dict[str, str] = {}  # client_order_id -> order_id

    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        venue: str = "auto",
        account_id: str = "default",
        client_order_id: str | None = None,
        algorithm: str | None = None,
        algorithm_params: dict | None = None,
    ) -> Order:
        """Create a new order in NEW state."""
        order = Order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            venue=venue,
            account_id=account_id,
            client_order_id=client_order_id,
            algorithm=algorithm,
            algorithm_params=algorithm_params or {},
        )
        self._orders[order.order_id] = order
        self._client_id_index[order.client_order_id] = order.order_id
        logger.info(
            f"[OrderManager] Created order {order.order_id}: {side.value} {quantity} {symbol} @ {venue}"
        )
        return order

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_order_by_client_id(self, client_order_id: str) -> Order | None:
        oid = self._client_id_index.get(client_order_id)
        return self._orders.get(oid) if oid else None

    def get_orders_by_status(self, status: OrderStatus) -> list[Order]:
        return [o for o in self._orders.values() if o.status == status]

    def get_active_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.is_active()]

    def submit_order(self, order_id: str, arrival_price: float | None = None) -> bool:
        """Mark order as submitted to venue (NEW -> PENDING)."""
        order = self._orders.get(order_id)
        if not order:
            return False
        if arrival_price:
            order.arrival_price = arrival_price
        return order.transition_to(OrderStatus.PENDING, "Submitted to venue")

    def process_fill(
        self,
        order_id: str,
        fill_qty: float,
        fill_price: float,
        fee_usd: float = 0.0,
        exchange_fee_usd: float = 0.0,
        fill_time: float | None = None,
        fill_id: str | None = None,
    ) -> bool:
        """Process a fill from venue."""
        order = self._orders.get(order_id)
        if not order or not order.is_active():
            return False
        order.add_fill(
            fill_qty, fill_price, fill_time, fee_usd, exchange_fee_usd, fill_id
        )
        return True

    def cancel_order(self, order_id: str, reason: str = "User requested") -> bool:
        """Request cancellation."""
        order = self._orders.get(order_id)
        if not order:
            return False
        return order.request_cancel(reason)

    def confirm_cancel(self, order_id: str, reason: str = "Cancelled") -> bool:
        """Confirm cancellation from venue."""
        order = self._orders.get(order_id)
        if not order:
            return False
        return order.confirm_cancel(reason)

    def reject_order(self, order_id: str, reason: str) -> bool:
        """Mark order rejected."""
        order = self._orders.get(order_id)
        if not order:
            return False
        return order.reject(reason)

    def expire_order(self, order_id: str, reason: str = "TIF expired") -> bool:
        """Mark order expired."""
        order = self._orders.get(order_id)
        if not order:
            return False
        return order.expire(reason)


# Global instance
order_manager = OrderManager()
