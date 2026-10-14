# Timeframe-to-Bars Mapping Recommendation

**Purpose:** Recommended `days` parameter values for `load_candles()` to achieve minimum statistical significance of 20-30K bars, for timeframes > 5m.

**Reference:** Existing `tf_per_day_map` in `data_loader.py` (lines 379-390).

---

## Existing `tf_per_day_map` (unchanged)

This map is a **constant** — it correctly maps each timeframe to bars-per-day. It does not need adjustment.

| Timeframe | Bars/Day |
|-----------|----------|
| 1m        | 1,440    |
| 3m        | 480      |
| 5m        | 288      |
| 15m       | 96       |
| 30m       | 48       |
| 1h        | 24       |
| 2h        | 12       |
| 4h        | 6        |
| 6h        | 4        |
| 1d        | 1        |

The `load_candles()` method uses: `req_candles = days * tf_per_day_map[timeframe]`

---

## Recommended Days for 20-30K Bars (Timeframes > 5m)

| Timeframe | Bars/Day | Days (20K target) | Resulting Bars (20K) | Days (30K target) | Resulting Bars (30K) | **Recommended Days** | **Resulting Bars** | Feasibility |
|-----------|----------|-------------------|---------------------|-------------------|---------------------|----------------------|--------------------|-------------|
| 15m       | 96       | 208               | 19,968              | 312               | 29,952              | **280**              | **26,880**         | ✅ Easy (~9 months) |
| 30m       | 48       | 416               | 19,968              | 625               | 30,000              | **500**              | **24,000**         | ✅ Moderate (~1.4 years) |
| 1h        | 24       | 833               | 19,992              | 1,250             | 30,000              | **1,000**            | **24,000**         | ✅ Moderate (~2.7 years) |
| 2h        | 12       | 1,666             | 19,992              | 2,500             | 30,000              | **2,000**            | **24,000**         | ⚠️ Stretch (~5.5 years) |
| 4h        | 6        | 3,333             | 19,998              | 5,000             | 30,000              | **3,300**            | **19,800**         | ⚠️ Hard (~9 years, needs ~2017+ data) |
| 6h        | 4        | 5,000             | 20,000              | 7,500             | 30,000              | **5,000**            | **20,000**         | ⚠️ Hard (~13.7 years, needs ~2013+ data) |
| 1d        | 1        | 20,000            | 20,000              | 30,000            | 30,000              | **7,300**            | **7,300**          | ❌ Impractical (~20 years, only ~5.5K days available) |

---

## Key Findings

### 1. Timeframes ≤ 1h: 20-30K bars is readily achievable
- 15m, 30m, 1h can all hit 24-28K bars with 1-2.7 years of data
- These are the most statistically robust timeframes for validation

### 2. Timeframes 2h-4h: Achievable with 5+ years of data
- 2h at 2000 days (5.5y) → 24K bars: reasonable for major pairs
- 4h at 3300 days (9y) → ~20K bars: feasible for BTC/ETH since ~2017

### 3. Timeframes ≥ 6h: Statistical significance is challenging
- 6h: 20K bars requires ~13.7 years — barely feasible (crypto reliable data ~2011+)
- 1d: 20K bars requires 20,000 days (~55 years) — **impossible** with crypto history
- **Recommendation:** For 6h+, relax to "as much historical data as available" and cap at max feasible bars

### 4. Practical Caps (Recommended Maximum Days)

| Timeframe | Hard Cap Days | Rationale |
|-----------|--------------|-----------|
| 15m       | 365          | ~1 year keeps data fresh; 34,560 bars is strong |
| 30m       | 600          | ~1.6 years; 28,800 bars |
| 1h        | 1,095        | ~3 years; 26,280 bars |
| 2h        | 1,825        | ~5 years; 21,900 bars |
| 4h        | 3,285        | ~9 years; 19,710 bars (near crypto data limits for newer pairs) |
| 6h        | 5,475        | ~15 years; 21,900 bars (all available crypto history) |
| 1d        | **N/A**      | Use `limit` parameter directly; ~5,475 daily bars available max |

---

## Proposed Updated `fetch_and_update_candles` Days Map

The `tf_days_map` in `fetch_and_update_candles()` (line 458-469) currently has conservative values. Recommended updates for consistency with validation targets:

```python
# CURRENT (data_loader.py line 458-469):
tf_days_map = {
    "1m": 1, "3m": 1, "5m": 1,
    "15m": 2, "30m": 4, "1h": 8,
    "2h": 16, "4h": 32, "6h": 48, "1d": 180,
}

# RECOMMENDED (aligned with 20-30K bar validation target):
tf_days_map = {
    "1m": 1, "3m": 1, "5m": 1,       # Keep low; high bar density
    "15m": 280,                       # 26,880 bars (~9 months)
    "30m": 500,                       # 24,000 bars (~1.4 years)
    "1h": 1000,                       # 24,000 bars (~2.7 years)
    "2h": 2000,                       # 24,000 bars (~5.5 years)
    "4h": 3300,                       # 19,800 bars (~9 years)
    "6h": 5000,                       # 20,000 bars (~13.7 years)
    "1d": 7300,                       # 7,300 bars (~20 years, max feasible)
}
```

---

## Validation Engine Integration Note

The `validator.py` `validate()` method accepts a `bars_per_day` parameter (line 162) but does not use it in the current implementation. To enforce the 20-30K bar minimum:

- Add a check in `validate()` that computes `total_bars = days * tf_per_day_map[timeframe]` for the requested timeframe
- If total_bars < 20,000, raise a `ValidationWarning` recommending the adjusted days
- This prevents the WFO engine from running with insufficient data for statistically meaningful results
