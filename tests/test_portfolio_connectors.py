"""
Tests for portfolio connectors (src/portfolio/connectors.py) and models.
Tests cover all exchange/broker connectors, data models, and error paths.
"""

from unittest.mock import Mock, patch

import pytest

from src.portfolio.connectors import (
    AlpacaBalanceConnector,
    AssetPosition,
    Balance,
    GeminiBalanceConnector,
    KuCoinBalanceConnector,
    PaperTradingBalanceConnector,
    RobinhoodCryptoBalanceConnector,
    SyntheticBalanceConnector,
    VenueBalance,
    categorize_currency,
)
from src.portfolio.models import UnifiedPortfolioSnapshot


class TestCategorizeCurrency:
    """Tests for the categorize_currency utility function."""

    def test_fiat_currencies(self):
        for curr in ["USD", "EUR", "GBP", "CAD", "AUD", "JPY"]:
            assert categorize_currency(curr) == "fiat"

    def test_stablecoins(self):
        for curr in ["USDT", "USDC", "BUSD", "DAI", "TUSD", "GUSD", "USDP"]:
            assert categorize_currency(curr) == "stablecoin"

    def test_known_stocks(self):
        for curr in ["AAPL", "SPY", "QQQ", "MSFT", "TSLA", "NVDA", "AMZN", "GOOGL"]:
            assert categorize_currency(curr) == "stock"

    def test_crypto_currencies(self):
        for curr in ["BTC", "ETH", "ZEC", "SOL", "ADA", "DOGE"]:
            assert categorize_currency(curr) == "crypto"

    def test_case_insensitive(self):
        assert categorize_currency("usd") == "fiat"
        assert categorize_currency("btc") == "crypto"
        assert categorize_currency("aapl") == "stock"


class TestBalanceModel:
    """Tests for Balance dataclass."""

    def test_balance_creation(self):
        b = Balance(
            currency="BTC",
            total=1.5,
            free=1.0,
            locked=0.5,
            usd_price=50000.0,
            usd_value=75000.0,
            venue="gemini",
            asset_type="crypto",
        )
        assert b.currency == "BTC"
        assert b.total == 1.5
        assert b.asset_type == "crypto"

    def test_balance_to_dict(self):
        b = Balance(currency="USD", total=1000.0, free=1000.0, venue="alpaca")
        d = b.to_dict()
        assert d["currency"] == "USD"
        assert d["venue"] == "alpaca"


class TestAssetPositionModel:
    """Tests for AssetPosition dataclass."""

    def test_position_creation(self):
        pos = AssetPosition(
            symbol="BTC/USD",
            venue="gemini",
            asset_type="crypto",
            side=1,
            quantity=0.5,
            entry_price=50000.0,
            current_price=55000.0,
            market_value_usd=27500.0,
        )
        assert pos.symbol == "BTC/USD"
        assert pos.side == 1
        assert pos.unrealized_pnl_usd == 0.0  # default

    def test_short_position(self):
        pos = AssetPosition(
            symbol="ETH/USD",
            venue="kucoin",
            asset_type="crypto",
            side=-1,
            quantity=10.0,
            entry_price=3000.0,
            current_price=2800.0,
            market_value_usd=28000.0,
        )
        assert pos.side == -1


class TestVenueBalanceModel:
    """Tests for VenueBalance dataclass."""

    def test_venue_balance_defaults(self):
        vb = VenueBalance(venue="gemini", venue_type="crypto")
        assert vb.venue == "gemini"
        assert vb.total_nav_usd == 0.0
        assert vb.is_connected is False
        assert vb.balances == []
        assert vb.positions == []

    def test_venue_balance_with_data(self):
        vb = VenueBalance(
            venue="kucoin",
            venue_type="crypto",
            total_nav_usd=100000.0,
            is_connected=True,
            balances=[Balance(currency="BTC", total=1.0, free=0.8, venue="kucoin")],
        )
        assert vb.total_nav_usd == 100000.0
        assert vb.is_connected is True
        assert len(vb.balances) == 1


