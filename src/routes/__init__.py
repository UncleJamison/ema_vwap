"""
FastAPI route modules extracted from src/app.py (ema_vwap-tq41).

Each module holds endpoint groups moved verbatim from the monolith. Shared
state (databases, settings manager, paper engine, ws manager) stays in
src.app and is read by route handlers at call time via `_st()` so tests
that monkeypatch `src.app._*` attributes continue to work.
"""

from .alerts import router as alerts_router
from .api_diagnostics import router as api_diagnostics_router
from .audit import router as audit_router
from .backtest_opt import router as backtest_opt_router
from .batch import router as batch_router
from .data_ops import router as data_ops_router
from .oms import router as oms_router
from .paper import router as paper_router
from .portfolio import router as portfolio_router
from .settings import router as settings_router
from .tasks import router as tasks_router
from .tear_sheet import router as tear_sheet_router
from .ws import router as ws_router

__all__ = [
    "alerts_router",
    "api_diagnostics_router",
    "audit_router",
    "backtest_opt_router",
    "batch_router",
    "data_ops_router",
    "oms_router",
    "paper_router",
    "portfolio_router",
    "settings_router",
    "tasks_router",
    "tear_sheet_router",
    "ws_router",
]
