"""
Audit candles.db for synthetic vs real data composition.
Synthetic candles are tagged with exchange='synthetic'; real with 'kucoin'.
"""

import sqlite3

DB_PATH = "data/candles.db"


def audit_candles():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in cursor.fetchall()]
    if "candles" not in tables:
        print("AUDIT FAIL: candles table missing.")
        return

    cursor.execute(
        "SELECT exchange, timeframe, COUNT(*) FROM candles GROUP BY exchange, timeframe ORDER BY exchange, timeframe;"
    )
    rows = cursor.fetchall()

    total_real = 0
    total_synthetic = 0

    print("=== CANDLE DB AUDIT (Synthetic vs Real) ===")
    for exchange, tf, count in rows:
        if exchange == "synthetic":
            total_synthetic += count
            label = "SYNTHETIC"
        else:
            total_real += count
            label = "REAL"

        note = " [GAP RISK]" if (tf == "3m" and count == 0) else ""
        print(f"  {exchange:10} {tf:4s}: {count:8d} candles ({label}){note}")

    total_all = total_real + total_synthetic
    synthetic_pct = (total_synthetic / total_all * 100) if total_all > 0 else 0.0
    real_pct = 100.0 - synthetic_pct

    print("\nSummary:")
    print(f"  Real candles:       {total_real} ({real_pct:.1f}%)")
    print(f"  Synthetic candles:  {total_synthetic} ({synthetic_pct:.1f}%)")
    print(f"  Total:              {total_all}")

    cursor.execute(
        "SELECT exchange, COUNT(*) FROM candles WHERE timeframe = '3m' GROUP BY exchange;"
    )
    tf3 = cursor.fetchall()
    if tf3:
        print("\n3m timeframe breakdown:")
        for ex, count in tf3:
            print(f"  {ex}: {count}")
    else:
        print("\n3m timeframe: NO DATA (will trigger synthetic fallback if requested)")
    conn.close()


if __name__ == "__main__":
    audit_candles()