class TestUnifiedPortfolioSnapshot:
    """Tests for UnifiedPortfolioSnapshot dataclass."""

    def test_snapshot_creation(self):
        snap = UnifiedPortfolioSnapshot(
            timestamp="2024-01-01T00:00:00Z",
            total_nav_usd=100000.0,
            total_cash_usd=10000.0,
            total_stablecoin_usd=5000.0,
            total_crypto_usd=50000.0,
            total_stock_usd=35000.0,
            total_buying_power_usd=15000.0,
            total_unrealized_pnl_usd=5000.0,
            crypto_weight_pct=50.0,
            stock_weight_pct=35.0,
            cash_weight_pct=15.0,
        )
        assert snap.total_nav_usd == 100000.0
        assert snap.crypto_weight_pct == 50.0

    def test_to_dict_roundtrip(self):
        vb = VenueBalance(venue="gemini", venue_type="crypto", is_connected=True)
        snap = UnifiedPortfolioSnapshot(
            timestamp="2024-01-01T00:00:00Z",
            total_nav_usd=100000.0,
            total_cash_usd=10000.0,
            total_stablecoin_usd=5000.0,
            total_crypto_usd=50000.0,
            total_stock_usd=35000.0,
            total_buying_power_usd=15000.0,
            total_unrealized_pnl_usd=5000.0,
            crypto_weight_pct=50.0,
            stock_weight_pct=35.0,
            cash_weight_pct=15.0,
            venues={"gemini": vb},
        )
        d = snap.to_dict()
        assert d["total_nav_usd"] == 100000.0
        assert "gemini" in d["venues"]


class TestCategorizeCurrencyIntegration:
    """Integration tests for categorize_currency with Balance."""

    def test_balance_asset_type_assignment(self):
        """Verify Balance.asset_type matches categorize_currency."""
        for curr, expected_type in [
            ("USD", "fiat"),
            ("USDT", "stablecoin"),
            ("BTC", "crypto"),
            ("AAPL", "stock"),
        ]:
            b = Balance(
                currency=curr,
                total=1.0,
                free=1.0,
                usd_price=1.0,
                usd_value=1.0,
                venue="test",
                asset_type=categorize_currency(curr),
            )
            assert b.asset_type == expected_type


class TestGeminiBalanceConnector:
    """Tests for GeminiBalanceConnector."""

    @pytest.fixture
    def mock_settings(self):
        with patch("src.portfolio.connectors.SettingsManager") as mock:
            mgr = Mock()
            mgr.get_gemini_credentials.return_value = {
                "api_key": "test_key",
                "secret_key": "test_secret",
                "is_sandbox": True,
            }
            mock.return_value = mgr
            yield mgr

    def test_get_venue_name(self):
        conn = GeminiBalanceConnector()
        assert conn.get_venue_name() == "gemini"

    def test_fetch_balances_no_credentials(self, mock_settings):
        mock_settings.get_gemini_credentials.return_value = {}
        conn = GeminiBalanceConnector()
        result = conn.fetch_balances()
        assert result.is_connected is False
        assert "credentials not configured" in result.error_message.lower()

    @patch("src.portfolio.connectors.requests.post")
    def test_fetch_balances_success(self, mock_post, mock_settings):
        mock_post.return_value = Mock(
            ok=True,
            json=lambda: [
                {"currency": "BTC", "amount": "1.5", "available": "1.0"},
                {"currency": "USD", "amount": "1000.0", "available": "1000.0"},
            ],
        )
        with patch("src.price_estimates.get_estimated_price", return_value=50000.0):
            conn = GeminiBalanceConnector()
            result = conn.fetch_balances()
            assert result.is_connected is True
            assert result.venue == "gemini"
            assert len(result.balances) == 2
            assert result.total_nav_usd > 0

    @patch("src.portfolio.connectors.requests.post")
    def test_fetch_balances_api_error(self, mock_post, mock_settings):
        mock_post.return_value = Mock(ok=False, status_code=401, text="Unauthorized")
        conn = GeminiBalanceConnector()
        result = conn.fetch_balances()
        assert result.is_connected is False
        assert "error" in result.error_message.lower()


class TestKuCoinBalanceConnector:
    """Tests for KuCoinBalanceConnector."""

    @pytest.fixture
    def mock_settings(self):
        with patch("src.portfolio.connectors.SettingsManager") as mock:
            mgr = Mock()
            mgr.get_kucoin_credentials.return_value = {
                "api_key": "test_key",
                "secret_key": "test_secret",
                "passphrase": "test_passphrase",
                "is_sandbox": True,
            }
            mock.return_value = mgr
            yield mgr

    def test_get_venue_name(self):
        conn = KuCoinBalanceConnector()
        assert conn.get_venue_name() == "kucoin"

    def test_fetch_balances_no_credentials(self, mock_settings):
        mock_settings.get_kucoin_credentials.return_value = {}
        conn = KuCoinBalanceConnector()
        result = conn.fetch_balances()
        assert result.is_connected is False
        assert "credentials not configured" in result.error_message.lower()

    @patch("src.portfolio.connectors.requests.get")
    def test_fetch_balances_success(self, mock_get, mock_settings):
        mock_get.return_value = Mock(
            ok=True,
            json=lambda: {
                "data": [
                    {
                        "currency": "BTC",
                        "balance": "1.0",
                        "available": "0.8",
                        "holds": "0.2",
                    },
                    {
                        "currency": "USDT",
                        "balance": "5000.0",
                        "available": "5000.0",
                        "holds": "0.0",
                    },
                ]
            },
        )
        with patch("src.price_estimates.get_estimated_price", return_value=50000.0):
            conn = KuCoinBalanceConnector()
            result = conn.fetch_balances()
            assert result.is_connected is True
            assert result.venue == "kucoin"
            assert len(result.balances) == 2
            assert result.total_nav_usd > 0

    @patch("src.portfolio.connectors.requests.get")
    def test_fetch_balances_api_error(self, mock_get, mock_settings):
        mock_get.return_value = Mock(
            ok=False, status_code=401, text="Invalid signature"
        )
        conn = KuCoinBalanceConnector()
        result = conn.fetch_balances()
        assert result.is_connected is False
        assert "error" in result.error_message.lower()


