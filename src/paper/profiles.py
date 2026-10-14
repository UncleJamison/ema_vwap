"""
Per-Asset Optuna Profile Registry Module.
Manages persistent calibration profiles discovered during Optuna hyperparameter optimization.
"""

import json
from typing import Any

from src.config import StrategyParams
from src.database import CandleDatabase
from src.paper.models import PaperProfile


class PaperProfileRegistry:
    """Registry managing per-asset calibrated Optuna strategy parameters."""

    def __init__(self, db: CandleDatabase | None = None) -> None:
        if db is None:
            from src.db_connection_manager import get_database

            db = get_database("paper_trading")
        self.db = db

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
        deployed: bool = False,
        deployed_at: str | None = None,
        validation_report: dict[str, Any] | None = None,
    ) -> int:
        """Save or update an Optuna hyperparameter profile for a specific asset."""
        if isinstance(params, StrategyParams):
            params_dict = params.to_dict()
            params_json = json.dumps(params_dict)
        elif isinstance(params, dict):
            params_json = json.dumps(params)
        else:
            params_json = str(params)

        validation_json = json.dumps(validation_report) if validation_report else None

        return self.db.save_paper_profile(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            strategy_mode=strategy_mode,
            target_metric=target_metric,
            params_json=params_json,
            optuna_score=optuna_score,
            is_active=is_active,
            deployed=deployed,
            deployed_at=deployed_at,
            validation_report_json=validation_json,
        )

    def import_batch_result_as_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        strategy_mode: str,
        target_metric: str,
        best_value: float,
        best_params: dict[str, Any] | str,
        risk_per_trade_pct: float | None = None,
        is_active: bool = True,
    ) -> int:
        """
        Import a batch sweep result into the paper profiles registry.
        Ensures risk_per_trade_pct from the sweep is preserved in params_json
        so paper trading respects the batch-swept risk profile.
        """

        if isinstance(best_params, str):
            params = json.loads(best_params)
        else:
            params = dict(best_params)
        if risk_per_trade_pct is not None:
            params["risk_per_trade_pct"] = risk_per_trade_pct
        return self.save_profile(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            strategy_mode=strategy_mode,
            target_metric=target_metric,
            params=params,
            optuna_score=best_value,
            is_active=is_active,
            deployed=False,
            deployed_at=None,
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
        self,
        exchange: str | None = None,
        active_only: bool = False,
        deployed_only: bool = False,
    ) -> list[PaperProfile]:
        """List all saved paper profiles."""
        rows = self.db.list_paper_profiles(
            exchange=exchange, active_only=active_only, deployed_only=deployed_only
        )
        return [self._row_to_profile(r) for r in rows]

    def delete_profile(self, profile_id: int) -> bool:
        """Delete a profile by database ID."""
        return self.db.delete_paper_profile(profile_id)

    def set_active(
        self, profile_id: int, is_active: bool, deployed: bool | None = None
    ) -> bool:
        """Enable or disable a profile, optionally set deployed flag."""
        return self.db.set_paper_profile_active(
            profile_id, is_active, deployed=deployed
        )

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
        Prefers deployed profiles (from batch sweep validation) over active profiles.
        Falls back to timeframe-agnostic deployed profiles when no timeframe-specific one exists.
        """
        # 1. Try deployed profile with exact timeframe
        profile = self.get_deployed_profile(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            strategy_mode=strategy_mode,
        )
        # 2. Fallback: deployed profile without timeframe (timeframe-agnostic)
        if not profile and timeframe:
            profile = self.get_deployed_profile(
                exchange=exchange,
                symbol=symbol,
                timeframe=None,
                strategy_mode=strategy_mode,
            )
        # 3. Fallback: active profile with exact timeframe
        if not profile:
            profile = self.get_profile(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                strategy_mode=strategy_mode,
            )
        # 4. Fallback: active profile without timeframe
        if not profile and timeframe:
            profile = self.get_profile(
                exchange=exchange,
                symbol=symbol,
                timeframe=None,
                strategy_mode=strategy_mode,
            )
        if not profile or (not profile.params and not profile.deployed):
            return fallback_params or StrategyParams()

        try:
            return StrategyParams.from_dict(profile.params)
        except Exception:
            return fallback_params or StrategyParams()

    def get_deployed_profile(
        self,
        exchange: str,
        symbol: str,
        timeframe: str | None = None,
        strategy_mode: str | None = None,
    ) -> PaperProfile | None:
        """Retrieve the deployed profile for an asset, or None if not deployed."""
        profiles = self.list_profiles(exchange=exchange, deployed_only=True)
        for p in profiles:
            if p.symbol == symbol and p.deployed:
                if timeframe and p.timeframe != timeframe:
                    continue
                if strategy_mode and p.strategy_mode != strategy_mode:
                    continue
                return p
        return None

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
            deployed=bool(row.get("deployed", 0)),
            deployed_at=row.get("deployed_at"),
            validation_report_json=row.get("validation_report_json"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )
