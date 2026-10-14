"""
gemini_ranker.py — Standalone research script for ranking Gemini USD pairs.

Evaluates all Gemini USD trading pairs using a multi-factor composite score
optimised for swing-trading or scalping coin selection.  Results are printed
to the terminal and optionally exported to JSON for further analysis.

Usage:
    python gemini_ranker.py                       # default: swing profile, top 10
    python gemini_ranker.py --profile scalp        # scalping-optimised weights
    python gemini_ranker.py --top 5                # show top 5 only
    python gemini_ranker.py --export               # save results to JSON
    python gemini_ranker.py --profile scalp --top 5 --export
"""

import argparse
import json
import os
import statistics
import sys
import time
from collections import defaultdict

import requests

# Fix Windows cp1252 encoding crash with emoji characters
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GEMINI_BASE = "https://api.gemini.com/v1"
GEMINI_BASE_V2 = "https://api.gemini.com/v2"

# --- Scoring weight profiles ---
PROFILES = {
    "swing": {
        "risk_adj_momentum": 0.20,
        "trend_consistency": 0.15,
        "drawdown_ratio": 0.15,
        "vol_accel": 0.15,
        "atr_score": 0.15,
        "directional_eff": 0.10,
        "vol_expansion": 0.05,
        "vol_to_mcap": 0.05,
    },
    "scalp": {
        "atr_score": 0.20,
        "spread_score": 0.20,
        "vol_accel": 0.15,
        "profitability_ratio": 0.15,
        "risk_adj_momentum": 0.10,
        "trend_consistency": 0.05,
        "directional_eff": 0.05,
        "vol_expansion": 0.05,
        "vol_to_mcap": 0.05,
    },
}

# --- Exclusion list: non-tradeable / pegged assets ---
EXCLUDED_BASES = {
    "PAXG",
    "PAXGRL",
    "DAI",
    "USDT",
    "GUSD",
    "GYEN",
    "EURT",
    "USDC",
    "BUSD",
    "TUSD",
    "USDP",
    "PYUSD",
    "ZUSD",
}

# --- Preferred CoinGecko IDs for common Gemini symbols ---
# Resolves the issue where CoinGecko has many tokens sharing the same ticker.
COINGECKO_ID_OVERRIDES = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "LINK": "chainlink",
    "AAVE": "aave",
    "UNI": "uniswap",
    "SHIB": "shiba-inu",
    "PEPE": "pepe",
    "FET": "artificial-superintelligence-alliance",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
    "ADA": "cardano",
    "XRP": "ripple",
    "ATOM": "cosmos",
    "FIL": "filecoin",
    "APE": "apecoin",
    "MANA": "decentraland",
    "SAND": "the-sandbox",
    "AXS": "axie-infinity",
    "CHZ": "chiliz",
    "BAT": "basic-attention-token",
    "ZEC": "zcash",
    "COMP": "compound-governance-token",
    "MKR": "maker",
    "SNX": "havven",
    "CRV": "curve-dao-token",
    "SUSHI": "sushi",
    "YFI": "yearn-finance",
    "1INCH": "1inch",
    "GRT": "the-graph",
    "ENJ": "enjincoin",
    "LRC": "loopring",
    "STORJ": "storj",
    "AMP": "amp-token",
    "SKL": "skale",
    "IMX": "immutable-x",
    "RNDR": "render-token",
    "HYPE": "hyperliquid",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
    "FLOKI": "floki",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SUI": "sui",
    "SEI": "sei-network",
    "TIA": "celestia",
    "JUP": "jupiter-exchange-solana",
    "STX": "blockstack",
    "INJ": "injective-protocol",
    "NEAR": "near",
    "ALGO": "algorand",
    "XTZ": "tezos",
    "EOS": "eos",
    "EGLD": "elrond-erd-2",
    "FLOW": "flow",
    "GALA": "gala",
    "ICP": "internet-computer",
    "QNT": "quant-network",
    "RUNE": "thorchain",
    "XLM": "stellar",
    "JASMY": "jasmycoin",
}

# --- Filters (pass/fail) ---
MIN_AVG_DAILY_VOL = 50_000
MIN_CANDLE_DAYS = 25
MIN_ATR_PCT = 1.0
CORRELATION_THRESHOLD = 0.90
CANDIDATE_POOL = 40  # Pre-correlation pool size
DEFAULT_TOP_N = 10

# --- Gemini fee defaults (bps) ---
GEMINI_MAKER_FEE = 0.0025  # 25 bps
GEMINI_TAKER_FEE = 0.0040  # 40 bps
ROUND_TRIP_FEE_PCT = (GEMINI_MAKER_FEE + GEMINI_TAKER_FEE) * 100  # 0.65%


# =============================================================================
# Data fetching
# =============================================================================


def fetch_candles(symbol, days=60):
    """Fetch daily candles from Gemini V2 API (newest-first)."""
    try:
        data = requests.get(
            f"{GEMINI_BASE_V2}/candles/{symbol}/1day", timeout=10
        ).json()
        if not isinstance(data, list) or len(data) == 0:
            return []
        return [
            {
                "ts": candle[0],
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            }
            for candle in data[:days]
        ]
    except Exception:
        return []


def fetch_spread(symbol):
    """Fetch top-of-book bid/ask from Gemini order book.

    Returns (spread_pct, mid_price) or (None, None) on failure.
    """
    try:
        data = requests.get(
            f"{GEMINI_BASE}/book/{symbol}",
            params={"limit_bids": 5, "limit_asks": 5},
            timeout=10,
        ).json()
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not bids or not asks:
            return None, None

        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])
        mid = (best_bid + best_ask) / 2.0

        if mid <= 0:
            return None, None

        spread_pct = ((best_ask - best_bid) / mid) * 100
        return spread_pct, mid
    except Exception:
        return None, None


# =============================================================================
# Metric computation
# =============================================================================


def compute_atr(candles):
    """Average True Range as % of price (newest-first candle list)."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(len(candles) - 1):
        curr, prev = candles[i], candles[i + 1]
        tr = max(
            curr["high"] - curr["low"],
            abs(curr["high"] - prev["close"]),
            abs(curr["low"] - prev["close"]),
        )
        trs.append(tr / prev["close"] * 100 if prev["close"] > 0 else 0)
    return statistics.mean(trs) if trs else 0.0


def compute_metrics(candles_60):
    """Compute swing/scalp metrics from 60 daily candles (newest-first)."""
    cur = candles_60[:30]
    prev = candles_60[30:60]

    if len(cur) < MIN_CANDLE_DAYS:
        return None

    closes = [candle["close"] for candle in cur]
    highs = [candle["high"] for candle in cur]
    lows = [candle["low"] for candle in cur]
    vols_usd = [candle["volume"] * candle["close"] for candle in cur]
    prev_vols = (
        [candle["volume"] * candle["close"] for candle in prev]
        if len(prev) >= 10
        else []
    )

    avg_vol = statistics.mean(vols_usd)
    if avg_vol < MIN_AVG_DAILY_VOL:
        return None

    # Oldest-to-newest for return calculations
    rev_closes = list(reversed(closes))
    daily_returns = [
        (rev_closes[i] - rev_closes[i - 1]) / rev_closes[i - 1]
        for i in range(1, len(rev_closes))
    ]
    if len(daily_returns) < 2:
        return None

    total_return = (rev_closes[-1] - rev_closes[0]) / rev_closes[0]
    vol_std = statistics.stdev(daily_returns)

    # 1. Risk-adjusted momentum
    risk_adj_mom = total_return / vol_std if vol_std > 0 else 0

    # 2. Trend consistency
    up_days = sum(1 for r in daily_returns if r > 0)
    trend_consist = up_days / len(daily_returns)

    # 3. Drawdown ratio
    peak = max(highs)
    max_dd = (peak - min(lows)) / peak if peak > 0 else 0
    drawdown_ratio = total_return / max_dd if max_dd > 0 else total_return

    # 4. Volume acceleration
    cur_avg_vol = avg_vol
    prev_avg_vol = statistics.mean(prev_vols) if prev_vols else cur_avg_vol
    vol_accel = cur_avg_vol / prev_avg_vol if prev_avg_vol > 0 else 1.0

    # 5. ATR score — avg daily range as % of price (swing range quality)
    #    Uses cur candles; need prev close so pass one extra candle
    atr_candles = candles_60[:31]  # 30 cur + 1 prev for TR calculation
    atr_pct = compute_atr(atr_candles)
    if atr_pct < MIN_ATR_PCT:
        return None  # filter out dead/sideways coins

    # ATR score: reward meaningful range (2–6% sweet spot), penalize extremes
    # Maps to a bell-curve-like score peaking around 3-5% daily range
    if atr_pct < 2.0:
        atr_score = atr_pct / 2.0  # 0.0 – 1.0 ramp up
    elif atr_pct <= 6.0:
        atr_score = 1.0  # sweet spot
    else:
        atr_score = max(0.0, 1.0 - (atr_pct - 6.0) / 10.0)  # gradual penalty

    # 6. Directional efficiency — net move / sum of absolute daily moves
    abs_path = sum(abs(r) for r in daily_returns)
    dir_eff = abs(total_return) / abs_path if abs_path > 0 else 0
    # Sign-adjust: reward upward efficiency, penalize downward
    dir_eff_signed = dir_eff if total_return > 0 else -dir_eff

    # 7. Volatility expansion — current ATR vs prior period ATR
    prev_atr_candles = candles_60[29:]  # prior 30 days + 1 for TR
    prev_atr_pct = (
        compute_atr(prev_atr_candles) if len(prev_atr_candles) >= 2 else atr_pct
    )
    vol_expansion = atr_pct / prev_atr_pct if prev_atr_pct > 0 else 1.0

    # 8. Fee-aware profitability ratio (ATR / round-trip fee cost)
    profitability_ratio = atr_pct / ROUND_TRIP_FEE_PCT if ROUND_TRIP_FEE_PCT > 0 else 0

    return {
        "avg_daily_usd_vol": avg_vol,
        "total_return_pct": total_return * 100,
        "risk_adj_momentum": risk_adj_mom,
        "trend_consistency": trend_consist,
        "drawdown_ratio": drawdown_ratio,
        "vol_accel": vol_accel,
        "atr_pct": atr_pct,
        "atr_score": atr_score,
        "directional_eff": dir_eff_signed,
        "vol_expansion": vol_expansion,
        "profitability_ratio": profitability_ratio,
        "current_price": closes[0],
        "pct_from_30d_high": ((closes[0] - max(highs)) / max(highs)) * 100,
        "pct_above_30d_low": ((closes[0] - min(lows)) / min(lows))
        * 100,  # Proximity to support
        "returns_history": daily_returns,  # for correlation calculations
    }


def compute_correlation(returns1, returns2):
    """Pearson correlation coefficient."""
    if len(returns1) != len(returns2) or len(returns1) < 2:
        return 0.0
    try:
        mu1 = statistics.mean(returns1)
        mu2 = statistics.mean(returns2)
        std1 = statistics.stdev(returns1)
        std2 = statistics.stdev(returns2)
        if std1 == 0 or std2 == 0:
            return 0.0

        cov = sum((r1 - mu1) * (r2 - mu2) for r1, r2 in zip(returns1, returns2)) / (
            len(returns1) - 1
        )
        return cov / (std1 * std2)
    except Exception:
        return 0.0


def normalize(coins, key):
    """Min-max normalise a metric across all coins."""
    vals = [c[key] for c in coins]
    lo, hi = min(vals), max(vals)
    rng = hi - lo
    for c in coins:
        c[f"{key}_norm"] = (c[key] - lo) / rng if rng > 0 else 0.5


# =============================================================================
# CoinGecko enrichment
# =============================================================================


def enrich_coingecko(raw):
    """Add market_cap, vol_to_mcap, and resolved name from CoinGecko."""
    print("Fetching CoinGecko coin list...")
    try:
        coin_list = requests.get(
            "https://api.coingecko.com/api/v3/coins/list", timeout=30
        ).json()
    except Exception as e:
        print(f"  CoinGecko list error (non-fatal): {e}")
        # Fill defaults so the rest of the pipeline works
        for m in raw.values():
            m["market_cap"] = 0
            m["vol_to_mcap"] = 0
            m["name"] = m.get("symbol", "?")
        return

    sym_to_ids = defaultdict(list)
    for item in coin_list:
        sym_to_ids[item["symbol"].upper()].append(item["id"])

    base_to_cg = {}
    for base in raw:
        # Use override mapping first (guaranteed correct)
        override = COINGECKO_ID_OVERRIDES.get(base.upper())
        if override:
            base_to_cg[base] = override
            continue
        # Fallback: heuristic match
        cands = sym_to_ids.get(base.upper(), [])
        if cands:
            best = next(
                (c for c in cands if base.lower() == c or base.lower() in c), cands[0]
            )
            base_to_cg[base] = best

    ids = list(base_to_cg.values())
    cg_data = {}
    for i in range(0, len(ids), 250):
        chunk = ids[i : i + 250]
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ",".join(chunk),
                    "sparkline": "false",
                    "per_page": 250,
                },
                timeout=30,
            ).json()
            for coin in resp:
                if isinstance(coin, dict):
                    cg_data[coin["id"]] = coin
            time.sleep(1.5)
        except Exception as e:
            print(f"  CoinGecko batch error: {e}")

    for base, m in raw.items():
        cg = cg_data.get(base_to_cg.get(base, ""), {})
        mcap = cg.get("market_cap") or 0
        m["market_cap"] = mcap
        m["vol_to_mcap"] = (m["avg_daily_usd_vol"] * 30) / mcap if mcap > 0 else 0
        m["name"] = cg.get("name", base)


# =============================================================================
# Display
# =============================================================================


def print_main_table(top, profile_name, weights, total_evaluated, total_symbols):
    """Print the main ranking table to stdout."""
    w_str = " | ".join(
        f"{k.replace('_', ' ').title()}({int(v * 100)}%)" for k, v in weights.items()
    )
    print("\n" + "=" * 160)
    print(f"GEMINI TOP {len(top)} — {profile_name.upper()} TRADING COMPOSITE RANKING")
    print(w_str)
    print("=" * 160)

    has_spread = any("spread_pct" in c for c in top)
    has_profit = any("profitability_ratio" in c for c in top)

    # Header
    hdr = (
        f"{'Rk':<4} {'Sym':<7} {'Name':<20} {'Score':<7} {'30dRet':>7} {'ATR%':>6} "
        f"{'DirEff':>7} {'VolExp':>7} {'Trend%':>7} {'DDRatio':>9} "
        f"{'vs30Hi':>7} {'vs30Lo':>8}"
    )
    if has_spread:
        hdr += f" {'Spread%':>9}"
    if has_profit:
        hdr += f" {'ProfR':>7}"
    hdr += f" {'AvgDailyVol':>14}"
    print(hdr)
    print("-" * 160)

    for i, c in enumerate(top, 1):
        quality = "🟢" if c["score"] > 0.65 else "🟡" if c["score"] > 0.40 else "🔴"
        trend_pct = c["trend_consistency"] * 100
        line = (
            f"{i:<4} {c['symbol']:<7} {c['name'][:19]:<20} "
            f"{c['score']:.3f}  "
            f"{c['total_return_pct']:>+7.1f}% "
            f"{c['atr_pct']:>5.1f}% "
            f"{c['directional_eff']:>+7.3f} "
            f"{c['vol_expansion']:>6.2f}x "
            f"{trend_pct:>6.1f}% "
            f"{c['drawdown_ratio']:>+8.2f} "
            f"{c['pct_from_30d_high']:>+7.1f}% "
            f"{c['pct_above_30d_low']:>+7.1f}%"
        )
        if has_spread:
            sp = c.get("spread_pct")
            line += f" {sp:>7.3f}%" if sp is not None else f" {'N/A':>8}"
        if has_profit:
            pr = c.get("profitability_ratio", 0)
            line += f" {pr:>5.1f}x"
        line += f" ${c['avg_daily_usd_vol']:>13,.0f}  {quality}"
        print(line)

    print("-" * 160)
    filtered = total_symbols - total_evaluated
    print(
        f"\nTotal coins evaluated: {total_evaluated}  |  "
        f"Filtered out (low ATR/volume/data/excluded): {filtered}"
    )
    print(
        f"\nScore tiers:  "
        f"🟢 Strong(>0.65): {len([c for c in top if c['score'] > 0.65])}  "
        f"🟡 Moderate(>0.40): {len([c for c in top if 0.40 < c['score'] <= 0.65])}  "
        f"🔴 Weak(<=0.40): {len([c for c in top if c['score'] <= 0.40])}"
    )


def print_score_breakdown(top, weights):
    """Print normalised score breakdown."""
    print("\n" + "=" * 140)
    print(f"NORMALIZED SCORE BREAKDOWN — TOP {len(top)}")
    print("=" * 140)

    # Build header dynamically from weights
    hdr = f"{'Rk':<4} {'Sym':<7} "
    for k in weights:
        label = k[:8].title()
        hdr += f"{label:>9} "
    hdr += f"{'Score':>7}"
    print(hdr)
    print("-" * 110)

    for i, c in enumerate(top, 1):
        line = f"{i:<4} {c['symbol']:<7} "
        for k in weights:
            nk = f"{k}_norm"
            val = c.get(nk, 0.0)
            line += f"{val:>9.3f} "
        line += f"{c['score']:>7.3f}"
        print(line)


def print_tips(profile_name):
    """Print contextual trading tips."""
    print("\n" + "=" * 160)
    if profile_name == "scalp":
        print(
            "💡 Spread < 0.10% is ideal for scalping — tighter spread = more profit per trade."
        )
        print(
            "💡 Profitability Ratio > 3x means ATR is 3× the round-trip fee cost (strong scalp candidate)."
        )
        print(
            f"💡 Gemini round-trip cost (maker+taker): {ROUND_TRIP_FEE_PCT:.2f}% — ATR must exceed this."
        )
        print("💡 High volume acceleration + tight spread = textbook scalp setup.")
    else:
        print(
            "💡 ATR sweet spot is 2–6% daily range — enough to swing trade profitably without excessive noise."
        )
        print(
            "💡 High directional efficiency + expanding volatility = textbook swing setup."
        )
        print(
            "💡 Coins within 10% of their 30d high with rising volume are the strongest breakout candidates."
        )
    print("=" * 160)


# =============================================================================
# Export
# =============================================================================


def export_results(top, profile_name):
    """Save results to JSON in hub_data/ranker_results.json."""
    export_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hub_data")
    os.makedirs(export_dir, exist_ok=True)
    export_path = os.path.join(export_dir, "ranker_results.json")

    # Strip non-serialisable fields
    exportable = []
    for c in top:
        entry = {k: v for k, v in c.items() if k != "returns_history"}
        exportable.append(entry)

    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "profile": profile_name,
        "count": len(exportable),
        "round_trip_fee_pct": ROUND_TRIP_FEE_PCT,
        "results": exportable,
    }

    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\n📁 Results exported to: {export_path}")


# =============================================================================
# Main
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Rank Gemini USD pairs for swing/scalp trading."
    )
    parser.add_argument(
        "--profile",
        choices=["swing", "scalp"],
        default="swing",
        help="Scoring weight profile (default: swing)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Number of top coins to display (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export results to hub_data/ranker_results.json",
    )
    parser.add_argument("--cprofile", action="store_true", help="Run with cProfile")
    parser.add_argument(
        "--line-profile", action="store_true", help="Run with line_profiler"
    )
    args = parser.parse_args()

    profiler = None
    if args.cprofile:
        import cProfile

        profiler = cProfile.Profile()
        profiler.enable()
        print("Starting Gemini Ranker with cProfile...")
    elif args.line_profile:
        try:
            from line_profiler import LineProfiler

            profiler = LineProfiler()
            profiler.add_function(compute_metrics)
            profiler.add_function(compute_correlation)
            profiler.add_function(enrich_coingecko)
            profiler.enable_by_count()
            print("Starting Gemini Ranker with line_profiler...")
        except ImportError:
            print("line_profiler is not installed. Run 'pip install line_profiler'.")
            profiler = None

    profile_name = args.profile
    weights = PROFILES[profile_name]
    final_n = args.top

    print(f"Profile: {profile_name.upper()}  |  Top: {final_n}")
    print("Fetching Gemini trading symbols...")
    try:
        all_symbols = requests.get(f"{GEMINI_BASE}/symbols", timeout=15).json()
    except Exception as e:
        print(f"Error fetching symbols: {e}")
        return

    usd_symbols = [
        s for s in all_symbols if s.endswith("usd") and not s.endswith("gusd")
    ]

    # --- Deduplication logic ---
    # Filter out "Retail" variants (e.g., dotrlusd when dotusd exists).
    base_names = set()
    for s in usd_symbols:
        base = s[:-3].upper()
        if not base.endswith("RL"):
            base_names.add(base)

    filtered_usd = []
    for s in usd_symbols:
        base = s[:-3].upper()
        if base.endswith("RL"):
            parent = base[:-2]
            if parent in base_names:
                continue
        filtered_usd.append(s)

    base_to_symbol = {s[:-3].upper(): s for s in filtered_usd}

    # --- Apply exclusion list ---
    excluded_count = 0
    for exc in list(base_to_symbol.keys()):
        if exc in EXCLUDED_BASES:
            del base_to_symbol[exc]
            excluded_count += 1

    total_symbols = len(base_to_symbol) + excluded_count
    print(
        f"Found {len(base_to_symbol)} tradeable USD pairs "
        f"({excluded_count} stablecoin/peg excluded). "
        f"Fetching 60 days of candles...\n"
    )

    # --- Fetch candles and compute metrics ---
    raw = {}
    for idx, (base, sym) in enumerate(base_to_symbol.items(), 1):
        if idx % 20 == 0:
            print(f"  Progress: {idx}/{len(base_to_symbol)} coins...")
        candles = fetch_candles(sym, days=60)
        if candles:
            m = compute_metrics(candles)
            if m:
                m["symbol"] = base
                m["gemini_symbol"] = sym
                raw[base] = m
        time.sleep(0.15)

    print(f"\nPassed filters: {len(raw)} coins")

    # --- Bid-ask spread enrichment ---
    need_spread = "spread_score" in weights
    if need_spread and raw:
        print("Fetching order book spreads...")
        for idx, (base, m) in enumerate(raw.items(), 1):
            sym = m["gemini_symbol"]
            spread_pct, _ = fetch_spread(sym)
            m["spread_pct"] = spread_pct if spread_pct is not None else 999.0
            # Spread score: lower spread = higher score (inverted, capped)
            # 0.01% → 1.0, 0.10% → ~0.9, 0.50% → ~0.5, 2%+ → 0
            if spread_pct is not None and spread_pct > 0:
                m["spread_score"] = max(0.0, 1.0 - (spread_pct / 2.0))
            else:
                m["spread_score"] = 0.0
            time.sleep(0.15)
            if idx % 10 == 0:
                print(f"  Spread progress: {idx}/{len(raw)}...")
    else:
        # Fill defaults for profiles that don't use spread
        for m in raw.values():
            m["spread_pct"] = None
            m["spread_score"] = 0.5

    # --- CoinGecko enrichment ---
    enrich_coingecko(raw)

    coins = list(raw.values())
    if not coins:
        print(
            "No coins passed all filters. Try lowering MIN_AVG_DAILY_VOL or MIN_ATR_PCT."
        )
        return

    # --- Normalize all metrics used in this profile ---
    for key in weights:
        if any(key in c for c in coins):
            normalize(coins, key)

    # --- Composite score ---
    for c in coins:
        c["score"] = sum(weights[k] * c.get(f"{k}_norm", 0.0) for k in weights)

    coins.sort(key=lambda x: x["score"], reverse=True)

    # --- Correlation Pruning (Diversification) ---
    final_top = []
    pruned_count = 0

    for c in coins:
        if len(final_top) >= final_n:
            break

        is_redundant = False
        for existing in final_top:
            corr = compute_correlation(
                c["returns_history"], existing["returns_history"]
            )
            if corr > CORRELATION_THRESHOLD:
                is_redundant = True
                pruned_count += 1
                break

        if not is_redundant:
            final_top.append(c)

    top = final_top

    if pruned_count > 0:
        print(f"Correlation pruning removed {pruned_count} redundant assets.")

    # --- Display ---
    print_main_table(top, profile_name, weights, len(coins), total_symbols)
    print_score_breakdown(top, weights)
    print_tips(profile_name)

    # --- Export ---
    if args.export:
        export_results(top, profile_name)

    if profiler:
        prof_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "hub_data", "profiles"
        )
        os.makedirs(prof_dir, exist_ok=True)
        if args.cprofile:
            profiler.disable()
            prof_file = os.path.join(prof_dir, f"ranker_{profile_name}_cprofile.prof")
            profiler.dump_stats(prof_file)
            print(f"\\n[PROFILING] cProfile stats saved to {prof_file}")
        elif args.line_profile:
            profiler.disable_by_count()
            prof_file = os.path.join(
                prof_dir, f"ranker_{profile_name}_line_profile.txt"
            )
            with open(prof_file, "w", encoding="utf-8") as f:
                profiler.print_stats(stream=f)
            print(f"\\n[PROFILING] line_profiler stats saved to {prof_file}")


if __name__ == "__main__":
    main()
