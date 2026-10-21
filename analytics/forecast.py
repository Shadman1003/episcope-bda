"""Direct multi-horizon ridge forecasting, parallelized by Spark district/disease."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd


FORECAST_ALL_SCHEMA = "district_id string, disease string, origin date, week_start date, horizon int, prediction double, lower80 double, upper80 double, baseline double, admission_rate double, predicted_admissions double, actual double, model string, is_latest boolean, issued_on date"
MODEL_NAME = "lagged_weather_ridge"


def interval_score(actual, lower, upper, alpha=.2):
    y, lo, hi = map(np.asarray, (actual, lower, upper))
    return hi-lo + 2/alpha*np.maximum(lo-y, 0) + 2/alpha*np.maximum(y-hi, 0)


def weighted_interval_score(actual, median, lower, upper, alpha=.2):
    """Proper WIS for a median and one central interval (K=1)."""
    return (.5*np.abs(np.asarray(actual)-median) + alpha/2*interval_score(actual, lower, upper, alpha))/1.5


def _ridge_fit_predict(x, y, query, penalty=8.):
    means = np.mean(x, axis=0)
    scales = np.std(x, axis=0)
    scales[scales < 1e-8] = 1.
    z = np.column_stack([np.ones(len(x)), (x-means)/scales])
    q = np.r_[1., (query-means)/scales]
    regularizer = np.eye(z.shape[1])*penalty
    regularizer[0, 0] = 0.
    weights = np.linalg.solve(z.T@z+regularizer, z.T@np.log1p(y))
    return max(0., float(np.expm1(np.clip(q@weights, 0, 20))))


def forecast_series(pdf):
    """Use only outcomes/weather available at each origin; final 52 target weeks held out."""
    pdf = pdf.sort_values("week_start").reset_index(drop=True)
    n = len(pdf)
    if n < 112:
        raise ValueError("At least 112 complete weeks are needed for honest seasonal backtests")
    y = pdf.cases.to_numpy(dtype=float)
    weather = pdf[["rainfall_mm", "temperature_c", "humidity_pct"]].to_numpy(dtype=float)
    # Missing weather is encoded explicitly. Imputation constants are neutral and
    # train-only standardization remains inside each expanding fit.
    weather_missing = ~np.isfinite(weather)
    weather = np.nan_to_num(weather, nan=0.)
    dates = pd.to_datetime(pdf.week_start)
    lag_columns = [f"report_lag{i}" for i in range(4)]
    snapshots = pdf[lag_columns].to_numpy(float) if all(c in pdf for c in lag_columns) else None
    def known_cases(week, origin):
        if snapshots is None:
            raise ValueError("Forecasting requires report-lag cohorts to reconstruct origin availability")
        return float(snapshots[week, :min(4, origin-week+1)].sum())
    # Three extra weeks allow the specified maximum 21-day report delay to elapse.
    scored_end = n-3
    rows = []
    for horizon in range(1, 5):
        def features(origin):
            target = origin+horizon
            target_date = dates.iloc[origin]+pd.Timedelta(weeks=horizon)
            phase = 2*math.pi*(target_date.dayofyear/365.2425)
            lags = [max(0, target-lag) for lag in (4, 6, 8)]
            recent = [known_cases(j, origin) for j in range(origin-3, origin+1)]
            return np.r_[np.log1p(known_cases(origin, origin)), np.log1p(known_cases(origin-1, origin)),
                         np.log1p(known_cases(origin-3, origin)), np.log1p(known_cases(origin-7, origin)),
                         np.log1p(np.mean(recent)),
                         math.sin(phase), math.cos(phase), math.sin(2*phase), math.cos(2*phase),
                         weather[lags].ravel(), weather_missing[lags].ravel().astype(float)]

        origins = list(range(8, n-horizon))
        x_all = np.asarray([features(i) for i in origins])
        labels = np.asarray([y[i+horizon] for i in origins])
        target_index = np.asarray(origins)+horizon
        calibration, baseline_calibration = [], []
        # A full year before scored targets supplies strictly past prediction errors.
        start = max(68, n-104-horizon)
        for origin in range(start, n):
            train_mask = target_index <= origin-3
            if train_mask.sum() < 52:
                continue
            prediction = _ridge_fit_predict(x_all[train_mask], labels[train_mask], features(origin))
            target = origin+horizon
            seasonal_index = target-52
            seasonal = float(y[seasonal_index]) if seasonal_index >= 0 else float(y[:origin+1].mean())
            past = [err for t, err in calibration if t <= origin-3]
            past_baseline = [err for t, err in baseline_calibration if t <= origin-3]
            # Before enough prequential residuals, use only training residuals to a
            # past-value seasonal predictor; never peek at future holdout errors.
            if len(past) < 12:
                safe_indices = np.arange(52, origin-2)
                historical = np.abs(y[safe_indices]-y[safe_indices-52])
                radius = float(np.quantile(historical, .8)) if len(historical) else 1.
            else:
                radius = float(np.quantile(past, .8, method="higher"))
            baseline_radius = (float(np.quantile(past_baseline, .8, method="higher"))
                               if len(past_baseline) >= 12 else radius)
            radius, baseline_radius = max(1., radius), max(1., baseline_radius)
            admission_rate = float(np.clip(pdf.admissions.iloc[:origin-2].sum()/max(1., y[:origin-2].sum()), 0., 1.))
            target_date = (dates.iloc[origin]+pd.Timedelta(weeks=horizon)).date()
            actual = float(y[target]) if target < n else float("nan")
            if scored_end-52 <= target < scored_end:
                for model, pred, rad in ((MODEL_NAME, prediction, radius), ("seasonal_naive", seasonal, baseline_radius)):
                    rows.append((str(pdf.district_id.iloc[0]), str(pdf.disease.iloc[0]), dates.iloc[origin].date(),
                                 target_date, horizon, pred, max(0., pred-rad), pred+rad, seasonal,
                                 admission_rate, pred*admission_rate, actual, model, False, (dates.iloc[origin]+pd.Timedelta(days=6)).date()))
            if origin == n-1:
                rows.append((str(pdf.district_id.iloc[0]), str(pdf.disease.iloc[0]), dates.iloc[origin].date(),
                             target_date, horizon, prediction, max(0., prediction-radius), prediction+radius,
                             seasonal, admission_rate, prediction*admission_rate, actual, MODEL_NAME, True, (dates.iloc[origin]+pd.Timedelta(days=6)).date()))
            if target < n:
                calibration.append((target, abs(actual-prediction)))
                baseline_calibration.append((target, abs(actual-seasonal)))
    names = [c.split()[0] for c in FORECAST_ALL_SCHEMA.split(", ")]
    return pd.DataFrame(rows, columns=names)


def forecast(weekly):
    return weekly.groupBy("district_id", "disease").applyInPandas(forecast_series, FORECAST_ALL_SCHEMA)


def metrics(backtest):
    from pyspark.sql import functions as F
    absolute = F.abs(F.col("actual")-F.col("prediction"))
    score = (F.col("upper80")-F.col("lower80") +
             10*F.greatest(F.col("lower80")-F.col("actual"), F.lit(0.)) +
             10*F.greatest(F.col("actual")-F.col("upper80"), F.lit(0.)))
    return backtest.groupBy("district_id", "disease", "horizon", "model").agg(
        F.count("*").alias("n"), F.avg(absolute).alias("mae"),
        F.avg(((F.col("actual") >= F.col("lower80")) & (F.col("actual") <= F.col("upper80"))).cast("double")).alias("coverage80"),
        F.avg((.5*absolute+.1*score)/1.5).alias("wis"))