class TestAlpacaBalanceConnector:
    """Tests for AlpacaBalanceConnector."""

    @pytest.fixture
    def mock_settings(self):
        with patch("src.portfolio.connectors.SettingsManager") as mock:
            mgr = Mock()
            mgr.get_alpaca_credentials.return_value = {
                "api_key": "test_key",
                "secret_key": "test_secret",
                "is_paper": True,
            }
            mock.return_value = mgr
            yield mgr

    def test_get_venue_name(self):
        conn = AlpacaBalanceConnector()
        assert conn.get_venue_name() == "alpaca"

    def test_fetch_balances_no_credentials(self, mock_settings):
        mock_settings.get_alpaca_credentials.return_value = {}
        conn = AlpacaBalanceConnector()
        result = conn.fetch_balances()
        assert result.is_connected is False
        assert "credentials not configured" in result.error_message.lower()

    @patch("src.portfolio.connectors.requests.get")
    def test_fetch_balances_success(self, mock_get, mock_settings):
        # Mock account response
        account_resp = Mock(ok=True)
        account_resp.json.return_value = {
            "equity": "100000.0",
            "cash": "20000.0",
            "buying_power": "40000.0",
            "long_market_value": "80000.0",
        }

        # Mock positions response
        positions_resp = Mock(ok=True)
        positions_resp.json.return_value = [
            {
                "symbol": "AAPL",
                "qty": "100",
                "avg_entry_price": "150.0",
                "current_price": "155.0",
                "market_value": "15500.0",
                "unrealized_pl": "500.0",
                "unrealized_plpc": "0.0333",
            },
            {
                "symbol": "SPY",
                "qty": "50",
                "avg_entry_price": "400.0",
                "current_price": "410.0",
                "market_value": "20500.0",
                "unrealized_pl": "500.0",
                "unrealized_plpc": "0.025",
            },
        ]

        mock_get.side_effect = [account_resp, positions_resp]

        conn = AlpacaBalanceConnector()
        result = conn.fetch_balances()

        assert result.is_connected is True
        assert result.venue == "alpaca"
        assert result.venue_type == "stock"
        assert result.total_nav_usd == 100000.0
        assert result.cash_usd == 20000.0
        assert result.buying_power_usd == 40000.0
        assert result.stock_usd == 80000.0
        assert len(result.positions) == 2
        assert result.positions[0].symbol == "AAPL"
        assert result.positions[0].quantity == 100.0
        assert result.positions[0].unrealized_pnl_usd == 500.0

    @patch("src.portfolio.connectors.requests.get")
    def test_fetch_balances_account_error(self, mock_get, mock_settings):
        mock_get.return_value = Mock(ok=False, status_code=401, text="Unauthorized")
        conn = AlpacaBalanceConnector()
        result = conn.fetch_balances()
        assert result.is_connected is False
        assert "error" in result.error_message.lower()


class TestRobinhoodCryptoBalanceConnector:
    """Tests for RobinhoodCryptoBalanceConnector."""

    @pytest.fixture
    def mock_settings(self):
        with patch("src.portfolio.connectors.SettingsManager") as mock:
            mgr = Mock()
            mgr.get_robinhood_credentials.return_value = {
                "api_key": "test_key",
                "private_key": "test_private_key",
            }
            mock.return_value = mgr
            yield mgr

    def test_get_venue_name(self):
        conn = RobinhoodCryptoBalanceConnector()
        assert conn.get_venue_name() == "robinhood"

    def test_fetch_balances_no_credentials(self, mock_settings):
        mock_settings.get_robinhood_credentials.return_value = {}
        conn = RobinhoodCryptoBalanceConnector()
        result = conn.fetch_balances()
        assert result.is_connected is False
        assert "credentials not configured" in result.error_message.lower()

    def test_fetch_balances_returns_stub(self, mock_settings):
        conn = RobinhoodCryptoBalanceConnector()
        result = conn.fetch_balances()
        # Robinhood returns a stub with is_connected=True but empty data
        assert result.venue == "robinhood"
        assert result.is_connected is True
        assert result.total_nav_usd == 0.0


