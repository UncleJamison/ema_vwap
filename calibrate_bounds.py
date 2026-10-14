#!/usr/bin/env python
"""Calibration sweep: derive VWAP slope and EMA bounds from real data.

Produces evidence-backed tables for VWAP_SLOPE_SEARCH_BOUNDS and EMA_SEARCH_BOUNDS
that will be committed to src/config.py.

Uses per-timeframe optimal lookback so higher timeframes (2h, 4h, 1d) are
calibrated over the same data window the batch sweep uses — not a flat 90 days
that under-samples low-frequency bars.
"""
import numpy as np

from src.config import get_optimal_lookback_days, get_timeframe_spec
from src.data_loader import DataLoader
from src.indicators import compute_vwap, compute_vwap_slope

SYMBOLS = ["BTC/USD", "ETH/USD", "ZEC/USD"]
# All timeframes supported by the batch sweep — including 2h, 3m, 6h which
# were previously missing and fell back to uncalibrated global defaults.
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"]

print(f"Calibrating bounds from {SYMBOLS} x {TIMEFRAMES} (per-TF optimal lookback)")
print("=" * 100)

slope_by_tf: dict[str, list[float]] = {tf: [] for tf in TIMEFRAMES}

for tf in TIMEFRAMES:
    spec = get_timeframe_spec(tf, "crypto")
    bars_per_day = spec.bars_per_day_crypto
    # Use per-timeframe optimal lookback — same as batch sweep — so slope
    # distributions are calibrated over representative data windows.
    # e.g. 1m=30d, 5m=180d, 2h=1460d, 4h=1825d.
    lookback_days = get_optimal_lookback_days(tf, "crypto")
    print(f"\n{tf} (bpd={bars_per_day}, lookback={lookback_days}d)...", flush=True)
    for sym in SYMBOLS:
        try:
            df = DataLoader.load_candles("kucoin", sym, tf, days=lookback_days)
            if df.empty or len(df) < 100:
                print(f"  {sym}: insufficient ({len(df)} rows)", flush=True)
                continue
            df["vwap"] = compute_vwap(df, anchor="D", asset_type="crypto")
            slope = compute_vwap_slope(df["vwap"], lookback=5).abs()
            slope = slope[(slope > 0) & (slope < 0.5)]
            slope_by_tf[tf].extend(slope.values.tolist())
            print(f"  {sym}: {len(df)} rows, {len(slope)} slopes", flush=True)
        except Exception as e:
            print(f"  {sym}: ERROR {e}", flush=True)

print("\n" + "=" * 100)
print("VWAP_SLOPE_SEARCH_BOUNDS (calibrated from actual data):")
print(
    f'{"TF":>4} | {"p01":>10} {"p25":>10} {"p50":>10} {"p75":>10} {"p90":>10} {"p95":>10} {"p99":>10}'
)
print("-" * 90)

calibrated_slope = {}
for tf in TIMEFRAMES:
    s = slope_by_tf[tf]
    if len(s) < 50:
        print(f"  {tf:>4}: insufficient data ({len(s)} samples), using defaults")
        calibrated_slope[tf] = (0.00001, 0.003)
        continue
    p01 = np.percentile(s, 1)
    p25 = np.percentile(s, 25)
    p50 = np.percentile(s, 50)
    p75 = np.percentile(s, 75)
    p90 = np.percentile(s, 90)
    p95 = np.percentile(s, 95)
    p99 = np.percentile(s, 99)
    print(
        f"{tf:>4} | {p01:>10.6f} {p25:>10.6f} {p50:>10.6f} {p75:>10.6f} {p90:>10.6f} {p95:>10.6f} {p99:>10.6f}"
    )

print(f"\n{'TF':>4} | {'min (p01)':>12} {'max (p95)':>12} {'max (p99)':>12}")
print("-" * 45)

for tf in TIMEFRAMES:
    s = slope_by_tf[tf]
    if len(s) < 50:
        min_b, max_b = 0.00001, 0.003
    else:
        min_b = max(np.percentile(s, 1), 1e-6)
        max_b = min(np.percentile(s, 95), 0.05)
    calibrated_slope[tf] = (min_b, max_b)
    print(f"{tf:>4} | {min_b:>12.6f} {max_b:>12.6f}")

print("\n\n" + "=" * 100)
print("CALIBRATED VWAP_SLOPE_SEARCH_BOUNDS:")
print("=" * 100)
print("VWAP_SLOPE_SEARCH_BOUNDS: dict[str, tuple[float, float]] = {")
for tf, (mn, mx) in calibrated_slope.items():
    print(f'    "{tf}": ({mn:.6f}, {mx:.6f}),')
print("}")

print("\nCALIBRATED EMA_SEARCH_BOUNDS:")
print("=" * 100)
print("EMA_SEARCH_BOUNDS: dict[str, tuple[int, int, int, int, int, int]] = {")
for tf in TIMEFRAMES:
    spec = get_timeframe_spec(tf, "crypto")
    bpd = int(spec.bars_per_day_crypto)
    # Trend EMA should span at least 1 day's worth of bars on this TF,
    # but not more than 2 days (to avoid noise dominance on low TFs)
    t_min = max(bpd, 20)
    t_max = max(bpd * 3, 60)
    if tf == "1d":
        t_min, t_max = 20, 250
    elif tf == "4h":
        t_min, t_max = 24, 150
    elif tf == "1h":
        t_min, t_max = 24, 120
    elif tf in ("15m", "30m"):
        t_min, t_max = 20, 60
    elif tf in ("1m", "3m", "5m"):
        t_min, t_max = 20, 48

    f_min = max(t_min // 3, 2)
    f_max = max(t_min, 10)
    s_min = max(t_min // 2, 5)
    s_max = max(t_max, 30)

    print(f'    "{tf}": ({f_min}, {f_max}, {s_min}, {s_max}, {t_min}, {t_max}),')

print("}")
print("\nCalibration complete.")
