import sys

sys.path.insert(0, "/c/crypto/ema_vwap")

if __name__ == "__main__":
    from src.config import OptunaConfig, StrategyParams
    from src.data_loader import DataLoader
    from src.optimizer import (
        OptunaObjectiveEvaluator,
        OptunaStudyCoordinator,
        OptunaTrialSpec,
    )

    df = DataLoader.generate_synthetic_candles(num_bars=150, seed=42)
    base_params = StrategyParams(strategy_mode="crossover")
    config = OptunaConfig(n_trials=6, sampler_seed=777)
    spec = OptunaTrialSpec.create(base_params, config)
    evaluator = OptunaObjectiveEvaluator(spec=spec, df=df)

    with OptunaStudyCoordinator() as coordinator:
        study = coordinator.create_study(directions="maximize")
        completed_study = coordinator.run_workers(
            evaluator=evaluator, total_trials=6, n_workers=2, master_seed=777
        )
        print("total trials:", len(completed_study.trials))
        for t in completed_study.trials:
            print(
                f"  trial {t.number}: state={t.state}, value={t.value}, params={t.params}"
            )
