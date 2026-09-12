"""
Per-Asset Optuna Profile Registry Module.
Manages persistent calibration profiles discovered during Optuna hyperparameter optimization.
"""

import json
from typing import Any

from src.config import StrategyParams
from src.database import CandleDatabase
from src.database import db as default_db
from src.paper.models import PaperProfile


class PaperProfileRegistry:
    """Registry managing per-asset calibrated Optuna strategy parameters."""

    def __init__(self, db: CandleDatabase | None = None) -> None:
        self.db = db or default_db

    def save_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str,
        target_metric: str,
        params: dict[str, Any] | StrategyParams | str,
        optuna_score: float = 0.0,
        is_active: bool = True,
    ) -> int:
        """Save or update an Optuna hyperparameter profile for a specific asset."""
        if isinstance(params, StrategyParams):
            params_dict = params.to_dict()
            params_json = json.dumps(params_dict)
        elif isinstance(params, dict):
            params_json = json.dumps(params)
        else:
            params_json = str(params)

        return self.db.save_paper_profile(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            strategy_mode=strategy_mode,
            target_metric=target_metric,
            params_json=params_json,
            optuna_score=optuna_score,
            is_active=is_active,
        )

    def get_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str | None = None,
        strategy_mode: str | None = None,
    ) -> PaperProfile | None:
        """Retrieve the active PaperProfile for a given asset."""
        row = self.db.get_paper_profile(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            strategy_mode=strategy_mode,
        )
        if not row:
            return None
        return self._row_to_profile(row)

    def list_profiles(
        self, exchange: str | None = None, active_only: bool = False
    ) -> list[PaperProfile]:
        """List all saved paper profiles."""
        rows = self.db.list_paper_profiles(exchange=exchange, active_only=active_only)
        return [self._row_to_profile(r) for r in rows]

    def delete_profile(self, profile_id: int) -> bool:
        """Delete a profile by database ID."""
        return self.db.delete_paper_profile(profile_id)

    def set_active(self, profile_id: int, is_active: bool) -> bool:
        """Enable or disable a profile."""
        return self.db.set_paper_profile_active(profile_id, is_active)

    def update_trade_direction(self, profile_id: int, trade_direction: str) -> bool:
        """Update trade direction (account mode) for a profile."""
        return self.db.update_paper_profile_trade_direction(profile_id, trade_direction)

    def get_strategy_params(
        self,
        exchange: str,
        symbol: str,
        timeframe: str | None = None,
        strategy_mode: str | None = None,
        fallback_params: StrategyParams | None = None,
    ) -> StrategyParams:
        """
        Retrieve StrategyParams configured for this asset, or fallback to default StrategyParams.
        """
        profile = self.get_profile(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            strategy_mode=strategy_mode,
        )
        if not profile or not profile.params:
            return fallback_params or StrategyParams()

        try:
            return StrategyParams.from_dict(profile.params)
        except Exception:
            return fallback_params or StrategyParams()

    @staticmethod
    def _row_to_profile(row: dict[str, Any]) -> PaperProfile:
        """Convert database dictionary to PaperProfile domain object."""
        try:
            params = json.loads(row.get("params_json", "{}"))
        except Exception:
            params = {}

        return PaperProfile(
            profile_id=row.get("id"),
            exchange=row.get("exchange", ""),
            symbol=row.get("symbol", ""),
            timeframe=row.get("timeframe", ""),
            strategy_mode=row.get("strategy_mode", ""),
            target_metric=row.get("target_metric", ""),
            params=params,
            optuna_score=float(row.get("optuna_score", 0.0)),
            is_active=bool(row.get("is_active", 1)),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
