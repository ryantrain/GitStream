from __future__ import annotations

import numpy as np
from xgboost import XGBRegressor


class MergeDelayModel:
    """Small fitted XGBoost regressor used for PR merge-delay forecasting."""

    def __init__(self) -> None:
        self.model = XGBRegressor(
            n_estimators=80,
            max_depth=4,
            learning_rate=0.12,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="reg:squarederror",
            random_state=42,
        )

        training_features = np.array(
            [
                [12, 4, 3, 1, 8.0],
                [64, 14, 9, 2, 18.0],
                [120, 42, 18, 3, 26.0],
                [220, 60, 32, 4, 36.0],
                [350, 100, 48, 5, 52.0],
            ],
            dtype=float,
        )
        training_targets = np.array([6.5, 14.0, 22.3, 31.4, 49.6], dtype=float)
        self.model.fit(training_features, training_targets)

    def predict(self, features: dict[str, float | int]) -> float:
        vector = np.array(
            [
                float(features.get("additions", 0)),
                float(features.get("deletions", 0)),
                float(features.get("changed_files", 0)),
                float(features.get("requested_reviewers_count", 0)),
                float(features.get("author_merge_hours", 0.0)),
            ],
            dtype=float,
        ).reshape(1, -1)
        return float(self.model.predict(vector)[0])
