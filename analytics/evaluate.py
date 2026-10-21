"""Evaluation boundary: this is the ONLY analytics module that reads truth files."""
from __future__ import annotations

import json
from pathlib import Path
from pyspark import StorageLevel
from pyspark.sql import functions as F, Window


TRADEOFF_SCHEMA = "method string, threshold_multiplier double, sensitivity double, false_alerts_per_district_year double, median_days_to_detection double, events long, detected_events long, false_alert_episodes long, evaluation_period string, district_event_sensitivity double"


def evaluate_detection(spark, alerts, root: Path, daily=None):
    """Threshold curves are retrospective evaluations, never detector features.

    Alert episodes begin on the first alert after seven quiet days. Nationwide
    events count once in event sensitivity; district-event sensitivity also
    reports how broadly the national event was detected. A timely detection must
    be issued during the true event, not only have an onset date inside it.
    """
    truth_path = root/"truth"/"ground_truth_outbreaks.csv"
    if not truth_path.exists():
        return spark.createDataFrame([], TRADEOFF_SCHEMA), {"status": "truth unavailable; no metrics invented"}
    truth = (spark.read.option("header", True).csv(str(truth_path))
             .withColumn("start_date", F.to_date("start_date"))
             .withColumn("end_date", F.to_date("end_date"))
             .select("event_id", "district_id", "disease", "start_date", "end_date"))
    threshold_rows = [("EARS_C2", float(t)) for t in (.5, 1, 1.5, 2, 2.5, 3, 4, 5)]
    threshold_rows += [("CUSUM", float(t)) for t in (2, 3, 4, 5, 7, 10)]
    thresholds = spark.createDataFrame(threshold_rows, "method string, threshold_multiplier double")
    # For any threshold h, "no eligible score exceeded h in the last seven
    # days" is exactly "the maximum eligible score was <= h". Compute that
    # threshold-independent window BEFORE expanding the grid. Then retain only
    # actual triggers. This avoids sorting/caching millions of non-alert rows.
    history = Window.partitionBy("district_id", "disease", "method").orderBy("date").rowsBetween(-7, -1)
    scored = (alerts.withColumn("previous_max", F.max(F.when(F.col("data_complete"), F.col("score"))).over(history))
              .where(F.col("data_complete") & (F.col("score") > F.when(F.col("method") == "EARS_C2", F.lit(.5)).otherwise(F.lit(2.))))
              .join(F.broadcast(thresholds), "method")
              .where(F.col("score") > F.col("threshold_multiplier"))
              .withColumn("trigger", F.lit(True))
              .withColumn("episode", F.coalesce(F.col("previous_max"), F.lit(-1e300)) <= F.col("threshold_multiplier"))
              .persist(StorageLevel.DISK_ONLY))
    max_date = alerts.agg(F.max("as_of_date")).first()[0]
    min_date = alerts.agg(F.min("as_of_date")).first()[0]
    split = max_date.replace(year=max_date.year, month=1, day=1)
    output, summary = [], {}
    for period in ("calibration", "holdout", "all"):
        signal = scored if period == "all" else scored.where(F.col("as_of_date") < F.lit(split) if period == "calibration" else F.col("as_of_date") >= F.lit(split))
        events = truth if period == "all" else truth.where(F.col("start_date") < F.lit(split) if period == "calibration" else F.col("start_date") >= F.lit(split))
        events = events.where((F.col("start_date") >= F.lit(min_date)) & (F.col("end_date") <= F.lit(max_date)))
        event_count = events.select("event_id").distinct().count()
        district_event_count = events.select("event_id", "district_id").distinct().count()
        exposure_rows = alerts if period == "all" else alerts.where(F.col("as_of_date") < F.lit(split) if period == "calibration" else F.col("as_of_date") >= F.lit(split))
        bounds = exposure_rows.agg(F.min("as_of_date"), F.max("as_of_date"), F.countDistinct("district_id")).first()
        years = max(1/365.2425, (bounds[1]-bounds[0]).days/365.2425) if bounds[0] else 1.
        exposure = max(1., bounds[2]*years)
        # False alerts are checked against every true outbreak, including one
        # crossing the calibration/holdout boundary. Only evaluable events enter
        # the sensitivity denominator and its corresponding hits.
        evaluable = events.select("event_id", "district_id").distinct().withColumn("evaluable", F.lit(True))
        matching_truth = truth.join(evaluable, ["event_id", "district_id"], "left").fillna({"evaluable": False})
        a, e = signal.alias("a"), matching_truth.alias("e")
        condition = ((F.col("a.district_id") == F.col("e.district_id")) &
                     (F.col("a.disease") == F.col("e.disease")) &
                     (F.col("a.date") >= F.col("e.start_date")) &
                     (F.col("a.date") <= F.col("e.end_date")))
        joined = a.join(F.broadcast(e), condition, "left").select(
            "a.*", F.col("e.event_id").alias("event_id"), F.col("e.start_date").alias("event_start"), F.col("e.end_date").alias("event_end"), F.col("e.evaluable").alias("evaluable"))
        grouping = ["method", "threshold_multiplier"]
        false = joined.where(F.col("episode") & F.col("event_id").isNull()).groupBy(*grouping).agg(F.count("*").alias("false_alert_episodes"))
        detections = joined.where(F.col("trigger") & F.col("evaluable") & F.col("event_id").isNotNull() & (F.col("as_of_date") <= F.col("event_end")))
        district_hits = detections.select(*grouping, "event_id", "district_id").distinct().groupBy(*grouping).count().withColumnRenamed("count", "district_hits")
        hits = (detections.groupBy(*grouping, "event_id").agg(F.min(F.datediff("as_of_date", "event_start")).alias("delay"))
                .groupBy(*grouping).agg(F.count("*").alias("detected_events"), F.percentile_approx("delay", .5).cast("double").alias("median_days_to_detection")))
        result = (thresholds.join(false, grouping, "left").join(hits, grouping, "left").join(district_hits, grouping, "left")
                  .fillna({"false_alert_episodes": 0, "detected_events": 0, "district_hits": 0})
                  .withColumn("sensitivity", F.col("detected_events")/event_count if event_count else F.lit(None).cast("double"))
                  .withColumn("district_event_sensitivity", F.col("district_hits")/district_event_count if district_event_count else F.lit(None).cast("double"))
                  .withColumn("false_alerts_per_district_year", F.col("false_alert_episodes")/exposure)
                  .withColumn("events", F.lit(event_count).cast("long"))
                  .withColumn("evaluation_period", F.lit(period)).select(*[c.split()[0] for c in TRADEOFF_SCHEMA.split(", ")]))
        output.extend([tuple(row) for row in result.collect()])
    scored.unpersist()
    curve = spark.createDataFrame(output, TRADEOFF_SCHEMA)
    recommended = (curve.where((F.col("evaluation_period") == "calibration") & (F.col("false_alerts_per_district_year") <= 5.))
                   .orderBy(F.desc("sensitivity"), "false_alerts_per_district_year").limit(1).collect())
    summary["calibration_recommendation"] = recommended[0].asDict() if recommended else None
    summary["operational_default"] = {"EARS_C2": 3., "CUSUM": 5.}
    summary["timeliness"] = "7-day mature onset cohorts; detection delay measured on actual issue date"
    summary["false_alert_definition"] = "new alert episode after 7 quiet days; pooled across diseases per district-year"
    summary["period_split"] = str(split)
    summary["artifact"] = evaluate_reporting_artefact(spark, alerts, root, daily)
    return curve, summary


