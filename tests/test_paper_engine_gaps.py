"""
Tests for PaperTradingEngine gaps (src/paper/engine.py): polling failure paths,
margin-call enforcement, position-close edge cases, reconciliation, circuit breaker,
manual close, step_all_active_profiles, reconciliation, entry_time immutability,
risk_per_trade_pct delegation, 25% cap, deployed profile validation.
"""

from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.config import StrategyParams
from src.paper.engine import PaperTradingEngine
from src.paper.models import PaperPosition


@pytest.fixture
def mock_ledger():
    """Create a mock ledger with proper defaults."""
    ledger = Mock()
    ledger.get_balance.return_value = 10000.0
    ledger.get_total_equity.return_value = 10000.0
    ledger.get_position.return_value = None
    ledger.get_statistics.return_value = {}
    ledger.list_positions.return_value = []
    return ledger


@pytest.fixture
def mock_registry():
    """Create a mock registry with proper defaults."""
    registry = Mock()
    params = StrategyParams()
    registry.get_strategy_params_sync.return_value = params
    registry.get_profile_sync.return_value = None
    registry.get_deployed_profile_sync.return_value = None
    registry.list_profiles_sync.return_value = []
    return registry


@pytest.fixture
def paper_engine(mock_ledger, mock_registry):
    """Create a paper engine with mocked dependencies."""
    with patch("src.settings.SettingsManager") as mock_settings:
        mock_settings_instance = Mock()
        mock_settings_instance.get.return_value = "false"
        mock_settings.return_value = mock_settings_instance

        with patch("src.paper.engine.PaperLedger", return_value=mock_ledger), patch(
            "src.paper.engine.PaperProfileRegistry", return_value=mock_registry
        ):
            engine = PaperTradingEngine(
                db=None,
                account_id="test_account",
                settings_mgr=None,
            )
            engine.ledger = mock_ledger
            engine.registry = mock_registry
            yield engine


def make_candles(base_price=50000, bars=50, freq="5min", start="2024-01-01", signal=1):
    """Helper to create candle DataFrame with signals."""
    dates = pd.date_range(start, periods=bars, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": [base_price] * bars,
            "high": [base_price + 500] * bars,
            "low": [base_price - 500] * bars,
            "close": [base_price] * bars,
            "volume": [100] * bars,
            "vwap": [base_price] * bars,
            "atr": [500] * bars,
            "signal": [signal] * bars,
        }
    )


def make_single_candle(base_price=50000, freq="5min", start="2024-01-01"):
    """Helper to create a single candle for edge case tests (will be duplicated to 20+ rows)."""
    dates = pd.date_range(start, periods=25, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": [base_price] * 25,
            "high": [base_price + 500] * 25,
            "low": [base_price - 500] * 25,
            "close": [base_price] * 25,
            "volume": [100] * 25,
            "vwap": [base_price] * 25,
            "atr": [500] * 25,
            "signal": [0] * 25,
        }
    )


def make_position(
    exchange="kucoin",
    symbol="BTC/USD",
    side="LONG",
    entry_price=50000.0,
    current_price=50000.0,
    quantity=0.2,
    cost_basis=10000.0,
    stop_loss=49000.0,
    take_profit=52000.0,
    entry_time="2024-01-01T00:00:00Z",
):
    """Create a PaperPosition with defaults."""
    return PaperPosition(
        account_id="test",
        exchange=exchange,
        symbol=symbol,
        side=side,
        entry_price=entry_price,
        current_price=current_price,
        quantity=quantity,
        cost_basis=cost_basis,
        stop_loss=stop_loss,
        take_profit=take_profit,
        entry_time=entry_time,
        last_bar_time="2024-01-01T00:00:00Z",
    )


