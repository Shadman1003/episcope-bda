"""Entry point for reproducible analytics from the aggregate lake."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from pyspark.sql import functions as F

from analytics.detection import detect
from analytics.evaluate import evaluate_detection
from analytics.forecast import forecast, metrics
from analytics.optimise import demand_from_forecasts, scenario_grid, ALLOCATION_SCHEMA, SCENARIO_SCHEMA
from analytics.seasonal import build_calendar


def _fingerprint(path):
    h = hashlib.sha256()
    for file in sorted(path.rglob("*.parquet")):
        stat = file.stat()
        h.update(f"{file.relative_to(path)}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return h.hexdigest()


def run_analytics(spark, root: Path) -> dict:
    root = Path(root)
    if (root/"data"/"lake").exists():
        root = root/"data"
    gold = root/"lake"/"gold"
    results = root.parent/"results"
    results.mkdir(exist_ok=True, parents=True)
    started = time.monotonic()
    daily = spark.read.parquet(str(gold/"daily"))
    weekly = spark.read.parquet(str(gold/"weekly"))
    encounters = spark.read.parquet(str(root/"lake"/"silver"/"encounters"))
    source_hash = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()
    input_hash = _fingerprint(root/"lake"/"silver"/"encounters")
    timings, counts = {}, {}

    def write(df, name):
        from pipeline.common import publish
        publish(df.coalesce(8), root, "gold", name)
        counts[name] = spark.read.parquet(str(gold/name)).count()

    t = time.monotonic()
    alerts = detect(spark, daily, encounters)
    write(alerts, "alerts")
    alerts = spark.read.parquet(str(gold/"alerts"))
    timings["detection_seconds"] = time.monotonic()-t
    t = time.monotonic()
    tradeoff, detection_evaluation = evaluate_detection(spark, alerts, root, daily)
    write(tradeoff, "detection_tradeoff")
    tradeoff.toPandas().to_csv(results/"detection_tradeoff.csv", index=False)
    timings["detection_evaluation_seconds"] = time.monotonic()-t

    t = time.monotonic()
    latest_date = daily.agg(F.max("date")).first()[0]
    complete = weekly.where(F.date_add("week_start", 6) <= F.lit(latest_date))
    calendar_input = complete
    # Report-week cohorts let each Spark group reconstruct the exact counts that
    # were available at each historical Sunday forecast issue time.
    cohorts = (encounters.withColumn("week_start", F.to_date(F.date_trunc("week", "onset_date")))
               .withColumn("report_week", F.to_date(F.date_trunc("week", "report_date")))
               .withColumn("lag", (F.datediff("report_week", "week_start")/7).cast("int"))
               .groupBy("district_id", "disease", "week_start").agg(*[
                   F.sum(F.when(F.col("lag") == i, 1).otherwise(0)).cast("double").alias(f"report_lag{i}") for i in range(4)]))
    complete = complete.join(cohorts, ["district_id", "disease", "week_start"], "left").fillna({f"report_lag{i}": 0. for i in range(4)})
    all_predictions = forecast(complete).cache()
    forecasts = all_predictions.where("is_latest").drop("actual", "model", "is_latest")
    backtest = all_predictions.where("not is_latest").drop("is_latest")
    write(forecasts, "forecasts")
    write(backtest, "forecast_backtest")
    forecast_metrics = metrics(backtest)
    write(forecast_metrics, "forecast_metrics")
    forecast_metrics.toPandas().to_csv(results/"forecast_metrics.csv", index=False)
    overall = forecast_metrics.groupBy("model", "disease", "horizon").agg(
        F.sum("n").alias("n"),
        (F.sum(F.col("mae")*F.col("n"))/F.sum("n")).alias("mae"),
        (F.sum(F.col("coverage80")*F.col("n"))/F.sum("n")).alias("coverage80"),
        (F.sum(F.col("wis")*F.col("n"))/F.sum("n")).alias("wis"))
    overall.toPandas().to_csv(results/"forecast_summary.csv", index=False)
    all_predictions.unpersist()
    timings["forecast_seconds"] = time.monotonic()-t

    t = time.monotonic()
    forecasts = spark.read.parquet(str(gold/"forecasts"))
    districts = daily.select("district_id", "population", "division").dropDuplicates(["district_id"])
    stocks = spark.read.parquet(str(gold/"stock")) if (gold/"stock").exists() else None
    demand = demand_from_forecasts(forecasts, districts, stocks)
    demand.to_csv(results/"resource_demand.csv", index=False)
    primary, grid, scenario_summary = scenario_grid(demand)
    allocation_columns = [c.split()[0] for c in ALLOCATION_SCHEMA.split(", ")]
    write(spark.createDataFrame(primary[allocation_columns], ALLOCATION_SCHEMA), "allocations")
    write(spark.createDataFrame(grid[allocation_columns], ALLOCATION_SCHEMA), "allocation_grid")
    write(spark.createDataFrame(scenario_summary, SCENARIO_SCHEMA), "allocation_scenarios")
    scenario_summary.to_csv(results/"allocation_scenarios.csv", index=False)
    primary.to_csv(results/"recommended_allocation.csv", index=False)
    chosen = scenario_summary[(scenario_summary.equity_floor == .2) & (scenario_summary.budget == primary.budget.iloc[0])].iloc[0].to_dict()
    recommendation = {
        "budget": float(chosen["budget"]), "equity_floor": .2,
        "explanation": "Reserve at least 20% of each resource's allocation in proportion to population; prioritize remaining capacity by weighted unmet forecast demand.",
        "measured_scenario": chosen,
        "warning": "Generic budget units and teaching demand conversions require local validation. Continuous bed/team capacity is not a dispatch manifest.",
    }
    timings["optimisation_seconds"] = time.monotonic()-t

    t = time.monotonic()
    write(build_calendar(calendar_input), "calendar")
    timings["calendar_seconds"] = time.monotonic()-t
    provenance = {"analytics_source_sha256": source_hash, "input_manifest_sha256": input_hash,
                  "input_manifest_definition": "sorted silver parquet relative names, byte sizes and nanosecond mtimes; use bronze checksums for content audit",
                  "as_of": str(latest_date), "timings": timings, "rows": counts,
                  "detection": detection_evaluation, "allocation_recommendation": recommendation,
                  "forecast_backtest": {"target_weeks": 52, "label_maturity_weeks": 3,
                                        "interval": "80% expanding prequential absolute-error intervals; no future residuals",
                                        "note": "The last 52 fully mature target weeks, excluding the latest three weeks, are scored."},
                  "total_seconds": time.monotonic()-started}
    (results/"analytics_latest.json").write_text(json.dumps(provenance, indent=2, default=str), encoding="utf-8")
    return provenance


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data"))
    args = parser.parse_args()
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.appName("EpiScope analytics").getOrCreate()
    try:
        print(json.dumps(run_analytics(spark, args.root), indent=2, default=str))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
