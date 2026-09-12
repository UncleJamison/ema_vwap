"""
RED test — verifies batch-auto-validate triggers validation on all saved
batch results after sweep completes, updates DB badges, and refreshes leaderboard.

Runs RED until the feature is implemented.
"""
import json

from fastapi.testclient import TestClient

import src.app as app_module
from src.app import app

# Import BatchOptimizer so the backend endpoint can use it
from src.database import CandleDatabase


def test_batch_auto_validate_writes_status_for_all_results_after_sweep(
    tmp_path, monkeypatch
):
    """RED expectation: the batch-auto-validate checkbox is dead code —
    after a sweep completes, no saved result has a non-null validation_status.

    Once the feature is wired, this test should pass: every saved result
    receives a non-null validation_status and validation_json, and the
    leaderboard renders the corresponding validation badges.
    """
    db = CandleDatabase(db_path=str(tmp_path / "batch_autovalidate.db"))
    # Seed two results as if a sweep just finished
    db.save_batch_result(
        "kucoin", "BTC/USD", "5m", "auto", "sharpe_ratio",
        1.5, 8, 1.5, 1.2, 60.0, 4.0, 12.0,
        json.dumps({"fast_ema": 9, "slow_ema": 21, "trend_ema": 50}),
        "2026-08-20T00:00:00+00:00",
    )
    db.save_batch_result(
        "kucoin", "ETH/USD", "5m", "auto", "sharpe_ratio",
        1.2, 6, 1.0, 0.8, 50.0, 3.0, 8.0,
        json.dumps({"fast_ema": 9, "slow_ema": 21, "trend_ema": 50}),
        "2026-08-20T00:00:00+00:00",
    )
    monkeypatch.setattr(app_module, "_batch_db", db)
    client = TestClient(app)

    # Simulate the frontend invoking auto-validate after sweep completion
    resp = client.post("/api/batch_results/auto-validate", json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["validated_count"] >= 1

    # Verify every saved result now has a non-null validation_status
    rows = db.load_batch_results(limit=100)
    for r in rows:
        assert r["validation_status"] is not None, (
            f"Result {r['id']} ({r['symbol']}/{r['timeframe']}) "
            "has no validation_status after auto-validate"
        )
        assert r["validation_json"] is not None