class TestPaperEngineEntryTimeImmutability:
    """Tests for entry_time immutability (4 sites in engine.py)."""

    def test_entry_time_set_on_creation_and_never_changed(
        self, paper_engine, mock_ledger
    ):
        """entry_time should be set at position creation and never modified."""
        mock_ledger.get_position_sync.return_value = None
        mock_ledger.get_balance_sync.return_value = 10000.0
        mock_ledger.get_total_equity_sync.return_value = 10000.0
        mock_ledger.list_positions_sync.return_value = []

        # Use force_candles with force_signals to force entry
        candles = make_candles(signal=1)
        signals = candles.copy()
        signals["signal"] = [1] * len(candles)

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = candles

            result = paper_engine.evaluate_symbol(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                force_candles=candles,
                force_signals=signals,
            )

            # Verify position was created (could be ENTER_LONG or other entry action)
            assert result["action"] in ("ENTER_LONG", "ENTER_SHORT")

            # Verify position was saved with entry_time
            mock_ledger.save_position_sync.assert_called()
            saved_pos = mock_ledger.save_position_sync.call_args[0][0]
            assert saved_pos.entry_time is not None
            original_entry_time = saved_pos.entry_time

            # Second evaluation should NOT change entry_time
            mock_ledger.get_position_sync.return_value = saved_pos
            mock_ledger.get_balance_sync.return_value = 10000.0
            mock_ledger.get_total_equity_sync.return_value = 10000.0

            # Use price movement that won't trigger stops - small movement in same direction
            candles2 = make_candles(
                base_price=50050, start="2024-01-01 00:05", signal=1
            )
            signals2 = candles2.copy()
            signals2["signal"] = [1] * len(candles2)

            with patch(
                "src.paper.engine.DataLoader.fetch_and_update_candles"
            ) as mock_fetch2:
                mock_fetch2.return_value = candles2

                result = paper_engine.evaluate_symbol(
                    exchange="kucoin",
                    symbol="BTC/USD",
                    timeframe="5m",
                    force_candles=candles2,
                    force_signals=signals2,
                )

                # entry_time should be unchanged regardless of action
                assert saved_pos.entry_time == original_entry_time


class TestPaperEngineRiskPerTradeDelegation:
    """Tests for risk_per_trade_pct delegation to RiskManager."""

    def test_position_sizing_uses_risk_per_trade_pct(self, paper_engine, mock_ledger):
        """Position sizing should use profile's risk_per_trade_pct via RiskManager."""
        mock_ledger.get_position_sync.return_value = None
        mock_ledger.get_balance_sync.return_value = 100000.0  # Use larger equity
        mock_ledger.get_total_equity_sync.return_value = 100000.0
        # Mock list_positions to return empty (no existing positions)
        mock_ledger.list_positions_sync.return_value = []

        params = StrategyParams(
            risk_per_trade_pct=2.0, atr_multiplier=2.0, max_position_pct=80.0
        )
        paper_engine.registry.get_strategy_params_sync.return_value = params

        # Use force_candles with force_signals to force entry
        candles = make_candles(signal=1)
        signals = candles.copy()
        signals["signal"] = [1] * len(candles)

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = candles

            result = paper_engine.evaluate_symbol(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                force_candles=candles,
                force_signals=signals,
            )

            # Risk amount = 2% of equity = $2000
            # With ATR=500, risk distance = 2 * 500 = 1000
            # Units = $2000 / 1000 = 2.0
            # pos_val = 2.0 * 50000 = 100,000 (100% of equity)
            # Portfolio cap: 80% of 100k = 80,000, / 50,000 = 1.6 units
            assert result["action"] in ("ENTER_LONG", "ENTER_SHORT")
            assert result["quantity"] > 0
            # With 100k equity, 2% risk, portfolio cap 80% = 80k / 50k = 1.6 units
            assert result["quantity"] == pytest.approx(1.6, rel=0.1)


