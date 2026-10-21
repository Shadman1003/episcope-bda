"""Build a bounded, aggregate-only release from Spark gold marts.

The compact row schemas are declared in payload['columns']; date/district/disease
indices avoid repeating strings tens of thousands of times. Heavy preparation
remains in Spark. Only aggregated gold rows are collected, never encounters.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from serving.privacy import count_bin, coupled_bins, safe_ratio, quality_release

MAX_BYTES = 10_000_000
DISEASES = ["dengue", "diarrhoea", "influenza", "malaria"]


def _date(value):
    return value.isoformat()[:10] if hasattr(value, "isoformat") else str(value)[:10]


def _district(value):
    return str(value).zfill(2)


def _distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    position = max(0, min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(point[0] - start[0] - position * dx, point[1] - start[1] - position * dy)


def _simplify(points, tolerance):
    if len(points) <= 4:
        return [[round(x, 4), round(y, 4)] for x, y, *_ in points]
    # An iterative RDP avoids recursion failures on detailed coastline polygons.
    keep, stack = {0, len(points) - 1}, [(0, len(points) - 1)]
    while stack:
        begin, end = stack.pop()
        if end <= begin + 1:
            continue
        far = max(range(begin + 1, end), key=lambda i: _distance(points[i], points[begin], points[end]))
        if _distance(points[far], points[begin], points[end]) > tolerance:
            keep.add(far)
            stack.extend([(begin, far), (far, end)])
    result = [[round(points[i][0], 4), round(points[i][1], 4)] for i in sorted(keep)]
    if len(result) < 4:
        result = [[round(x, 4), round(y, 4)] for x, y, *_ in points[::max(1, len(points) // 4)]]
        if result[0] != result[-1]:
            result.append(result[0])
    return result


def _rings(polygon, tolerance):
    result = []
    for index, original in enumerate(polygon):
        ring = _simplify(original, tolerance)
        # D3's spherical polygon convention needs clockwise exterior rings for
        # polygons smaller than a hemisphere; RFC 7946 sources often use CCW.
        signed = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
        if (index == 0 and signed > 0) or (index > 0 and signed < 0):
            ring = list(reversed(ring))
        result.append(ring)
    return result


def simplify_geojson(geojson, districts, tolerance=.008):
    names = {row["district_name"].lower(): row["district_id"] for row in districts}
    known = {row["district_id"] for row in districts}
    features = []
    for feature in geojson.get("features", []):
        properties = feature.get("properties", {})
        identifier = _district(properties.get("district_id", properties.get("id", "")))
        if identifier not in known:
            name = str(properties.get("district_name", properties.get("shapeName", properties.get("name", "")))).lower()
            identifier = names.get(name)
        if not identifier:
            continue
        geometry = feature.get("geometry", {})
        kind, coordinates = geometry.get("type"), geometry.get("coordinates", [])
        if kind == "Polygon":
            coordinates = _rings(coordinates, tolerance)
        elif kind == "MultiPolygon":
            coordinates = [_rings(polygon, tolerance) for polygon in coordinates]
        else:
            continue
        features.append({"type": "Feature", "properties": {"district_id": identifier}, "geometry": {"type": kind, "coordinates": coordinates}})
    return {"type": "FeatureCollection", "features": features}


def _json_bytes(value):
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def validate_release(payload):
    """Validate public count columns; indices, dates and dimensionless scores differ."""
    observed_keys = [tuple(row[:3]) for row in payload.get("observed", [])]
    if len(observed_keys) != len(set(observed_keys)):
        raise ValueError("Observation release cells must be disjoint and unique")
    count_columns = {
        "observed": [3],
        "forecasts": [4, 5, 6, 7],
        "signals": [3, 4, 5],
        "allocations": [4, 5, 6, 7, 8],
    }
    for table, positions in count_columns.items():
        for row in payload.get(table, []):
            for position in positions:
                value = row[position]
                if value is not None and (value < 0 or 0 < value < 5 or value % 5 != 0):
                    raise ValueError(f"Unsafe public count in {table}[{position}]: {value}")
    for row in payload.get("allocations", []):
        values = row[4:9]
        if any(v is None for v in values) and not all(v is None for v in values):
            raise ValueError("Allocation balance tuple has partial suppression")
    if any(any(value is not None for value in row[3:6]) for row in payload.get("signals", [])):
        raise ValueError("Daily numerator, expected counts and thresholds must not be released")
    if any(forbidden in payload for forbidden in ("totals", "daily", "patients", "encounters", "admissions", "deaths")):
        raise ValueError("Overlapping totals or patient-level tables are forbidden")
    for row in payload.get('quality', []):
        values=[row.get(key) for key in ('rows_in','rows_out','quarantined','duplicates')]
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError('Quality balance tuple has partial suppression')
        for value in values + [rule.get('failed') for rule in row.get('rules',[])]:
            if value is not None and (value < 0 or 0 < value < 5 or value % 5 != 0):
                raise ValueError('Unsafe quality count')
    return True


def export_dashboard(spark, root: Path) -> dict:
    from pyspark.sql import functions as F

    root = Path(root).resolve()
    gold = root / "lake" / "gold"
    output = root.parent / "dashboard" / "data"
    output.mkdir(parents=True, exist_ok=True)

    def read(name):
        path = gold / name
        return spark.read.parquet(str(path)) if path.exists() else None

    weekly = read("weekly")
    if weekly is None:
        raise FileNotFoundError(f"Required gold mart is missing: {gold / 'weekly'}")
    registry = weekly.select("district_id", "district_name", "division", "population").distinct().orderBy("district_id").collect()
    districts = [{"district_id": _district(row.district_id), "district_name": row.district_name, "division": row.division, "population": int(row.population or 0)} for row in registry]
    district_index = {row["district_id"]: i for i, row in enumerate(districts)}
    disease_names = sorted(row.disease for row in weekly.select("disease").distinct().collect())
    disease_index = {name: i for i, name in enumerate(disease_names)}
    week_dates = [_date(row.week_start) for row in weekly.select("week_start").distinct().orderBy("week_start").collect()]
    week_index = {week: i for i, week in enumerate(week_dates)}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload = {
        "version": 1, "generated_at": now, "as_of": week_dates[-1],
        "label": "Synthetic surveillance teaching prototype · public geography, population and climate",
        "privacy": {"k": 5, "rounding": "Counts are rounded down to multiples of five. Positive counts below five are withheld.", "policy": "Only disjoint district/disease/week observations are released. No admissions, deaths, daily case counts or exact parent totals are published. Arithmetic logistics families are suppressed together. Displayed sums include only visible cells.", "limitations": "Small-cell and complementary-total controls; not a differential-privacy guarantee."},
        "districts": districts, "diseases": disease_names, "weeks": week_dates,
        "columns": {
            "observed": ["district", "disease", "week", "cases", "completeness"],
            "forecasts": ["district", "disease", "week_start", "horizon", "prediction", "lower80", "upper80", "baseline"],
            "signals": ["district", "disease", "date", "observed", "expected", "threshold", "score", "alert", "complete", "reporting_artefact_suspected"],
            "allocations": ["district", "resource", "budget", "equity_floor", "demand", "stock", "allocation", "unmet", "baseline_allocation"],
            "calendar": ["division", "disease", "epi_week", "seasonal_index", "action", "year"],
        },
        "observed": [], "forecasts": [], "signals": [], "allocations": [], "calendar": [],
        "tradeoff": [], "scenarios": [], "resources": [], "quality": [],
    }
    observed_rows = weekly.select("district_id", "disease", "week_start", "cases", "completeness").orderBy("district_id", "disease", "week_start").collect()
    for row in observed_rows:
        payload["observed"].append([district_index[_district(row.district_id)], disease_index[row.disease], week_index[_date(row.week_start)], count_bin(row.cases), safe_ratio(row.completeness)])

    forecasts = read("forecasts")
    if forecasts is not None:
        for row in forecasts.orderBy("district_id", "disease", "week_start").collect():
            bins = coupled_bins([row.prediction, row.lower80, row.upper80, row.baseline])
            payload["forecasts"].append([district_index[_district(row.district_id)], disease_index[row.disease], _date(row.week_start), int(row.horizon), *bins])
        payload["forecast_origin"] = _date(forecasts.agg(F.max("origin")).first()[0])
        if "issued_on" in forecasts.columns:
            payload["forecast_issued_on"] = _date(forecasts.agg(F.max("issued_on")).first()[0])

    alerts = read("alerts")
    if alerts is not None:
        # The browser gets the latest complete decision state, not overlapping
        # daily observations. Daily observed and expected counts remain private.
        recent = alerts.filter(F.col("method") == "EARS_C2")
        latest = recent.agg(F.max("date")).first()[0]
        if latest is not None:
            recent = recent.filter(F.col("date") == F.lit(latest))
            for row in recent.orderBy("district_id", "disease").collect():
                # No daily case values are released: a signal score is withheld
                # whenever its daily numerator/baseline includes a small cell.
                safe = all(count_bin(v) is not None for v in [row.observed, row.expected])
                payload["signals"].append([district_index[_district(row.district_id)], disease_index[row.disease], _date(row.date), None, None, None, safe_ratio(row.score, 2) if safe else None, bool(row.alert) if safe else False, bool(row.data_complete), bool(row.asDict().get("reporting_artefact_suspected", False))])
            payload["signal_as_of"] = _date(latest)
            if "as_of_date" in recent.columns:
                payload["signal_issued_on"] = _date(recent.agg(F.max("as_of_date")).first()[0])

    allocations = read("allocation_grid")
    if allocations is None:
        allocations = read("allocations")
    if allocations is not None:
        resources = sorted(row.resource for row in allocations.select("resource").distinct().collect())
        payload["resources"] = resources
        resource_index = {resource: i for i, resource in enumerate(resources)}
        for row in allocations.orderBy("budget", "equity_floor", "district_id", "resource").collect():
            payload["allocations"].append([district_index[_district(row.district_id)], resource_index[row.resource], round(float(row.budget), 2), float(row.equity_floor), *coupled_bins([row.demand, row.stock, row.allocation, row.unmet, row.baseline_allocation])])
    calendar = read("calendar")
    if calendar is not None:
        for row in calendar.orderBy("division", "disease", "epi_week").collect():
            payload["calendar"].append([row.division, disease_index.get(row.disease, row.disease), int(row.epi_week), safe_ratio(row.seasonal_index), row.action, int(row.year)])
    tradeoff = read("detection_tradeoff")
    if tradeoff is not None:
        metric_fields = ["method", "evaluation_period", "threshold_multiplier", "sensitivity", "false_alerts_per_district_year", "median_days_to_detection"]
        payload["tradeoff"] = [{key: safe_ratio(value) if isinstance(value, (float, int)) else value for key, value in row.asDict().items() if key in metric_fields} for row in tradeoff.collect() if row.asDict().get("evaluation_period", "all") == "all"]
    scenarios = read("allocation_scenarios")
    if scenarios is not None:
        # Exact national unmet-demand totals would allow subtraction of a hidden
        # district. Publish only scenario controls and dimensionless comparisons.
        payload["scenarios"] = [{"budget": round(float(row.budget), 2), "equity_floor": float(row.equity_floor), "benefit_vs_baseline_pct": safe_ratio(row.benefit_vs_baseline_pct), "min_population_ratio": safe_ratio(row.min_population_ratio), "transport_utilisation": safe_ratio(row.transport_utilisation)} for row in scenarios.collect()]
    geography_path = root / "reference" / "districts.geojson"
    quality = read('quality')
    if quality is not None:
        payload['quality'] = [quality_release(row.asDict()) for row in quality.orderBy(F.desc('loaded_at')).collect()]
    payload["geography"] = simplify_geojson(json.loads(geography_path.read_text(encoding="utf-8")), districts) if geography_path.exists() else {"type": "FeatureCollection", "features": []}
    validate_release(payload)
    raw = _json_bytes(payload)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"Public dashboard payload is {len(raw):,} bytes; limit is {MAX_BYTES:,}. Reduce release detail instead of silently truncating.")
    destination = output / "dashboard.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_bytes(raw)
    temporary.replace(destination)
    manifest = {"generated_at": now, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "counts": {table: len(payload[table]) for table in ["observed", "forecasts", "signals", "allocations", "calendar"]}, "privacy": "disjoint weekly cells, five-count bins, coupled balance suppression", "path": str(destination)}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    total_bytes = sum(path.stat().st_size for path in output.glob("*.json"))
    if total_bytes > MAX_BYTES:
        raise ValueError(f"Combined dashboard JSON exceeds 10 MB ({total_bytes:,} bytes)")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data"))
    args = parser.parse_args()
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.appName("EpiScope-public-export").getOrCreate()
    try:
        print(json.dumps(export_dashboard(spark, args.root), indent=2))
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
