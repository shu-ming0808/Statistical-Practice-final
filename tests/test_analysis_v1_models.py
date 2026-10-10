"""Statistical contracts for temporal selection and convex stacking.

Synthetic complete events avoid loading any private ridership snapshot.  The
small XGBoost grid exercises the real estimators while keeping this suite fast.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from analysis_v1.models import (  # noqa: E402
    BASE_MODELS, Encoder, Trainer, event_mae, fit_one, solve_stack,
    temporal_splits,
)


def synthetic_events(last_year=2024):
    rows = []
    for year in range(2017, last_year + 1):
        event_date = pd.Timestamp(year, 7, 10 + (year % 5))
        for station_id, station in enumerate(("北門", "大橋頭", "雙連", "民權西路")):
            for hour in range(18, 24):
                t = year - 2017
                baseline = 20 + 7 * station_id + 3 * (hour - 18) + 2 * t
                baseline += 2 * ((station_id + hour + t) % 5)
                weekend = int(event_date.dayofweek >= 5)
                y = 2.2 * baseline + 30 + 11 * weekend + 3 * station_id * (hour - 18)
                rows.append({
                    "event_date": event_date, "year": year, "station": station,
                    "hour": hour, "baseline_hourly_56d": float(baseline),
                    "is_weekend": weekend, "day_of_year": event_date.dayofyear,
                    "year_index": t, "y": float(y),
                })
    return pd.DataFrame(rows)


def small_config():
    return {
        "seed": 7349, "outer_years": [2023, 2024],
        "min_inner_train_events": 2, "min_base_train_events": 4,
        "min_inner_folds": 2, "max_forward_groups": 1,
        "min_forward_improvement": 0.0,
        "candidate_groups": ["is_weekend", "year_index"],
        "ridge_lambdas": [0.01, 0.3],
        "xgb_grid": [{"max_depth": 1, "n_estimators": 3},
                     {"max_depth": 2, "n_estimators": 3}],
        "xgb_fixed": {"learning_rate": 0.05, "min_child_weight": 4,
                      "reg_lambda": 10.0, "subsample": 1.0,
                      "colsample_bytree": 1.0},
    }


class StackOptimizationTest(unittest.TestCase):
    def test_simplex_recovers_exact_feasible_mixture(self):
        p = np.array([[10., 40., 70.], [15., 65., 100.], [5., 80., 120.]])
        expected = np.array([0.2, 0.3, 0.5])
        y = p @ expected
        weights, info = solve_stack(p, y, ["event-a", "event-b", "event-c"])
        self.assertTrue(np.all(weights >= 0))
        self.assertAlmostEqual(weights.sum(), 1.0, places=10)
        np.testing.assert_allclose(p @ weights, y, atol=1e-7)
        self.assertAlmostEqual(info["oof_mae"], 0.0, places=7)
        self.assertEqual(info["scope"], "meta_training_loss_not_outer_test_score")

    def test_event_balance_does_not_favor_event_with_more_rows(self):
        # Row-weighted MAE would choose 0; equal-event MAE must choose 20.
        dates = ["a"] * 9 + ["b", "c"]
        y = np.array([0.] * 9 + [20., 20.])
        p = np.tile([0., 10., 20.], (len(y), 1))
        weights, info = solve_stack(p, y, dates)
        np.testing.assert_allclose(p @ weights, 20., atol=1e-8)
        self.assertAlmostEqual(info["oof_mae"], 20 / 3)
        frame = pd.DataFrame({"event_date": dates, "y": y})
        self.assertAlmostEqual(event_mae(frame, p @ weights), info["oof_mae"])
        for column in p.T:
            self.assertLessEqual(info["oof_mae"], event_mae(frame, column) + 1e-7)

    def test_rejects_nonfinite_negative_and_wrong_number_of_models(self):
        bad_inputs = [np.ones((2, 2)), np.array([[np.nan, 2, 3], [1, 2, 3]]),
                      np.array([[-1., 2, 3], [1, 2, 3]])]
        for p in bad_inputs:
            with self.subTest(predictions=p):
                with self.assertRaises(ValueError):
                    solve_stack(p, [1., 2.], ["a", "b"])


class PreprocessingAndRidgeTest(unittest.TestCase):
    def test_encoder_statistics_only_use_its_training_subset(self):
        frame = synthetic_events(2021)
        train = frame.loc[frame.year < 2021]
        valid = frame.loc[frame.year == 2021].copy()
        valid["baseline_hourly_56d"] += 100_000
        encoder = Encoder().fit(train, ("year_index", "is_weekend"))
        self.assertEqual(encoder.mean["baseline_hourly_56d"], train.baseline_hourly_56d.mean())
        np.testing.assert_allclose(encoder.scale["baseline_hourly_56d"],
                                   train.baseline_hourly_56d.std(ddof=0))
        original_mean = encoder.mean.copy()
        original_scale = encoder.scale.copy()
        x = encoder.transform(valid)
        j = encoder.names.index("baseline_hourly_56d")
        expected = ((valid.baseline_hourly_56d - train.baseline_hourly_56d.mean()) /
                    train.baseline_hourly_56d.std(ddof=0))
        np.testing.assert_allclose(x[:, j], expected)
        pd.testing.assert_series_equal(encoder.mean, original_mean)
        pd.testing.assert_series_equal(encoder.scale, original_scale)
        self.assertEqual(encoder.mean["is_weekend"], 0)
        self.assertEqual(encoder.scale["is_weekend"], 1)

    def test_structural_unknowns_and_post_event_candidates_are_rejected(self):
        frame = synthetic_events(2020)
        encoder = Encoder().fit(frame, ())
        unseen = frame.iloc[:1].copy()
        unseen["station"] = "新車站"
        with self.assertRaisesRegex(ValueError, "Unseen station"):
            encoder.transform(unseen)
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            Encoder().fit(frame, ("observed_weather",))

    def test_ridge_uses_n_lambda_and_does_not_penalize_intercept(self):
        frame = synthetic_events(2020)
        lam = 0.3
        fit = fit_one(frame, "ridge", ["year_index"], {"lambda": lam}, small_config())
        self.assertEqual(fit.estimator.alpha, len(frame) * lam)
        self.assertEqual(fit.params["sklearn_alpha"], len(frame) * lam)
        x = fit.encoder.transform(frame)
        xc = x - x.mean(axis=0)
        yc = frame.y.to_numpy() - frame.y.mean()
        beta = np.linalg.solve(xc.T @ xc + len(frame) * lam * np.eye(x.shape[1]), xc.T @ yc)
        intercept = frame.y.mean() - x.mean(axis=0) @ beta
        np.testing.assert_allclose(fit.estimator.coef_, beta, rtol=1e-8, atol=1e-8)
        self.assertAlmostEqual(fit.estimator.intercept_, intercept, places=8)
        # Replication should not change a mean-SSE penalty strength.
        twice = pd.concat([frame, frame], ignore_index=True)
        fit_twice = fit_one(twice, "ridge", ["year_index"], {"lambda": lam}, small_config())
        self.assertEqual(fit_twice.estimator.alpha, 2 * len(frame) * lam)
        np.testing.assert_allclose(fit.predict(frame), fit_twice.predict(frame), rtol=1e-9, atol=1e-8)


class TemporalTrainingTest(unittest.TestCase):
    def test_year_splits_keep_whole_events_and_only_earlier_training(self):
        frame = synthetic_events(2024)
        # Add a second complete event in a year to test year blocking too.
        extra = frame.loc[frame.year == 2021].copy()
        extra["event_date"] += pd.Timedelta(days=7)
        frame = pd.concat([frame, extra], ignore_index=True).sample(frac=1, random_state=7)
        splits = list(temporal_splits(frame, min_events=2))
        self.assertTrue(splits)
        for year, train, valid in splits:
            self.assertTrue((train.year < year).all())
            self.assertTrue((valid.year == year).all())
            self.assertLess(train.event_date.max(), valid.event_date.min())
            self.assertFalse(set(train.event_date) & set(valid.event_date))
            self.assertTrue(valid.groupby("event_date").size().eq(24).all())
            self.assertEqual(set(valid.event_date), set(frame.loc[frame.year == year, "event_date"]))

    def test_future_labels_do_not_change_earlier_oof_predictions(self):
        frame = synthetic_events(2023)
        changed = frame.copy()
        changed.loc[changed.year >= 2022, "y"] += 10_000
        before = Trainer(small_config()).build_meta(frame)
        after = Trainer(small_config()).build_meta(changed)
        keys = ["event_date", "station", "hour"]
        early_before = before.loc[before.year <= 2022].sort_values(keys).reset_index(drop=True)
        early_after = after.loc[after.year <= 2022].sort_values(keys).reset_index(drop=True)
        self.assertGreater(len(early_before), 0)
        np.testing.assert_allclose(early_before[list(BASE_MODELS)], early_after[list(BASE_MODELS)],
                                   rtol=0, atol=1e-9)
        self.assertFalse(np.allclose(early_before.y, early_after.y))
        self.assertTrue((pd.to_datetime(before.base_train_end) < before.event_date).all())

    def test_outer_truth_cannot_change_weights_or_predictions(self):
        frame = synthetic_events(2024)
        changed = frame.copy()
        changed.loc[changed.year == 2024, "y"] += 500_000
        pred1, meta1, _, _ = Trainer(small_config()).backtest(frame, progress=lambda _: None)
        pred2, meta2, _, _ = Trainer(small_config()).backtest(changed, progress=lambda _: None)
        for before, after in zip(meta1, meta2):
            self.assertEqual(before["outer_year"], after["outer_year"])
            self.assertEqual(before["weights"], after["weights"])
            self.assertEqual(before["base_models"], after["base_models"])
        columns = ["baseline", *BASE_MODELS, "equal", "stack"]
        np.testing.assert_allclose(pred1[columns], pred2[columns], rtol=0, atol=1e-9)
        self.assertFalse(np.allclose(pred1.y, pred2.y))

    def test_cache_does_not_silently_reuse_changed_training_labels(self):
        frame = synthetic_events(2020)
        changed = frame.copy()
        changed["y"] += 100
        trainer = Trainer(small_config())
        original = trainer.fit_bundle(frame)["ols"].predict(frame)
        refreshed = trainer.fit_bundle(changed)["ols"].predict(frame)
        self.assertFalse(np.allclose(original, refreshed),
                         "A cache keyed only by event dates hides revised training labels")


if __name__ == "__main__":
    unittest.main()