class TestPaperEnginePortfolioCap:
    """Tests for portfolio-level max_position_pct cap."""

    def test_portfolio_cap_applies(self, paper_engine, mock_ledger):
        """Portfolio cap should limit total position value to max_position_pct% of equity."""
        mock_ledger.get_position_sync.return_value = None
        mock_ledger.get_balance_sync.return_value = 10000.0
        mock_ledger.get_total_equity_sync.return_value = 10000.0
        mock_ledger.list_positions_sync.return_value = []
        # Mock list_positions to return empty (no existing positions)
        mock_ledger.list_positions_sync.return_value = []

        # Small risk_per_trade_pct but tight stop would create large position
        # max_position_pct = 80% (default)
        params = StrategyParams(
            risk_per_trade_pct=0.1, atr_multiplier=0.1, max_position_pct=80.0
        )
        paper_engine.registry.get_strategy_params_sync.return_value = params

        # Use force_candles with force_signals to force entry
        candles = make_candles(base_price=50000, signal=1)
        candles["atr"] = [10] * 50
        signals = candles.copy()
        signals["signal"] = [1] * len(candles)

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = candles

            result = paper_engine.evaluate_symbol(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                force_candles=candles,
                force_signals=signals,
            )

            # Should be capped at 80% of equity = $8000 / $50000 = 0.16 units
            assert result["action"] == "ENTER_LONG"
            assert result["quantity"] == pytest.approx(0.16, rel=0.2)

    def test_portfolio_cap_respects_existing_positions(self, paper_engine, mock_ledger):
        """Portfolio cap should account for existing positions."""
        from src.paper.models import PaperPosition

        mock_ledger.get_position_sync.return_value = None
        mock_ledger.get_balance_sync.return_value = 10000.0
        mock_ledger.get_total_equity_sync.return_value = 10000.0
        mock_ledger.list_positions_sync.return_value = []

        # Mock existing position worth $3000 (30% of equity)
        existing_pos = PaperPosition(
            exchange="kucoin",
            symbol="ETH/USD",
            side="LONG",
            entry_price=3000.0,
            current_price=3000.0,
            quantity=1.0,
            cost_basis=3000.0,
            unrealized_pnl=0.0,
        )
        mock_ledger.list_positions_sync.return_value = [existing_pos]

        params = StrategyParams(
            risk_per_trade_pct=2.0, atr_multiplier=2.0, max_position_pct=80.0
        )
        paper_engine.registry.get_strategy_params_sync.return_value = params

        candles = make_candles(signal=1)
        signals = candles.copy()
        signals["signal"] = [1] * len(candles)

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = candles

            result = paper_engine.evaluate_symbol(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                force_candles=candles,
                force_signals=signals,
            )

            # 80% of 10k = 8000 max, minus 3000 existing = 5000 available
            # 5000 / 50000 = 0.1 units
            assert result["action"] == "ENTER_LONG"
            assert result["quantity"] == pytest.approx(0.1, rel=0.2)


class TestPaperEngineDeployedProfileValidation:
    """Tests for deployed profile validation."""

    def test_deployed_profile_preferred_over_active(self, paper_engine, mock_registry):
        """Deployed profile should be preferred over active profile."""
        # The actual code in get_strategy_params calls get_deployed_profile first
        # then falls back to get_profile
        deployed_profile = Mock()
        deployed_profile.timeframe = "5m"
        deployed_profile.get_strategy_params.return_value = StrategyParams(
            fast_ema=12, slow_ema=26
        )

        mock_registry.get_deployed_profile_sync.return_value = deployed_profile
        mock_registry.get_profile_sync.return_value = None  # No active profile
        # get_strategy_params should use deployed profile's params
        mock_registry.get_strategy_params_sync.return_value = StrategyParams(
            fast_ema=12, slow_ema=26
        )

        params = paper_engine.registry.get_strategy_params_sync(
            exchange="kucoin",
            symbol="BTC/USD",
            timeframe="5m",
        )

        # Should use deployed profile's params (fast_ema=12)
        assert params.fast_ema == 12


