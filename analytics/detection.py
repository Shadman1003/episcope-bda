"""Real-time eligible onset surveillance. Ground truth is deliberately absent here."""
from __future__ import annotations

import pandas as pd
import numpy as np


MATURITY_DAYS = 7
ALERT_SCHEMA = "date date, district_id string, disease string, observed long, expected double, threshold double, score double, alert boolean, method string, data_complete boolean, report_count long, as_of_date date, standard_deviation double, reporting_artefact_suspected boolean"


def cusum_scores(observed, expected, deviation, eligible=None):
    """One-sided standardized Page CUSUM; allowance k=.5, reset after missingness."""
    observed, expected, deviation = map(np.asarray, (observed, expected, deviation))
    eligible = np.ones(len(observed), bool) if eligible is None else np.asarray(eligible)
    out, value = [], 0.0
    for y, mu, sd, usable in zip(observed, expected, deviation, eligible):
        if not usable or not np.isfinite(mu) or not np.isfinite(sd):
            value = 0.0
        else:
            value = max(0.0, value + (y-mu)/max(1.0, sd) - .5)
        out.append(value)
    return np.asarray(out)


def detect(spark, daily, encounters=None):
    """Spark baseline windows followed by parallel per-series CUSUM recursion.

    Every onset cohort is observed at the same seven-day maturity. A record with
    report_date > onset_date+7 cannot enter that cohort's decision. Actual issue
    dates, rather than retrospective onset dates, are used by the evaluator.
    """
    from pyspark.sql import functions as F, Window
    max_date = daily.agg(F.max("date")).first()[0]
    base = daily.select("date", "district_id", "disease", "cases", "report_count", "completeness")
    if encounters is not None:
        eligible = encounters.where(
            (F.col("report_date") <= F.date_add("onset_date", MATURITY_DAYS)) &
            (F.col("report_date") >= F.col("onset_date"))
        ).groupBy("district_id", "disease", F.col("onset_date").alias("date")).agg(F.count("*").alias("observed"))
        base = base.join(eligible, ["district_id", "disease", "date"], "left").fillna({"observed": 0})
    else:
        raise ValueError("As-of detection requires silver encounters with onset_date and report_date")
    base = (base.withColumn("as_of_date", F.date_add("date", MATURITY_DAYS))
            .where(F.col("as_of_date") <= F.lit(max_date)))
    # A backfill can also depress the preceding short baseline. Detect that
    # reporting regime from the feed itself, rather than from injected truth.
    # The trailing pause is an explicit timeliness/false-alarm trade-off.
    reports = encounters.groupBy("district_id", F.col("report_date").alias("date")).agg(
        F.count("*").alias("reports"),
        F.avg((F.datediff("report_date", "onset_date") > 7).cast("double")).alias("late_share"))
    report_days = (daily.select("district_id", "date").distinct().join(reports, ["district_id", "date"], "left")
                   .fillna({"reports": 0, "late_share": 0.})
                   .withColumn("backfill_signal", ((F.col("reports") >= 10) & (F.col("late_share") > .5)).cast("int")))
    guard = Window.partitionBy("district_id").orderBy("date").rowsBetween(-7, 0)
    report_days = (report_days.withColumn("reporting_artefact_suspected", F.max("backfill_signal").over(guard) > 0)
                   .select("district_id", F.col("date").alias("as_of_date"), "reporting_artefact_suspected"))
    base = base.join(report_days, ["district_id", "as_of_date"], "left").fillna({"reporting_artefact_suspected": False})
    coverage = Window.partitionBy("district_id", "disease").orderBy("date").rowsBetween(-6, 0)
    base = base.withColumn("coverage_smoothed", F.avg("completeness").over(coverage))
    baseline = Window.partitionBy("district_id", "disease").orderBy("date").rowsBetween(-9, -3)
    scored = (base.withColumn("expected", F.avg("observed").over(baseline))
              .withColumn("standard_deviation", F.greatest(F.lit(1.), F.stddev_samp("observed").over(baseline)))
              .withColumn("baseline_n", F.count("observed").over(baseline))
              .withColumn("data_complete", (F.col("coverage_smoothed") >= .6) & (F.col("baseline_n") == 7) & (F.avg("coverage_smoothed").over(baseline) >= .6) & ~F.col("reporting_artefact_suspected"))
              .withColumn("score", (F.col("observed")-F.col("expected"))/F.col("standard_deviation"))
              .withColumn("threshold", F.col("expected")+3*F.col("standard_deviation"))
              .withColumn("alert", F.col("data_complete") & (F.col("score") > 3.))
              .withColumn("method", F.lit("EARS_C2")))
    columns = [name.split()[0] for name in ALERT_SCHEMA.split(", ")]
    ears = scored.select(*columns).fillna({"score": 0., "expected": 0.})

    def group_cusum(pdf):
        pdf = pdf.sort_values("date").copy()
        pdf["score"] = cusum_scores(pdf.observed, pdf.expected, pdf.standard_deviation, pdf.data_complete)
        previous = np.r_[0., pdf.score.to_numpy()[:-1]]
        pdf["threshold"] = pdf.expected + (5.5-previous)*pdf.standard_deviation
        pdf["alert"] = pdf.data_complete & (pdf.score > 5.)
        pdf["method"] = "CUSUM"
        return pdf[columns]

    cusum = ears.groupBy("district_id", "disease").applyInPandas(group_cusum, ALERT_SCHEMA)
    return ears.unionByName(cusum)
