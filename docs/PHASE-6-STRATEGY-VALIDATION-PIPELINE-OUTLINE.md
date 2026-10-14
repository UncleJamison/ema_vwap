# Phase 6 Architecture Outline — Automated Strategy Validation Pipeline

**Epic**: `ema_vwap-tnt` — *Automated Strategy Validation Pipeline: Multi-Stage Gate (Backtest + WFO + Monte Carlo)*

## Architecture Overview

```mermaid
flowchart TD
    subgraph Validation Engine (src/validator.py)
        A[Optuna Sweep / Candidate Params] --> B[Gate 1: In-Sample Backtest Engine]
        B -->|Check Viability| C{Return > 0% & Sharpe ≥ 1.0?}
        C -->|No| F[Status: FAIL / Poor Performer]
        C -->|Yes: Gate 1 Pass| D[Gate 2: Walk-Forward Engine]
        
        D -->|Evaluate Overfit| G{WFE ≥ 50% & Consistency ≥ 50%?}
        G -->|No| H[Status: CAUTION / Overfit Risk]
        G -->|Yes: Gate 2 Pass| E[Gate 3: Monte Carlo Simulator]
        
        E -->|Stress Risk| I{Risk of Ruin < 5% & Max DD < 25%?}
        I -->|No| J[Status: CAUTION / Tail Risk]
        I -->|Yes: Gate 3 Pass| K[Status: ROBUST / Production Grade]
    end
    
    subgraph UI & Deployment Gates
        F & H & J & K --> L[Persist Results & Badges in SQLite]
        L --> M[Optuna & Batch Sweep Leaderboard UI]
        M --> N{Attempt Deploy to Paper?}
        N -->|If FAIL or CAUTION| O[Show Safety Warning & Breakdown Modal]
        N -->|If ROBUST| P[Immediate Safe 1-Click Deployment]
    end
```

## Subtasks & Breakdown

### Subtask 1: Strategy Validation Engine & Multi-Stage Gate REST API (`ema_vwap-tnt.1`)
- **`src/validator.py`**:
  - Implement `ValidationCriteria` dataclass (configurable thresholds for Return, Sharpe, WFE %, Consistency %, Risk of Ruin %, Max DD %).
  - Implement `StrategyValidator` orchestrating `BacktestEngine`, `WalkForwardEngine`, and `MonteCarloSimulator`.
  - Structured output `ValidationReport` with overall `status` (`ROBUST`, `CAUTION`, `FAIL`), individual gate pass/fail flags, metrics dictionary, and actionable summary messages.
- **REST API**:
  - `POST /api/strategy/validate`: Runs validation pipeline and returns structured report.

### Subtask 2: Optuna Sweep Auto-Validation & 1-Click Table Actions (`ema_vwap-tnt.2`)
- **Auto-Validation on Sweeps**:
  - Add auto-validation option to Optuna single-symbol and Batch multi-coin sweep workers.
  - Automatically evaluate top candidate trials.
- **Interactive Table Integration**:
  - Add `🔬 Validate` action button and status badge column to Optuna Top Trials table and Batch Sweep Leaderboard table.
  - Persist validation scores and gate results in SQLite database.

### Subtask 3: Validation Details Modal & Paper Deployment Guardrails (`ema_vwap-tnt.3`)
- **Inspection Modal / Popover**:
  - Clickable status badge in tables opening a clean breakdown modal of Gate 1, 2, and 3 metrics.
- **Paper Deployment Guardrail**:
  - Confirmation alert/warning modal when attempting to deploy a failed or flagged configuration into live paper trading.