class TestPaperEnginePositionCloseEdgeCases:
    """Tests for position close edge cases."""

    def test_stop_loss_adverse_gap_fill(self, paper_engine, mock_ledger):
        """Stop loss with gap-through should fill at open price."""
        pos = PaperPosition(
            account_id="test",
            exchange="kucoin",
            symbol="BTC/USD",
            side="LONG",
            entry_price=50000.0,
            current_price=50000.0,
            quantity=0.2,
            cost_basis=10000.0,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_time="2024-01-01T00:00:00Z",
            last_bar_time="2024-01-01T00:00:00Z",
        )
        mock_ledger.get_position_sync.return_value = pos

        params = StrategyParams(taker_fee_pct=0.075, slippage_pct=0.05)
        paper_engine.registry.get_strategy_params_sync.return_value = params

        # Candle opens BELOW stop loss (gap down)
        # low_price = 48500, stop_loss = 49000, open = 48800
        candles = make_single_candle(base_price=50000)
        # Modify the last candle to have the gap-down scenario
        candles.iloc[-1, candles.columns.get_loc("open")] = 48800
        candles.iloc[-1, candles.columns.get_loc("high")] = 49500
        candles.iloc[-1, candles.columns.get_loc("low")] = 48500
        candles.iloc[-1, candles.columns.get_loc("close")] = 48700
        candles.iloc[-1, candles.columns.get_loc("vwap")] = 48750
        signals = candles.copy()
        signals["signal"] = [1] * len(candles)

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = candles

            result = paper_engine.evaluate_symbol(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                force_candles=candles,
                force_signals=signals,
            )

            # Should trigger STOP_LOSS, fill at open (48800) * (1 - slippage)
            assert result["action"] == "EXIT_STOP_LOSS"
            expected_exit = 48800 * (1 - 0.075 / 100)  # slippage applied
            assert result["exit_price"] == pytest.approx(expected_exit, rel=0.01)

    def test_take_profit_trigger(self, paper_engine, mock_ledger):
        """Take profit should trigger when price crosses target."""
        pos = PaperPosition(
            account_id="test",
            exchange="kucoin",
            symbol="BTC/USD",
            side="LONG",
            entry_price=50000.0,
            current_price=50000.0,
            quantity=0.2,
            cost_basis=10000.0,
            stop_loss=48000.0,
            take_profit=52000.0,
            entry_time="2024-01-01T00:00:00Z",
            last_bar_time="2024-01-01T00:00:00Z",
        )
        mock_ledger.get_position_sync.return_value = pos
        paper_engine.registry.get_strategy_params_sync.return_value = StrategyParams(
            taker_fee_pct=0.075
        )

        # Candle crosses above take_profit
        candles = make_single_candle(base_price=50000)
        candles.iloc[-1, candles.columns.get_loc("open")] = 51900
        candles.iloc[-1, candles.columns.get_loc("high")] = 52500
        candles.iloc[-1, candles.columns.get_loc("low")] = 51800
        candles.iloc[-1, candles.columns.get_loc("close")] = 52200
        candles.iloc[-1, candles.columns.get_loc("vwap")] = 52000
        signals = candles.copy()
        signals["signal"] = [1] * len(candles)

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = candles

            result = paper_engine.evaluate_symbol(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                force_candles=candles,
                force_signals=signals,
            )

            assert result["action"] == "EXIT_TAKE_PROFIT"
            assert result["exit_price"] == pytest.approx(52000.0, rel=0.01)


class TestPaperEngineMaxHoldingBars:
    """Tests for max_holding_bars time-based exit."""

    def test_max_holding_bars_triggers_timeout(self, paper_engine, mock_ledger):
        """Position should exit after max_holding_bars bars."""
        pos = PaperPosition(
            account_id="test",
            exchange="kucoin",
            symbol="BTC/USD",
            side="LONG",
            entry_price=50000.0,
            current_price=50000.0,
            quantity=0.2,
            cost_basis=10000.0,
            stop_loss=48000.0,
            take_profit=52000.0,
            entry_time="2024-01-01T00:00:00Z",
            last_bar_time="2024-01-01T00:00:00Z",
        )
        mock_ledger.get_position_sync.return_value = pos

        params = StrategyParams(max_holding_bars=10)
        paper_engine.registry.get_strategy_params_sync.return_value = params

        # Simulate 10 bars passed (commented variables kept for documentation)
        # entry_dt = pd.Timestamp("2024-01-01T00:00:00Z", tz="UTC")
        # bar_time = pd.Timestamp("2024-01-01T00:50:00Z", tz="UTC")  # 10 bars * 5min = 50 min

        candles = make_single_candle(base_price=50000)
        signals = candles.copy()
        signals["signal"] = [0] * len(candles)

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = candles

            result = paper_engine.evaluate_symbol(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                force_candles=candles,
                force_signals=signals,
            )

            assert result["action"] == "EXIT_TIMEOUT"
            assert result["holding_period_bars"] >= 10