def evaluate_reporting_artefact(spark, alerts, root, daily=None):
    path = root/"truth"/"reporting_artefacts.csv"
    if not path.exists():
        return {"status": "artefact truth unavailable"}
    artefacts = spark.read.option("header", True).csv(str(path))
    if not artefacts.take(1):
        return {"status": "no injected artefacts"}
    periods = artefacts.select("district_id", F.to_date("backfill_date").alias("backfill_date")).distinct()
    matches = alerts.join(F.broadcast(periods), "district_id").where(
        (F.col("as_of_date") >= F.col("backfill_date")) &
        (F.col("as_of_date") <= F.date_add("backfill_date", 7)))
    details = [r.asDict() for r in matches.groupBy("method").agg(
        F.sum(F.col("alert").cast("int")).alias("alerts_in_backfill_window"),
        F.sum("observed").alias("eligible_onset_cases")).collect()]
    naive_details = []
    if daily is not None:
        past = Window.partitionBy("district_id", "disease").orderBy("date").rowsBetween(-9, -3)
        naive = (daily.select("district_id", "disease", "date", "report_count")
                 .withColumn("mu", F.avg("report_count").over(past))
                 .withColumn("sd", F.greatest(F.lit(1.), F.stddev_samp("report_count").over(past)))
                 .withColumn("naive_alert", F.col("report_count") > F.col("mu")+3*F.col("sd")))
        naive = naive.join(F.broadcast(periods), "district_id").where(
            (F.col("date") >= F.col("backfill_date")) & (F.col("date") <= F.date_add("backfill_date", 7)))
        naive_details = [r.asDict() for r in naive.groupBy("district_id").agg(
            F.sum(F.col("naive_alert").cast("int")).alias("naive_report_date_c2_alerts"),
            F.sum(F.when(F.col("date") == F.col("backfill_date"), F.col("report_count")).otherwise(0)).alias("backfill_day_report_cases"),
            F.max("report_count").alias("largest_disease_day_report_count")).collect()]
    return {"status": "measured", "details": details,
            "naive_report_date_comparator": naive_details,
            "interpretation": "Backfilled reports older than seven onset days cannot enter any as-of signal. Alerts from genuine concurrent onset variation may remain; zero is not assumed."}