class TestSyntheticBalanceConnector:
    """Tests for SyntheticBalanceConnector."""

    def test_get_venue_name(self):
        conn = SyntheticBalanceConnector()
        assert conn.get_venue_name() == "synthetic"

    def test_fetch_balances_returns_simulated_data(self):
        conn = SyntheticBalanceConnector()
        result = conn.fetch_balances()

        assert result.is_connected is True
        assert result.venue == "synthetic"
        assert result.venue_type == "synthetic"
        assert result.total_nav_usd > 0
        assert result.cash_usd == 25000.0
        assert result.stablecoin_usd == 15000.0
        assert result.crypto_usd > 0
        assert result.stock_usd > 0
        assert len(result.balances) == 6  # USD, USDT, BTC, ETH, AAPL, SPY
        assert len(result.positions) == 4  # BTC, ETH, AAPL, SPY
        assert result.buying_power_usd == 40000.0  # cash + stablecoin

        # Check positions have correct structure
        for pos in result.positions:
            assert pos.venue == "synthetic"
            assert pos.asset_type in ("crypto", "stock")
            assert pos.market_value_usd > 0


class TestPaperTradingBalanceConnector:
    """Tests for PaperTradingBalanceConnector."""

    def test_get_venue_name(self):
        conn = PaperTradingBalanceConnector()
        assert conn.get_venue_name() == "paper"

    @patch("src.portfolio.connectors.PaperTradingEngine")
    def test_fetch_balances_with_positions(self, mock_engine_class):
        mock_engine = Mock()
        mock_engine.get_balance_snapshot.return_value = {
            "cash_usd": 5000.0,
            "stablecoin_usd": 0.0,
            "crypto_usd": 10000.0,
            "stock_usd": 0.0,
            "total_nav_usd": 15000.0,
            "buying_power_usd": 5000.0,
            "positions": [
                {
                    "symbol": "BTC/USD",
                    "venue": "kucoin",
                    "side": "LONG",
                    "quantity": 0.5,
                    "entry_price": 50000.0,
                    "current_price": 55000.0,
                    "unrealized_pnl": 2500.0,
                    "unrealized_pnl_pct": 10.0,
                },
            ],
        }
        mock_engine_class.return_value = mock_engine

        conn = PaperTradingBalanceConnector()
        result = conn.fetch_balances()

        assert result.is_connected is True  # has positions
        assert result.venue == "paper"
        assert result.cash_usd == 5000.0
        assert result.crypto_usd == 10000.0
        assert result.total_nav_usd == 15000.0
        assert len(result.positions) == 1

    @patch("src.portfolio.connectors.PaperTradingEngine")
    def test_fetch_balances_no_positions(self, mock_engine_class):
        mock_engine = Mock()
        mock_engine.get_balance_snapshot.return_value = {
            "cash_usd": 10000.0,
            "stablecoin_usd": 0.0,
            "crypto_usd": 0.0,
            "stock_usd": 0.0,
            "total_nav_usd": 10000.0,
            "buying_power_usd": 10000.0,
            "positions": [],
        }
        mock_engine_class.return_value = mock_engine

        conn = PaperTradingBalanceConnector()
        result = conn.fetch_balances()

        assert result.is_connected is False  # no positions
        assert result.cash_usd == 10000.0


class TestPriceEstimates:
    """Tests for price_estimates module."""

    def test_known_currencies(self):
        from src.price_estimates import get_estimated_price

        assert get_estimated_price("BTC") == 65000.0
        assert get_estimated_price("ETH") == 3500.0
        assert get_estimated_price("ZEC") == 225.0
        assert get_estimated_price("SOL") == 550.0

    def test_unknown_currency_fallback(self):
        from src.price_estimates import get_estimated_price

        assert get_estimated_price("UNKNOWN") == 100.0


class TestConnectorErrorHandling:
    """Tests for error handling across all connectors."""

    def test_all_connectors_handle_missing_credentials(self):
        connectors = [
            GeminiBalanceConnector,
            KuCoinBalanceConnector,
            AlpacaBalanceConnector,
            RobinhoodCryptoBalanceConnector,
        ]
        for conn_class in connectors:
            with patch("src.portfolio.connectors.SettingsManager") as mock:
                mgr = Mock()
                # Return empty credentials for all
                mgr.get_gemini_credentials.return_value = {}
                mgr.get_kucoin_credentials.return_value = {}
                mgr.get_alpaca_credentials.return_value = {}
                mgr.get_robinhood_credentials.return_value = {}
                mock.return_value = mgr

                conn = conn_class()
                result = conn.fetch_balances()
                assert result.is_connected is False
                assert result.error_message is not None
                assert "credentials" in result.error_message.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
