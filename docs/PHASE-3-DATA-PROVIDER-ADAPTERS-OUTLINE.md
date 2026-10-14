# Phase 3 Architecture Outline — Abstract Data Provider Adapter Architecture & Timezone Pipeline

## Overview

Refactor the data ingestion engine in `src/data_loader.py` into an extensible, modular `BaseDataProvider` adapter system under `src/providers/`. This decouples exchange-specific REST API calls, symbol formatting, and rate limiting from caching, resampling, and consuming modules. It also establishes a normalized UTC and timezone conversion pipeline required for US stock market hours (RTH, Pre-market, After-hours) and VWAP session resets.

---

## Directory Structure

```
src/
├── providers/
│   ├── __init__.py            # Exports BaseDataProvider, registry, adapters, and tz helpers
│   ├── base.py                # BaseDataProvider abstract class & standard candle schema validator
│   ├── timezone.py            # UTC ISO-8601 normalization & US/Eastern market session helpers
│   ├── crypto.py              # GeminiAdapter & KuCoinAdapter concrete implementations
│   ├── synthetic.py           # SyntheticAdapter with multi-asset price simulation
│   └── registry.py            # Dynamic ProviderRegistry for provider lookup & discovery
├── data_loader.py             # High-level DataLoader orchestrator dispatching to providers
└── database.py                # SQLite candle caching layer
```

---

## Component Specifications

### 1. `src/providers/base.py` (`BaseDataProvider`)
- **Abstract Base Class**: Defines the standardized interface for all data providers.
- **Attributes**:
  - `name: str` (e.g., `"gemini"`, `"kucoin"`, `"synthetic"`, `"alpaca"`)
  - `asset_type: str` (`"crypto"`, `"stock"`, `"synthetic"`)
  - `native_timeframes: Set[str]`
- **Abstract Methods**:
  - `fetch_historical_candles(symbol: str, timeframe: str, days: int = 180, limit: Optional[int] = None) -> pd.DataFrame`
  - `format_symbol(symbol: str) -> str`
- **Concrete Utilities**:
  - `get_supported_timeframes() -> Set[str]`
  - `get_donor_timeframe(target_tf: str) -> Optional[str]`
  - `normalize_symbol(symbol: str) -> str` (Canonical `BASE/QUOTE` format)
  - `validate_and_normalize_df(df: pd.DataFrame) -> pd.DataFrame` (Standard OHLCV columns, UTC timestamps, duplicates removal, numeric casting)

---

### 2. `src/providers/timezone.py` (Timezone & Session Pipeline)
- `to_utc_series(series: pd.Series) -> pd.Series`: Parse any datetime/unix/ISO representation into timezone-naive or timezone-aware standard UTC.
- `utc_to_market_tz(series_or_df, tz: str = "America/New_York")`: Convert timestamps to target market timezone.
- `is_market_hours(timestamp, market_tz: str = "America/New_York", session_type: str = "rth") -> bool`:
  - `rth`: Regular Trading Hours (09:30 - 16:00 ET, Mon-Fri)
  - `pre`: Pre-market (04:00 - 09:30 ET)
  - `post`: After-hours (16:00 - 20:00 ET)
  - `extended`: Pre + RTH + Post (04:00 - 20:00 ET)
- `get_session_anchor_utc(timestamp, market_tz: str = "America/New_York") -> pd.Timestamp`: Returns the session start in UTC (09:30 ET for US stocks, 00:00 UTC for crypto).

---

### 3. `src/providers/crypto.py` (`GeminiAdapter`, `KuCoinAdapter`)
- **`GeminiAdapter`**:
  - Native TFs: `1m, 5m, 15m, 30m, 1h, 6h, 1d`
  - Endpoint: `https://api.gemini.com/v2/candles/{symbol}/{timeframe}`
  - Symbol formatting: `btcusd`, `ethusd`
- **`KuCoinAdapter`**:
  - Native TFs: `1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d`
  - Endpoint: `https://api.kucoin.com/api/v1/market/candles`
  - Symbol formatting: `BTC-USDT`
  - Paginated fetching (1500 candles per chunk) backwards from current timestamp.

---

### 4. `src/providers/synthetic.py` (`SyntheticAdapter`)
- Configurable random-walk geometric Brownian motion candle generator.
- Multi-asset seed prices (BTC, ETH, SOL, DOGE, BONK, PEPE, SHIB, FLOKI, ZEC).
- Supports all standard timeframes.

---

### 5. `src/providers/registry.py` (`ProviderRegistry`)
- Dynamic registration and provider lookup by exchange name.
- Default registry auto-populates `gemini`, `kucoin`, `synthetic`.
- Easy extension for future stock providers (`alpaca`, `polygon`, `tiingo`).

---

### 6. `src/data_loader.py` (`DataLoader`)
- Dispatches to `ProviderRegistry`.
- Coordinates SQLite database caching and multi-timeframe resampling (`resample_and_upsert`).
- Maintains 100% backward-compatible API signatures for `DataLoader.load_candles`, `DataLoader.format_gemini_symbol`, `DataLoader.format_kucoin_symbol`, `DataLoader.generate_synthetic_candles`, etc.
