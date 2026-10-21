"""Analytic invariants: availability, interval scoring and actual LP constraints."""
import unittest
import numpy as np
import pandas as pd

from analytics.detection import cusum_scores
from analytics.forecast import forecast_series, weighted_interval_score
from analytics.optimise import solve_allocation, RESOURCE_PARAMETERS
from analytics.seasonal import decompose_series


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.demand = pd.DataFrame([
            {"district_id": d, "division": division, "population": population, "resource": resource,
             "demand": need, "stock": 1.}
            for d, division, population, need in (("01", "East", 100., 10.), ("02", "West", 300., 90.))
            for resource in ("iv_saline", "beds")])

    def test_constraints_and_population_floor(self):
        transport = {"East": 20., "West": 80.}
        table, summary = solve_allocation(self.demand, budget=120., equity_floor=.4, transport=transport)
        self.assertLessEqual(summary["total_cost"], 120.+1e-6)
        self.assertTrue((table.allocation >= -1e-8).all())
        self.assertTrue((table.unmet+table.stock+table.allocation >= table.demand-1e-7).all())
        for resource, part in table.groupby("resource"):
            total = part.allocation.sum()
            for row in part.itertuples():
                self.assertGreaterEqual(row.allocation+1e-7, .4*(row.population/400)*total)
        for division, part in table.groupby("division"):
            volume = sum(r.allocation*RESOURCE_PARAMETERS[r.resource]["volume"] for r in part.itertuples())
            self.assertLessEqual(volume, transport[division]+1e-6)

    def test_zero_budget_and_no_shortage_are_valid(self):
        table, _ = solve_allocation(self.demand, 0., .8)
        self.assertAlmostEqual(table.allocation.sum(), 0.)
        safe = self.demand.copy()
        safe["stock"] = 1000.
        table, summary = solve_allocation(safe, 500., .5)
        self.assertAlmostEqual(table.allocation.sum(), 0.)
        self.assertAlmostEqual(summary["weighted_unmet"], 0.)

    def test_budget_increase_cannot_worsen_optimum(self):
        _, small = solve_allocation(self.demand, 60., .2)
        _, large = solve_allocation(self.demand, 120., .2)
        self.assertLessEqual(large["weighted_unmet"], small["weighted_unmet"]+1e-7)


def series_fixture(n=220):
    t = np.arange(n)
    cases = np.maximum(1., np.round(20+10*np.sin(t*2*np.pi/52)))
    return pd.DataFrame({"district_id": "01", "disease": "dengue", "division": "Dhaka",
                         "week_start": pd.date_range("2021-01-04", periods=n, freq="W-MON"),
                         "cases": cases, "admissions": np.round(cases*.2),
                         "rainfall_mm": 30+20*np.sin(t*2*np.pi/52), "temperature_c": 27., "humidity_pct": 70.,
                         "report_lag0": cases*.5, "report_lag1": cases*.3,
                         "report_lag2": cases*.15, "report_lag3": cases*.05})


class ForecastTests(unittest.TestCase):
    def test_rolling_origin_has_52_targets_and_valid_intervals(self):
        out = forecast_series(series_fixture())
        for (_, horizon), part in out[~out.is_latest].groupby(["model", "horizon"]):
            self.assertEqual(len(part), 52)
            self.assertTrue(((pd.to_datetime(part.week_start)-pd.to_datetime(part.origin)).dt.days == horizon*7).all())
        self.assertEqual(len(out[out.is_latest]), 4)
        self.assertTrue((out.lower80 <= out.prediction).all())
        self.assertTrue((out.upper80 >= out.prediction).all())
        self.assertTrue((out.lower80 >= 0.).all())

    def test_future_targets_and_weather_cannot_change_past_forecast(self):
        fixture = series_fixture()
        original = forecast_series(fixture)
        origin = pd.Timestamp(fixture.week_start.iloc[180]).date()
        changed = fixture.copy()
        future = changed.week_start > pd.Timestamp(origin)+pd.Timedelta(days=6)
        changed.loc[future, ["cases", "rainfall_mm", "temperature_c", "humidity_pct", "report_lag0", "report_lag1", "report_lag2", "report_lag3"]] = 99999.
        altered = forecast_series(changed)
        before = original[(original.origin == origin) & (original.model == "lagged_weather_ridge")]
        after = altered[(altered.origin == origin) & (altered.model == "lagged_weather_ridge")]
        self.assertEqual(len(before), 4)
        np.testing.assert_allclose(before.prediction, after.prediction)
        np.testing.assert_allclose(before.lower80, after.lower80)
        np.testing.assert_allclose(before.upper80, after.upper80)

    def test_late_report_cohort_cannot_change_earlier_origin_feature(self):
        fixture = series_fixture()
        original = forecast_series(fixture)
        # Change same-week count labels and reports arriving three weeks later.
        # The origin's training labels are mature and its features exclude lag3.
        origin = pd.Timestamp(fixture.week_start.iloc[180]).date()
        fixture.loc[180, "cases"] += 10000.
        fixture.loc[180, "report_lag3"] += 10000.
        altered = forecast_series(fixture)
        a = original[(original.origin == origin) & (original.model == "lagged_weather_ridge")]
        b = altered[(altered.origin == origin) & (altered.model == "lagged_weather_ridge")]
        np.testing.assert_allclose(a.prediction, b.prediction)
        np.testing.assert_allclose(a.upper80, b.upper80)

    def test_interval_score_penalizes_missed_truth(self):
        self.assertAlmostEqual(float(weighted_interval_score(10., 10., 8., 12.)), .4/1.5)
        self.assertGreater(float(weighted_interval_score(30., 10., 8., 12.)), float(weighted_interval_score(10., 10., 8., 12.)))


class DetectionAndSeasonTests(unittest.TestCase):
    def test_cusum_accumulates_and_resets_after_unavailable_data(self):
        scores = cusum_scores([1, 4, 4, 4], [1, 1, 1, 1], [1, 1, 1, 1], [True, True, False, True])
        np.testing.assert_allclose(scores, [0., 2.5, 0., 2.5])

    def test_calendar_is_derived_and_next_year(self):
        calendar = decompose_series(series_fixture(260))
        self.assertEqual(len(calendar), 53)
        self.assertEqual(set(calendar.year), {2026})
        self.assertGreater(calendar.seasonal_index.max(), calendar.seasonal_index.min())
        self.assertTrue(calendar.action.str.contains("surveillance|surge|supplies").all())


if __name__ == "__main__":
    unittest.main()
