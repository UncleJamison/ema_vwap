# Phase 1 Architecture Outline — EMA + VWAP Crypto Trading Suite

## Project Directory Structure

```
c:\crypto\ema_vwap\
├── docs/
│   └── PHASE-1-ARCHITECTURE-OUTLINE.md
├── src/
│   ├── __init__.py
│   ├── config.py              # Configuration dataclasses & strategy parameters
│   ├── data_loader.py         # Gemini API, KuCoin, CCXT & Synthetic candles data fetcher
│   ├── indicators.py          # Vectorized EMA, VWAP, VWAP slope, ATR, relative volume
│   ├── strategy.py            # Strategy signal generator (Crossover, Multi-EMA, Pullback)
│   ├── risk_manager.py        # Position sizing, stop-loss calculations (VWAP/ATR), R:R
│   ├── backtester.py          # Vectorized & Event-driven backtester & metrics calculator
│   ├── app.py                 # FastAPI backend server & API endpoints
│   └── web/
│       ├── index.html         # Interactive web app UI
│       ├── style.css          # Glassmorphism dark mode stylesheet
│       └── app.js             # Plotly chart renderer & UI event logic
└── tests/
    ├── __init__.py
    ├── test_data_loader.py
    ├── test_indicators.py
    ├── test_strategy.py
    ├── test_risk_manager.py
    └── test_backtester.py
```

## Module Specifications

### 1. `config.py`
- `StrategyParams`: Dataclass holding EMA lengths (9, 21, 50, 55), VWAP slope threshold, volume multiplier, stop loss mode ('vwap', 'atr'), ATR multiplier, risk %, initial capital, R:R ratio.
- `ExchangeConfig`: Exchange settings for Gemini, KuCoin, and fallback modes.

### 2. `data_loader.py`
- `DataLoader`: Class to handle fetching candles from Gemini REST API (`https://api.gemini.com/v2/candles/{symbol}/{timeframe}`), KuCoin REST API, or generating realistic synthetic OHLCV dataframe (with datetime index, open, high, low, close, volume).

### 3. `indicators.py`
- `compute_ema(df, period)`: Calculates exponential moving average.
- `compute_vwap(df, anchor='D')`: Computes Volume Weighted Average Price using cumulative price-volume product divided by cumulative volume.
- `compute_vwap_slope(df, lookback=5)`: Calculates normalized VWAP slope angle / rate of change over $N$ bars.
- `compute_atr(df, period=14)`: Computes Average True Range.
- `compute_relative_volume(df, period=20)`: Calculates relative volume multiplier ($V / \text{SMA}(V, 20)$).
- `compute_all_indicators(df, params)`: Combines all calculation functions onto the DataFrame.

### 4. `strategy.py`
- `EmaVwapStrategy`: Strategy class evaluating signal triggers.
  - `generate_crossover_signals(df)`: 9 EMA crossing VWAP.
  - `generate_multi_ema_signals(df)`: 8/21 EMA crossover with 50/55 EMA trend confirmation.
  - `generate_pullback_signals(df)`: VWAP pullback detection with momentum entry trigger.
  - Applies VWAP slope & Volume confirmation gates.

### 5. `risk_manager.py`
- `PositionSizer`: Calculates trade lot size given account balance and risk percentage.
- `StopLossCalculator`: Computes VWAP stop level or ATR dynamic trailing stop.

### 6. `backtester.py`
- `BacktestEngine`: Runs backtest loop or vectorized simulation on historical candles.
- Returns `BacktestResult` containing net profit, win rate, profit factor, max drawdown %, Sharpe ratio, equity curve, and list of executed trades.

### 7. `app.py` & `web/`
- FastAPI server serving endpoints (`/api/data`, `/api/backtest`, `/api/indicators`) and hosting the interactive Plotly frontend UI.
