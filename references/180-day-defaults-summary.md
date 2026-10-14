# 180-Day Hardcoded Defaults Map

## Summary
Found **37 occurrences** of hardcoded `180` day defaults across the codebase, spread across 15 files. These appear in Pydantic model fields, function parameter defaults, dictionary mappings, and config dataclasses.

---

## src/app.py (5 occurrences)
| Line | Context |
|------|---------|
| 169 | `days=180` — `DataLoader.load_candles()` call in backtest/validation flow |
| 275 | `days=180` — `DataLoader.load_candles()` call in backtest/validation flow |
| 372 | `days=180` — `DataLoader.load_candles()` call in backtest/validation flow |
| 535 | `days: int = 180` — `SyncHistoryRequest` Pydantic model (API: `/api/sync_history`) |
| 1207 | `days: int = 180` — `BatchOptimizeRequest` Pydantic model (API: `/api/batch_optimize`) |

---

## src/config.py (1 occurrence)
| Line | Context |
|------|---------|
| 200 | `regime_sweep_days: int = 180` — `OptunaConfig` dataclass field (regime-sweep sweep window) |

---

## src/data_loader.py (5 occurrences)
| Line | Context |
|------|---------|
| 46 | `"3m": 180,` — `CACHE_TTL_SECONDS` dict: 180 seconds = 3 minutes (staleness check) |
| 131 | `days: int = 180` — `fetch_kucoin_historical()` method default |
| 145 | `"3m": 180,` — duplicate entry in timeframe mapping (same cache TTL dict) |
| 174 | `days: int = 180` — `fetch_gemini_historical()` method default |
| 468 | `"1d": 180,` — `tf_days_map` dict: maps "1d" timeframe to 180 days for donor-TF resolution |

---

## src/batch_optimizer.py (1 occurrence)
| Line | Context |
|------|---------|
| 55 | `days: int = 180` — `BatchOptimizerConfig` dataclass field |

---

## src/providers/alpaca.py (1 occurrence)
| Line | Context |
|------|---------|
| 85 | `days: int = 180` — `fetch_historical_candles()` method default |

---

## src/providers/base.py (1 occurrence)
| Line | Context |
|------|---------|
| 95 | `days: int = 180` — Base provider class default for `fetch_historical_candles` |

---

## src/providers/crypto.py (2 occurrences)
| Line | Context |
|------|---------|
| 33 | `days: int = 180` — Gemini provider default |
| 107 | `days: int = 180` — Another Gemini/provider default |

---

## src/providers/polygon.py (1 occurrence)
| Line | Context |
|------|---------|
| 71 | `days: int = 180` — `fetch_historical_candles()` method default |

---

## src/providers/synthetic.py (1 occurrence)
| Line | Context |
|------|---------|
| 158 | `days: int = 180` — Synthetic simulation provider default |

---

## src/providers/unified_adapter.py (2 occurrences)
| Line | Context |
|------|---------|
| 66 | `days: int = 180` — `fetch()` method default |
| 114 | `days: int = 180` — Another method in unified adapter |

---

## src/paper/engine.py (1 occurrence)
| Line | Context |
|------|---------|
| 58 | `"3m": 180,` — Timeframe-to-seconds mapping dict (3 minutes in seconds) |

---

## src/web/app.js (2 occurrences)
| Line | Context |
|------|---------|
| 996 | `days = 180;` — Front-end JavaScript default |
| 1580 | `days = 180;` — Front-end JavaScript default |

---

## tests/test_batch_optimizer.py (1 occurrence)
| Line | Context |
|------|---------|
| 95 | `assert cfg.days == 180` — Test assertion verifying default value |

---

## Key Observations
1. **Pattern**: Most 180-day defaults are function/method parameter defaults (`days: int = 180`) or Pydantic model field defaults. These control how many days of historical candles to fetch.
2. **Data_loader.py lines 46/145/468**: These are **time-in-seconds mappings** (180 seconds = 3 min, 180 "days" for donor-TF resolution), not historical-data fetch defaults.
3. **Provider base class** (`base.py` line 95): Sets `days: int = 180` as the universal default across all data providers, making it a single point of change.
4. **app.py**: The `days=180` hardcoded in three `DataLoader.load_candles()` calls (lines 169, 275, 372) could be replaced by pulling from `req.days` or a config value — these are in request-handling flows where `days` is already a parameter.
5. **config.py `regime_sweep_days`**: A distinct config parameter for regime-sweep optimization window, separate from candle-history duration.

## Recommendations
- Extract `180` to a single configurable constant (e.g., `DEFAULT_DAYS = 180`) in `config.py` or the provider base class.
- Replace hardcoded `days=180` in `app.py` request handlers with `req.days` or config lookup.
- Audit whether `180` days is a regulatory/strategy requirement or an arbitrary default that should be user-configurable.