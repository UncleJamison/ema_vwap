#!/usr/bin/env python
"""Diagnostic: VWAP slope distribution across timeframes.
Verifies whether the per-timeframe bounds in VWAP_SLOPE_SEARCH_BOUNDS
match the actual slope magnitude distribution computed by compute_vwap_slope().
"""
import numpy as np

from src.config import VWAP_SLOPE_SEARCH_BOUNDS, VWAP_SLOPE_SEARCH_BOUNDS_DEFAULTS
from src.data_loader import DataLoader
from src.indicators import compute_vwap, compute_vwap_slope

print("VWAP Slope Distribution by Timeframe (30 days, BTC/USD)")
print("=" * 70)

for tf in ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"]:
    df = DataLoader.load_candles("kucoin", "BTC/USD", tf, days=30)
    if df.empty:
        print(f"{tf}: No data")
        continue
    df["vwap"] = compute_vwap(df, anchor="D", asset_type="crypto")
    slope = compute_vwap_slope(df["vwap"], lookback=5).abs()
    # Remove extreme outliers
    slope = slope[(slope > 0) & (slope < 0.1)]
    print(
        f"{tf:>3}: px={len(df):>6}  "
        f"p50={np.percentile(slope, 50):.6f}  "
        f"p75={np.percentile(slope, 75):.6f}  "
        f"p90={np.percentile(slope, 90):.6f}  "
        f"p95={np.percentile(slope, 95):.6f}  "
        f"max={slope.max():.6f}"
    )

print("\n" + "=" * 70)
print("Current VWAP_SLOPE_SEARCH_BOUNDS (min, max) per TF:")
for tf, bounds in VWAP_SLOPE_SEARCH_BOUNDS.items():
    print(f"{tf:>3}: {bounds[0]:.6f} - {bounds[1]:.6f}")
print(
    f"fallback: {VWAP_SLOPE_SEARCH_BOUNDS_DEFAULTS[0]:.6f} - {VWAP_SLOPE_SEARCH_BOUNDS_DEFAULTS[1]:.6f}"
)