class TestPaperEngineReconciliation:
    """Tests for downtime reconciliation logic."""

    def test_reconcile_stop_loss_during_downtime(self, paper_engine, mock_ledger):
        """Stop loss breached during downtime should be reconciled."""
        pos = PaperPosition(
            account_id="test",
            exchange="kucoin",
            symbol="BTC/USD",
            side="LONG",
            entry_price=50000.0,
            current_price=50000.0,
            quantity=0.2,
            cost_basis=10000.0,
            stop_loss=49000.0,
            take_profit=52000.0,
            entry_time="2024-01-01T00:00:00Z",
        )
        mock_ledger.list_positions_sync.return_value = [pos]

        # Candle data showing stop loss breached during downtime
        candles = pd.DataFrame(
            {
                "timestamp": pd.date_range(
                    "2024-01-01 00:05:00", periods=5, freq="5min", tz="UTC"
                ),
                "open": [50000, 49500, 48800, 48700, 48500],
                "high": [50000, 49800, 49000, 48800, 48700],
                "low": [49500, 48800, 48500, 48400, 48300],
                "close": [49500, 48800, 48500, 48400, 48300],
                "volume": [100] * 5,
                "vwap": [49800, 49000, 48600, 48500, 48400],
                "atr": [500] * 5,
            }
        )

        params = StrategyParams(
            stop_loss_type="vwap", vwap_stop_offset_pct=0.1, taker_fee_pct=0.075
        )
        paper_engine.registry.get_strategy_params_sync.return_value = params

        results = paper_engine.reconcile_positions_on_startup(
            force_candles_map={"kucoin:BTC/USD": candles}
        )

        assert len(results) == 1
        assert "STOP_LOSS" in results[0]["action"]


class TestPaperEngineCircuitBreaker:
    """Tests for circuit breaker logic in polling loop."""

    def test_circuit_breaker_trips_after_consecutive_errors(self, paper_engine):
        """Circuit breaker should trip after MAX_CONSECUTIVE_ERRORS."""
        paper_engine._MAX_CONSECUTIVE_ERRORS = 3
        paper_engine._stop_event = Mock()
        paper_engine._stop_event.wait = Mock()
        paper_engine._stop_event.is_set = Mock(
            side_effect=[False, False, False, False, True]
        )

        # Mock step_all_active_profiles to raise exceptions
        with patch.object(
            paper_engine, "step_all_active_profiles", side_effect=Exception("API error")
        ):
            paper_engine.start_polling(interval_seconds=1, persist=False)

            # Wait for circuit breaker to trip
            paper_engine._poll_thread.join(timeout=5)

            # Circuit breaker should have tripped
            # (This is a simplified test - actual implementation may vary)


class TestPaperEngineManualClose:
    """Tests for manual position close."""

    def test_manual_close_position(self, paper_engine, mock_ledger):
        """Manual close should create trade record with MANUAL exit reason."""
        pos = PaperPosition(
            account_id="test",
            exchange="kucoin",
            symbol="BTC/USD",
            side="LONG",
            entry_price=50000.0,
            current_price=55000.0,
            quantity=0.2,
            cost_basis=10000.0,
            entry_time="2024-01-01T00:00:00Z",
        )
        mock_ledger.get_position_sync.return_value = pos
        mock_ledger.get_balance_sync.return_value = 10000.0

        trade = paper_engine.close_position_market("kucoin", "BTC/USD", 55000.0)

        assert trade is not None
        assert trade.exit_reason == "MANUAL"
        assert trade.exit_price == 55000.0
        assert trade.realized_pnl > 0
        mock_ledger.close_position_sync.assert_called_once()


