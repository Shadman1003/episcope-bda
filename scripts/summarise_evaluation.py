#!/usr/bin/env python3
"""Summarise completed EpiScope evaluations using only the Python standard library.

Reads national aggregate products, never encounter files, district metric tables,
Parquet, Spark or a browser. Missing required inputs fail rather than create a
success-looking partial summary. Missing optional generation metadata is explicit.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys


EXPECTED_DISEASES = {"dengue", "diarrhoea", "influenza", "malaria"}
EXPECTED_HORIZONS = {1, 2, 3, 4}
BASELINE = "seasonal_naive"
FORECAST_NUMBERS = {"horizon", "n", "mae", "coverage80", "wis"}
DETECTION_NUMBERS = {"threshold_multiplier", "sensitivity", "false_alerts_per_district_year",
                     "median_days_to_detection", "events", "detected_events", "false_alert_episodes",
                     "district_event_sensitivity"}
ALLOCATION_NUMBERS = {"budget", "equity_floor", "weighted_unmet", "baseline_weighted_unmet", "unmet",
                      "total_cost", "min_population_ratio", "benefit_vs_baseline_pct", "transport_utilisation"}
INTEGER_FIELDS = {"horizon", "n", "events", "detected_events", "false_alert_episodes"}


class EvaluationInputs:
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root is not None else None
        self.sources = []
        self.warnings = []

    def read_bytes(self, path):
        content = path.read_bytes()
        # Portable evidence should not publish the current user's absolute path.
        resolved = path.resolve()
        relative = resolved.relative_to(self.root).as_posix() if self.root and resolved.is_relative_to(self.root) else path.name
        self.sources.append({"path": relative, "bytes": len(content),
                             "sha256": hashlib.sha256(content).hexdigest()})
        return content

    def json(self, path):
        value = json.loads(self.read_bytes(path).decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected a JSON object: {path}")
        return value

    def table(self, path, numbers, required):
        reader = csv.DictReader(io.StringIO(self.read_bytes(path).decode("utf-8-sig")))
        missing = set(required)-set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
        result = []
        for line, row in enumerate(reader, 2):
            # Retain only the documented aggregate schema, excluding any future
            # accidentally attached identifiers or explanatory free-text columns.
            record = {key: (row.get(key) or "").strip() for key in sorted(required)}
            for key in numbers & set(required):
                text = record[key]
                if text == "":
                    record[key] = None
                    continue
                value = float(text)
                if not math.isfinite(value):
                    raise ValueError(f"{path.name}:{line}: non-finite {key}")
                if key in INTEGER_FIELDS:
                    if not value.is_integer():
                        raise ValueError(f"{path.name}:{line}: {key} must be integral")
                    value = int(value)
                record[key] = value
            result.append(record)
        return result


def _same_number(a, b):
    return a is not None and b is not None and math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def _comparison(candidate, baseline):
    """Preserve signed measured changes and distinguish roundoff from a gain."""
    if candidate is None or baseline is None:
        return {"direction": "unavailable", "relative_reduction_pct": None}
    difference = baseline-candidate
    direction = ("numerically_equivalent" if _same_number(candidate, baseline) else
                 "lower" if candidate < baseline else "higher")
    return {"direction": direction, "baseline_minus_candidate": difference,
            "relative_reduction_pct": 100*difference/baseline if baseline != 0 else None}


def _matching(rows, method, threshold, period):
    matches = [row for row in rows if row["method"] == method and row["evaluation_period"] == period
               and _same_number(row["threshold_multiplier"], threshold)]
    if len(matches) > 1:
        raise ValueError(f"Duplicate detection aggregate for {method}, {threshold}, {period}")
    return matches[0] if matches else None


def summarise_detection(analytics, curve, inputs):
    source = analytics.get("detection", {})
    recommendation = source.get("calibration_recommendation")
    matched = {}
    if recommendation:
        for period in ("calibration", "holdout", "all"):
            matched[period] = _matching(curve, recommendation.get("method"),
                                        recommendation.get("threshold_multiplier"), period)
        if matched.get("calibration") is None:
            inputs.warnings.append("The recorded detection recommendation has no matching calibration CSV row.")
        else:
            for key in DETECTION_NUMBERS:
                if key in recommendation:
                    recorded, actual = recommendation[key], matched["calibration"].get(key)
                    if not ((recorded is None and actual is None) or _same_number(recorded, actual)):
                        inputs.warnings.append(f"Recorded detection recommendation differs from calibration CSV field {key}; inputs may come from different runs.")
    event_counts = {}
    for period in sorted({row["evaluation_period"] for row in curve}):
        counts = {row["events"] for row in curve if row["evaluation_period"] == period}
        if len(counts) != 1:
            raise ValueError(f"Inconsistent event denominators in {period} detection aggregates")
        event_counts[period] = next(iter(counts))
        if event_counts[period] == 0:
            inputs.warnings.append(f"Detection {period} contains no evaluable injected outbreaks; sensitivity and delay cannot establish performance.")
    defaults = []
    for method, threshold in sorted(source.get("operational_default", {}).items()):
        defaults.append({"method": method, "threshold_multiplier": threshold,
                         "evaluation": {period: _matching(curve, method, threshold, period)
                                        for period in ("calibration", "holdout", "all")}})
    return {"calibration_recommendation_recorded": recommendation,
            "recommended_operating_point_evaluation": matched,
            "operational_defaults": defaults, "event_denominators_by_period": event_counts,
            "period_split": source.get("period_split"),
            "timeliness_definition": source.get("timeliness"),
            "false_alert_definition": source.get("false_alert_definition"),
            "threshold_tradeoff": sorted(curve, key=lambda r: (r["evaluation_period"], r["method"], r["threshold_multiplier"])),
            "interpretation": "Threshold selection uses the recorded calibration recommendation. Holdout rows are reported without reselecting on holdout outcomes. Event sensitivity counts a national event once; district-event sensitivity measures affected-district coverage."}


def summarise_artefact(analytics):
    source = analytics.get("detection", {}).get("artifact", {})
    naive = source.get("naive_report_date_comparator", [])
    measured = source.get("details", [])
    method_counts = {}
    eligible = {}
    for row in measured:
        method = row.get("method", "unspecified")
        method_counts[method] = method_counts.get(method, 0)+row.get("alerts_in_backfill_window", 0)
        eligible[method] = eligible.get(method, 0)+row.get("eligible_onset_cases", 0)
    naive_total = sum(row.get("naive_report_date_c2_alerts", 0) for row in naive) if naive else None
    ears_count = method_counts.get("EARS_C2")
    return {"status": source.get("status", "unavailable"),
            "artefact_district_comparator_rows": source.get("nationalised_comparator_districts", len(naive)),
            "naive_report_date_c2_alert_rows": naive_total,
            "mature_onset_alert_rows_by_method": method_counts,
            "eligible_onset_cases_by_method": eligible,
            "naive_minus_mature_onset_c2_alert_rows": naive_total-ears_count if naive_total is not None and ears_count is not None else None,
            "backfill_day_report_cases": sum(row.get("backfill_day_report_cases", 0) for row in naive) if naive else None,
            "source_interpretation": source.get("interpretation"),
            "comparison_unit": "Sums of district-disease-day alert flags in the evaluated backfill window; these are not false-alert episode counts. Concurrent real variation can contribute to these alerts."}


def summarise_forecasts(analytics, rows, inputs):
    by_key = {}
    for row in rows:
        key = (row["model"], row["disease"], row["horizon"])
        if key in by_key:
            raise ValueError(f"Duplicate forecast aggregate: {key}")
        by_key[key] = row
        if row["n"] is None or row["n"] <= 0:
            raise ValueError(f"Forecast aggregate has no scored observations: {key}")
    models = sorted({row["model"] for row in rows})
    diseases = sorted({row["disease"] for row in rows})
    horizons = sorted({row["horizon"] for row in rows})
    if set(diseases) != EXPECTED_DISEASES or set(horizons) != EXPECTED_HORIZONS:
        inputs.warnings.append("Forecast aggregates do not contain exactly the four required diseases and horizons 1–4.")
    comparisons = []
    for row in sorted(rows, key=lambda r: (r["disease"], r["horizon"], r["model"])):
        if row["model"] == BASELINE:
            continue
        baseline = by_key.get((BASELINE, row["disease"], row["horizon"]))
        if baseline is None:
            inputs.warnings.append(f"Seasonal-naive comparison missing for {row['disease']} horizon {row['horizon']}.")
        elif baseline["n"] != row["n"]:
            inputs.warnings.append(f"Unequal model/baseline evaluation counts for {row['disease']} horizon {row['horizon']}.")
        comparisons.append({"model": row["model"], "disease": row["disease"], "horizon": row["horizon"],
                            "candidate": {key: row[key] for key in ("n", "mae", "coverage80", "wis")},
                            "seasonal_naive": {key: baseline[key] for key in ("n", "mae", "coverage80", "wis")} if baseline else None,
                            "mae_comparison": _comparison(row["mae"], baseline["mae"] if baseline else None),
                            "wis_comparison": _comparison(row["wis"], baseline["wis"] if baseline else None),
                            "coverage80_minus_nominal": row["coverage80"]-.8 if row["coverage80"] is not None else None})
    expected = len(EXPECTED_DISEASES)*len(EXPECTED_HORIZONS)
    for model in models:
        if len([row for row in rows if row["model"] == model]) != expected:
            inputs.warnings.append(f"Model {model} has an incomplete disease/horizon grid.")
    pooled = []
    for model in models:
        subset = [row for row in rows if row["model"] == model]
        values = {"model": model, "prediction_records": sum(row["n"] for row in subset), "metric_observations": {}}
        for metric in ("mae", "coverage80", "wis"):
            valid = [row for row in subset if row[metric] is not None]
            n = sum(row["n"] for row in valid)
            values[metric] = math.fsum(row[metric]*row["n"] for row in valid)/n if n else None
            values["metric_observations"][metric] = n
        pooled.append(values)
    actual_n = sum(row["n"] for row in rows)
    recorded_n = analytics.get("rows", {}).get("forecast_backtest")
    if recorded_n is not None and actual_n != recorded_n:
        inputs.warnings.append(f"Forecast CSV observation total {actual_n} differs from analytics backtest rows {recorded_n}; inputs may come from different runs.")
    return {"backtest_design_recorded": analytics.get("forecast_backtest"), "diseases": diseases,
            "horizons_weeks": horizons, "models": models, "comparisons": comparisons,
            "pooled_prediction_record_metrics": pooled,
            "interpretation": "National coverage of district-level forecasts: errors are pooled over scored district-week prediction records, not errors of summed national case forecasts. Different horizons can score the same target week; prediction records are not independent case observations. Lower MAE/WIS is preferable; coverage80 is assessed against nominal 0.8."}


def summarise_allocation(analytics, scenarios, inputs):
    source = analytics.get("allocation_recommendation", {})
    chosen = source.get("measured_scenario")
    budget, equity = source.get("budget"), source.get("equity_floor")
    matching = [row for row in scenarios if _same_number(row["budget"], budget) and _same_number(row["equity_floor"], equity)]
    if len(matching) != 1:
        inputs.warnings.append("Recorded allocation policy has no unique matching scenario CSV row.")
    scenario = matching[0] if len(matching) == 1 else None
    if chosen and scenario:
        for key in ALLOCATION_NUMBERS:
            if key in chosen and not _same_number(chosen[key], scenario.get(key)):
                inputs.warnings.append(f"Recorded allocation scenario differs from CSV field {key}; inputs may come from different runs.")
    measured = scenario or chosen
    return {"recommended_budget": budget, "recommended_equity_floor": equity,
            "recommendation_explanation": source.get("explanation"), "source_warning": source.get("warning"),
            "chosen_scenario_recorded": chosen, "chosen_scenario_csv": scenario,
            "weighted_unmet_comparison": _comparison(measured.get("weighted_unmet"), measured.get("baseline_weighted_unmet")) if measured else None,
            "observed_budget_levels": sorted({row["budget"] for row in scenarios if row["budget"] is not None}),
            "observed_equity_floors": sorted({row["equity_floor"] for row in scenarios if row["equity_floor"] is not None}),
            "scenario_count": len(scenarios),
            "national_scenario_summaries": sorted(scenarios, key=lambda r: (r["budget"], r["equity_floor"])),
            "interpretation": "The chosen budget/equity policy is copied from the completed run, not reselected to create an apparent gain. Weighted unmet demand uses teaching weights and generic budget units; tiny signed differences can be numerical equivalence. Continuous bed/team capacity is not an integer dispatch plan."}


def build_summary(results, generation_manifest=None):
    results = Path(results).resolve()
    inputs = EvaluationInputs(results.parent)
    analytics = inputs.json(results/"analytics_latest.json")
    forecasts = inputs.table(results/"forecast_summary.csv", FORECAST_NUMBERS, FORECAST_NUMBERS | {"model", "disease"})
    allocations = inputs.table(results/"allocation_scenarios.csv", ALLOCATION_NUMBERS, ALLOCATION_NUMBERS)
    detection = inputs.table(results/"detection_tradeoff.csv", DETECTION_NUMBERS, DETECTION_NUMBERS | {"method", "evaluation_period"})
    if generation_manifest is not None:
        # An explicit manifest must exist: typos must not silently lose scale evidence.
        manifest_path = Path(generation_manifest).resolve(strict=True)
    else:
        candidates = (results.parent/"generation_manifest.json", results.parent/"data"/"generation_manifest.json")
        existing = [path for path in candidates if path.exists()]
        if len(existing) > 1 and existing[0].read_bytes() != existing[1].read_bytes():
            raise ValueError("Multiple different generation manifests found; select --generation-manifest explicitly")
        manifest_path = existing[0] if existing else candidates[-1]
    generation = None
    if manifest_path.exists():
        manifest = inputs.json(manifest_path)
        keys = ("schema_version", "synthetic", "scale", "seed", "period", "minimum_required_rows", "unique_encounters",
                "duplicate_resubmissions", "encounter_rows", "facilities", "unique_outbreak_events", "district_event_rows",
                "background_topup_rows", "generated_utc", "elapsed_seconds")
        generation = {key: manifest[key] for key in keys if key in manifest}
        generation["reference_hashes"] = manifest.get("reference_hashes")
        generation["generator_limitations"] = manifest.get("mechanisms", {}).get("limitations")
        period = manifest.get("period", [])
        if len(period) == 2 and analytics.get("as_of") and analytics["as_of"] > str(period[1]):
            inputs.warnings.append("Analytics extends beyond the generator's base period; generation row counts describe the seeded feed, not additional incremental encounters.")
        if manifest.get("synthetic") is not True:
            inputs.warnings.append("Generation manifest does not affirm synthetic encounters; review the scope label before publication.")
    else:
        inputs.warnings.append("Generation manifest unavailable: scale, encounter count and seed are not inferred from paths or forecast row counts.")
    for name, count in (("detection_tradeoff", len(detection)), ("allocation_scenarios", len(allocations))):
        recorded = analytics.get("rows", {}).get(name)
        if recorded is not None and recorded != count:
            inputs.warnings.append(f"{name} CSV rows {count} differ from analytics rows {recorded}; inputs may come from different runs.")
    summary = {"schema_version": "1.0", "created_utc": datetime.now(timezone.utc).isoformat(),
               "scope": {"study": "Synthetic encounter/stock teaching study with public reference inputs",
                         "generation_manifest_confirms_synthetic": generation.get("synthetic") if generation else None,
                         "clinical_or_real_world_validation": False,
                         "aggregation": "National summaries of aggregate district/disease evaluation products; no encounter or district-record tables included"},
               "generation": generation,
               "run": {key: analytics.get(key) for key in ("as_of", "analytics_source_sha256", "input_manifest_sha256",
                                                            "input_manifest_definition", "rows", "timings", "total_seconds")},
               "detection": summarise_detection(analytics, detection, inputs),
               "reporting_artefact": summarise_artefact(analytics),
               "forecasting": summarise_forecasts(analytics, forecasts, inputs),
               "allocation": summarise_allocation(analytics, allocations, inputs),
               "input_files": inputs.sources, "warnings": inputs.warnings}
    summary["status"] = "summarised_with_warnings" if inputs.warnings else "summarised"
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path, help="Completed run's results directory")
    parser.add_argument("--generation-manifest", type=Path,
                        help="Exact completed run manifest; otherwise discover the cloud artifact or local data layout")
    parser.add_argument("--output", required=True, type=Path, help="Destination evaluation_summary.json")
    args = parser.parse_args(argv)
    try:
        summary = build_summary(args.results, args.generation_manifest)
        encoded = json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False)+"\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name+".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, args.output)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Evaluation summary not written: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": summary["status"], "output": str(args.output.resolve()),
                      "warning_count": len(summary["warnings"]), "scale": (summary["generation"] or {}).get("scale"),
                      "as_of": summary["run"]["as_of"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
