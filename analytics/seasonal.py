"""Division-level log-additive seasonal decomposition and preparedness actions."""
from __future__ import annotations

import numpy as np
import pandas as pd


CALENDAR_SCHEMA = "division string, disease string, epi_week int, seasonal_index double, action string, year int"


def decompose_series(pdf):
    pdf = pdf.sort_values("week_start").copy()
    log_cases = np.log1p(pdf.cases.to_numpy(float))
    # A centered annual moving mean estimates trend; remaining log residuals
    # are averaged by ISO week and centered to form a multiplicative index.
    trend = pd.Series(log_cases).rolling(53, center=True, min_periods=26).mean().to_numpy()
    residual = log_cases-trend
    work = pd.DataFrame({"week": pd.to_datetime(pdf.week_start).dt.isocalendar().week.astype(int), "residual": residual})
    seasons = work.groupby("week").residual.mean().reindex(range(1, 54)).interpolate().ffill().bfill()
    seasons -= seasons.mean()
    indices = np.exp(seasons.to_numpy())
    next_year = pd.to_datetime(pdf.week_start).max().year+1
    records = []
    for week in range(1, 54):
        imminent = max(indices[(week+lead-1) % 53] for lead in (2, 3, 4))
        current = indices[week-1]
        action = ("Maintain surge readiness" if current >= 1.2 else
                  "Pre-position supplies; start prevention campaign" if imminent >= 1.2 else
                  "Routine surveillance and stock review")
        records.append((str(pdf.division.iloc[0]), str(pdf.disease.iloc[0]), week, float(current), action, int(next_year)))
    return pd.DataFrame(records, columns=[c.split()[0] for c in CALENDAR_SCHEMA.split(", ")])


def build_calendar(weekly):
    from pyspark.sql import functions as F
    return (weekly.groupBy("division", "disease", "week_start").agg(F.sum("cases").alias("cases"))
            .groupBy("division", "disease").applyInPandas(decompose_series, CALENDAR_SCHEMA))