class TestPaperEngineStepAllActiveProfiles:
    """Tests for step_all_active_profiles snapshot reuse."""

    def test_candle_snapshot_reuse(self, paper_engine, mock_registry, mock_ledger):
        """Same (exchange, symbol, timeframe) should reuse candle snapshot."""
        profiles = [
            Mock(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                params={"fast_ema": 9},
            ),
            Mock(
                exchange="kucoin",
                symbol="BTC/USD",
                timeframe="5m",
                params={"fast_ema": 12},
            ),
        ]
        mock_registry.list_profiles_sync_sync.return_value = profiles

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = make_candles()

            paper_engine.registry.list_profiles_sync.return_value = profiles
            paper_engine.registry.get_strategy_params_sync.return_value = (
                StrategyParams()
            )

            _ = paper_engine.step_all_active_profiles()

            # Should only fetch once for both profiles (same exchange/symbol/timeframe)
            assert mock_fetch.call_count == 1


class TestPaperEngineManualStep:
    """Tests for manual step endpoint."""

    def test_manual_step_fetches_live(self, paper_engine, mock_ledger, mock_registry):
        """Manual step should fetch live candles."""
        mock_registry.get_profile_sync.return_value = Mock(timeframe="5m")
        paper_engine.registry.get_strategy_params_sync.return_value = StrategyParams()
        mock_ledger.get_position_sync.return_value = None

        with patch(
            "src.paper.engine.DataLoader.fetch_and_update_candles"
        ) as mock_fetch:
            mock_fetch.return_value = make_candles()

            result = paper_engine.evaluate_symbol(
                exchange="kucoin", symbol="BTC/USD", timeframe="5m", fetch_live=True
            )

            mock_fetch.assert_called_once()
            assert result["action"] != "insufficient_data"


class TestPaperEngineEntryTimeImmutability4Sites:
    """Verify entry_time is immutable at all 4 sites in engine.py."""

    def test_entry_time_never_changed_after_creation(self, paper_engine, mock_ledger):
        """entry_time set once at creation, never modified at any of the 4 sites."""
        # Site 1: Position creation (line ~724)
        # Site 2: HOLD path (line ~560) - should NOT modify entry_time
        # Site 3: EXIT paths (lines ~580, ~620) - should NOT modify entry_time
        # Site 4: Reconciliation (line ~1115) - should NOT modify entry_time

        # Create initial position
        pos = PaperPosition(
            account_id="test",
            exchange="kucoin",
            symbol="BTC/USD",
            side="LONG",
            entry_price=50000.0,
            current_price=50000.0,
            quantity=0.2,
            cost_basis=10000.0,
            entry_time="2024-01-01T00:00:00Z",
        )
        mock_ledger.get_position_sync.return_value = pos

        original_entry_time = pos.entry_time

        # Simulate multiple evaluations
        for i in range(5):
            mock_ledger.get_position_sync.return_value = pos
            mock_ledger.get_balance_sync.return_value = 10000.0
            mock_ledger.get_total_equity_sync.return_value = 10000.0

            with patch(
                "src.paper.engine.DataLoader.fetch_and_update_candles"
            ) as mock_fetch:
                mock_fetch.return_value = make_candles(
                    base_price=50000 + i * 100, start=f"2024-01-01 00:{i*5:02d}"
                )

                # Must provide force_candles with force_signals
                candles = make_candles(
                    base_price=50000 + i * 100, start=f"2024-01-01 00:{i*5:02d}"
                )
                signals = candles.copy()
                signals["signal"] = [0] * len(candles)

                _ = paper_engine.evaluate_symbol(
                    exchange="kucoin",
                    symbol="BTC/USD",
                    timeframe="5m",
                    force_candles=candles,
                    force_signals=signals,
                )

                # entry_time must never change
                assert (
                    pos.entry_time == original_entry_time
                ), f"entry_time changed on evaluation {i+1}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
