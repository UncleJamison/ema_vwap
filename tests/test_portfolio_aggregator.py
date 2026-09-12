"""
Unit and integration tests for Multi-Exchange & Broker Unified Balance Aggregator.
"""

from src.database import CandleDatabase
from src.portfolio.aggregator import PortfolioAggregator
from src.portfolio.connectors import (
    AlpacaBalanceConnector,
    GeminiBalanceConnector,
    KuCoinBalanceConnector,
    SyntheticBalanceConnector,
    categorize_currency,
)
from src.settings import SettingsManager


def test_currency_categorization():
    """Verify currency string categorization."""
    assert categorize_currency("USD") == "fiat"
    assert categorize_currency("EUR") == "fiat"
    assert categorize_currency("USDT") == "stablecoin"
    assert categorize_currency("USDC") == "stablecoin"
    assert categorize_currency("AAPL") == "stock"
    assert categorize_currency("SPY") == "stock"
    assert categorize_currency("BTC") == "crypto"
    assert categorize_currency("ETH") == "crypto"
    assert categorize_currency("SOL") == "crypto"


def test_synthetic_wallet_connector():
    """Verify synthetic multi-asset paper wallet returns balances and positions."""
    connector = SyntheticBalanceConnector()
    venue_bal = connector.fetch_balances()

    assert venue_bal.venue == "synthetic"
    assert venue_bal.is_connected is True
    assert venue_bal.total_nav_usd > 50000.0
    assert venue_bal.cash_usd == 25000.0
    assert venue_bal.stablecoin_usd == 15000.0
    assert venue_bal.crypto_usd > 0.0
    assert venue_bal.stock_usd > 0.0
    assert len(venue_bal.positions) == 4  # BTC, ETH, AAPL, SPY


def test_portfolio_aggregator_snapshot_fallback():
    """Verify PortfolioAggregator compiles a complete snapshot with fallback to synthetic wallet."""
    aggregator = PortfolioAggregator()
    snapshot = aggregator.get_unified_snapshot()

    assert snapshot.total_nav_usd > 0.0
    assert snapshot.crypto_weight_pct > 0.0
    assert snapshot.stock_weight_pct > 0.0
    assert snapshot.cash_weight_pct > 0.0
    assert (
        round(
            snapshot.crypto_weight_pct
            + snapshot.stock_weight_pct
            + snapshot.cash_weight_pct,
            1,
        )
        == 100.0
    )
    assert len(snapshot.positions) > 0


def test_venue_connection_unconfigured_behavior(tmp_path):
    """Verify real connectors gracefully report missing credentials without crashing."""
    db = CandleDatabase(db_path=str(tmp_path / "empty_settings.db"))
    settings = SettingsManager(db=db)

    gemini = GeminiBalanceConnector(settings)
    g_bal = gemini.fetch_balances()
    assert g_bal.is_connected is False
    assert "not configured" in (g_bal.error_message or "").lower()

    kucoin = KuCoinBalanceConnector(settings)
    k_bal = kucoin.fetch_balances()
    assert k_bal.is_connected is False
    assert "not configured" in (k_bal.error_message or "").lower()

    alpaca = AlpacaBalanceConnector(settings)
    a_bal = alpaca.fetch_balances()
    assert a_bal.is_connected is False
    assert "not configured" in (a_bal.error_message or "").lower()


def test_portfolio_endpoints_with_test_client():
    """Verify FastAPI portfolio REST endpoints."""
    from fastapi.testclient import TestClient

    from src.app import app

    client = TestClient(app)

    # 1. Snapshot endpoint
    resp = client.get("/api/portfolio/snapshot?include_synthetic=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "snapshot" in data
    assert data["snapshot"]["total_nav_usd"] > 0

    # 2. Venues endpoint
    resp_v = client.get("/api/portfolio/venues?include_synthetic=true")
    assert resp_v.status_code == 200
    venues_data = resp_v.json()
    assert "venues" in venues_data

    # 3. Positions endpoint
    resp_p = client.get("/api/portfolio/positions?include_synthetic=true")
    assert resp_p.status_code == 200
    pos_data = resp_p.json()
    assert "positions" in pos_data
    assert len(pos_data["positions"]) > 0

    # 4. Refresh endpoint
    resp_r = client.post("/api/portfolio/refresh?include_synthetic=true")
    assert resp_r.status_code == 200
    assert resp_r.json()["status"] == "success"

    # 5. History endpoint
    resp_h = client.get("/api/portfolio/history?days=10&include_synthetic=true")
    assert resp_h.status_code == 200
    h_data = resp_h.json()
    assert h_data["status"] == "success"
    assert len(h_data["history"]) >= 10
    assert "total_nav" in h_data["history"][0]


def test_portfolio_aggregator_historical_nav_method():
    """Verify PortfolioAggregator.get_historical_nav method."""
    agg = PortfolioAggregator()
    history = agg.get_historical_nav(days=15, include_synthetic=True)
    assert isinstance(history, list)
    assert len(history) == 16  # 0 to 15 days inclusive
    assert history[0]["total_nav"] > 0
    assert "crypto_value" in history[0]
    assert "stock_value" in history[0]
    assert "cash_value" in history[0]
