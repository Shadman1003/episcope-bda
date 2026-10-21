"""Deterministic, bounded-memory synthetic encounters, facilities and stock feeds.

All encounters and clinical/logistics outcomes are SYNTHETIC. Geography,
denominators and historical weather are attributable public references.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import pandas as pd

DISEASES = np.array(["dengue", "diarrhoea", "influenza", "malaria", "other"])
ICD = np.array([["A90", "A91", "A90", "A90", "A90"], ["A00", "A09", "A09", "A09", "A09"], ["J10", "J11", "J11", "J11", "J11"], ["B50", "B51", "B52", "B53", "B54"], ["Z00", "R50", "Z00", "R50", "Z00"]])
START, END = "2021-01-01", "2025-12-31"
MIN_SF1_ROWS = 25_000_000
SCHEMA_VERSION = "1.0"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_outbreaks(districts: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Truth is written separately and is consumed only by the evaluator."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, 10]))
    rows = []
    for i in range(34):
        disease = str(DISEASES[i % 4])
        eligible = districts
        if disease == "malaria":
            eligible = districts[districts.district_name.isin(["Bandarban", "Khagrachhari", "Rangamati", "Cox's Bazar"])]
        district = eligible.iloc[int(rng.integers(len(eligible)))]
        onset = pd.Timestamp("2022-02-01") + pd.Timedelta(days=int(rng.integers(0, 1350)))
        duration = int(rng.integers(14, 43))
        rows.append(dict(event_id=f"outbreak_{i+1:02}", district_id=district.district_id, disease=disease, start_date=str(onset.date()), end_date=str((onset + pd.Timedelta(days=duration-1)).date()), multiplier=float(rng.uniform(3.5, 8.0)), shape="pulse", is_national=False))
    dhaka = districts.loc[districts.district_name.eq("Dhaka"), "district_id"].iloc[0]
    rows.append(dict(event_id="outbreak_35_slow", district_id=dhaka, disease="dengue", start_date="2024-05-01", end_date="2024-07-23", multiplier=7.0, shape="slow_growth", is_national=False))
    for district_id in districts.district_id:
        rows.append(dict(event_id="outbreak_36_novel_respiratory", district_id=district_id, disease="influenza", start_date="2025-02-15", end_date="2025-04-11", multiplier=6.0, shape="novel_respiratory_surge", is_national=True))
    return pd.DataFrame(rows)


def make_facilities(districts: pd.DataFrame, geojson: dict, seed: int) -> pd.DataFrame:
    from shapely.geometry import Point, shape
    rng = np.random.default_rng(np.random.SeedSequence([seed, 20]))
    # At least four facilities per district; population-weighted remainder models
    # the urban reporting skew while keeping every district represented.
    weights = np.sqrt(districts.population.to_numpy(dtype=float))
    raw = weights / weights.sum() * (640 - 4 * 64)
    allocation = np.floor(raw).astype(int) + 4
    residual = 640 - int(allocation.sum())
    allocation[np.argsort(-(raw - np.floor(raw)))[:residual]] += 1
    geometries = {f["properties"]["district_id"]: shape(f["geometry"]) for f in geojson["features"]}
    rows = []
    for district, n in zip(districts.itertuples(), allocation):
        geometry = geometries[district.district_id]
        left, bottom, right, top = geometry.bounds
        for j in range(int(n)):
            # Rejection sampling puts synthetic facilities inside their actual district.
            for _ in range(2000):
                point = Point(rng.uniform(left, right), rng.uniform(bottom, top))
                if geometry.contains(point):
                    break
            else:
                point = geometry.representative_point()
            kind = "district_hospital" if j == 0 else "upazila_health_complex" if j % 3 == 0 else "clinic"
            beds = int(rng.integers(180, 700) if j == 0 else rng.integers(30, 120) if j % 3 == 0 else rng.integers(8, 40))
            rows.append(dict(facility_id=f"FAC_{district.district_id}_{j+1:03}", district_id=district.district_id, facility_name=f"Synthetic {district.district_name} {kind.replace('_', ' ').title()} {j+1}", facility_type=kind, latitude=round(point.y, 6), longitude=round(point.x, 6), beds=beds, icu_beds=int(beds * (0.05 if j == 0 else 0.01))))
    return pd.DataFrame(rows)


def daily_counts(districts: pd.DataFrame, weather: pd.DataFrame, outbreaks: pd.DataFrame, scale: float, seed: int):
    dates = pd.date_range(START, END)
    count_shape = (64, len(dates), 5)
    expected = np.zeros(count_shape, dtype=np.float64)
    outbreak_flags = np.zeros(count_shape, dtype=bool)
    doy = dates.dayofyear.to_numpy()
    pop = districts.population.to_numpy(dtype=float)
    pop_share = pop / pop.sum()
    for d, district in enumerate(districts.itertuples()):
        climate = weather[weather.district_id.eq(district.district_id)].set_index("date").reindex(dates)
        if climate.isna().any().any():
            raise ValueError(f"Weather missing dates for {district.district_name}")
        rain = climate.rainfall_mm
        lagged_rain = rain.rolling(28, min_periods=1).mean().shift(35).bfill().to_numpy()
        lagged_temp = climate.temperature_c.rolling(28, min_periods=1).mean().shift(35).bfill().to_numpy()
        flood = rain.rolling(7, min_periods=1).sum().shift(7).bfill().to_numpy()
        urban = 3.8 if district.district_name == "Dhaka" else 2.2 if district.district_name in ["Chittagong", "Gazipur", "Narayanganj"] else 1.0
        hill = 30.0 if district.district_name in ["Bandarban", "Khagrachhari", "Rangamati"] else 6.0 if district.district_name == "Cox's Bazar" else 0.10
        # Constructed epidemic mechanisms, not fitted causal estimates. Dengue
        # weather is lagged 35..62 days; diarrhoea follows heavy rain by 7..13.
        dengue_season = np.exp(1.35 * np.cos(2 * np.pi * (doy - 255) / 365.25))
        diarrhoea_season = 0.8 + 0.85 * np.exp(-((doy - 110) / 40) ** 2) + 0.6 * np.exp(-((doy - 285) / 45) ** 2)
        influenza_season = np.exp(0.9 * np.cos(2 * np.pi * (doy - 200) / 365.25))
        malaria_season = np.exp(0.9 * np.cos(2 * np.pi * (doy - 225) / 365.25))
        expected[d, :, 0] = pop_share[d] * 0.17 * urban * dengue_season * (1 + 0.065 * lagged_rain) * np.exp(-((lagged_temp - 28) / 12) ** 2)
        expected[d, :, 1] = pop_share[d] * 0.27 * diarrhoea_season * (1 + 0.6 * np.maximum(flood - 100, 0) / 100)
        expected[d, :, 2] = pop_share[d] * 0.23 * influenza_season
        expected[d, :, 3] = pop_share[d] * 0.025 * hill * malaria_season
        expected[d, :, 4] = pop_share[d] * 0.30 * (1 + 0.08 * np.cos(2 * np.pi * doy / 365.25))
    lookup = {value: i for i, value in enumerate(districts.district_id)}
    for event in outbreaks.itertuples():
        d, k = lookup[event.district_id], int(np.flatnonzero(DISEASES == event.disease)[0])
        mask = (dates >= event.start_date) & (dates <= event.end_date)
        length = int(mask.sum())
        if event.shape == "slow_growth":
            curve = np.linspace(1, event.multiplier, length)
        elif event.shape == "novel_respiratory_surge":
            curve = 1 + (event.multiplier - 1) * np.sin(np.linspace(0.1, np.pi - 0.1, length))
        else:
            curve = np.full(length, event.multiplier)
        expected[d, mask, k] *= curve
        outbreak_flags[d, mask, k] = True
    # Negative-binomial noise: variance = mu + mu² / dispersion. Mean slightly
    # exceeds the required minimum. Rare shortfall is filled with background
    # encounters, disclosed in generation_manifest.json.
    expected *= (25_200_000 * scale) / expected.sum()
    rng = np.random.default_rng(np.random.SeedSequence([seed, 30]))
    dispersion = 15.0
    counts = rng.negative_binomial(dispersion, dispersion / (dispersion + expected)).astype(np.int64)
    minimum = math.ceil(MIN_SF1_ROWS * scale)
    topup = max(0, minimum - int(counts.sum()))
    if topup:
        extra = rng.multinomial(topup, (np.repeat(pop_share, len(dates)) / len(dates)))
        counts[:, :, 4] += extra.reshape(64, len(dates))
    return dates, counts, outbreak_flags, topup


def artefact_table(facilities: pd.DataFrame, districts: pd.DataFrame) -> pd.DataFrame:
    dhaka = districts.loc[districts.district_name.eq("Dhaka"), "district_id"].iloc[0]
    candidates = facilities[facilities.district_id.eq(dhaka)].head(5)
    return pd.DataFrame([dict(artefact_id="silent_then_backfill", facility_id=fid, district_id=dhaka, start_date="2024-09-01", end_date="2024-09-21", backfill_date="2024-09-22", type="reporting_silence_backfill") for fid in candidates.facility_id])


def write_encounters(root: Path, districts: pd.DataFrame, population: pd.DataFrame, facilities: pd.DataFrame, dates, counts, outbreak_flags, artefacts: pd.DataFrame, seed: int, chunk_rows: int):
    out = root / "incoming" / "encounters"
    out.mkdir(parents=True, exist_ok=True)
    manifest, unique_rows, duplicate_rows = [], 0, 0
    holidays = np.array([f"{year}-{day}" for year in range(2021, 2027) for day in ("02-21", "03-26", "04-14", "05-01", "12-16")], dtype="datetime64[D]")
    for d, district in enumerate(districts.itertuples()):
        rng = np.random.default_rng(np.random.SeedSequence([seed, 40, d]))
        local_facilities = facilities[facilities.district_id.eq(district.district_id)]
        facility_ids = local_facilities.facility_id.to_numpy()
        facility_weights = np.sqrt(local_facilities.beds.to_numpy(dtype=float))
        facility_weights /= facility_weights.sum()
        demography = population[population.district_id.eq(district.district_id)]
        demographic_weights = demography.population.to_numpy(dtype=float)
        demographic_weights /= demographic_weights.sum()
        bands = demography.age_band.to_numpy()
        lower = np.array([int(band.rstrip("+").split("-")[0]) for band in bands])
        upper = np.array([100 if band == "90+" else int(band.split("-")[-1]) for band in bands])
        for year in range(2021, 2026):
            day_numbers = np.flatnonzero(dates.year == year)
            cells = np.array([day * 5 + disease for day in day_numbers for disease in range(5)], dtype=np.int32)
            number = counts[d, day_numbers, :].ravel()
            # Largest district/year array is <2m integer indexes at SF=1;
            # line-level fields are expanded in 100k-row chunks only.
            expanded = np.repeat(cells, number)
            for part, offset in enumerate(range(0, len(expanded), chunk_rows)):
                cell = expanded[offset:offset + chunk_rows]
                day, disease = cell // 5, cell % 5
                n = len(cell)
                onset = dates.to_numpy(dtype="datetime64[D]")[day]
                delay = np.minimum(rng.lognormal(0.65, 0.9, n).astype(int), 18)
                report = onset + delay.astype("timedelta64[D]")
                # Bangladesh weekend is Friday/Saturday. Most reports from those
                # dates and selected fixed public holidays shift forward, <=21d.
                low_reporting = rng.random(n) < 0.85
                for _ in range(3):
                    weekday = (report.astype(int) + 3) % 7
                    shift = low_reporting & ((weekday >= 4) & (weekday <= 5) | np.isin(report, holidays))
                    report = report + shift.astype("timedelta64[D]")
                chosen_facility = rng.choice(facility_ids, n, p=facility_weights)
                for artefact in artefacts.itertuples():
                    silent = (chosen_facility == artefact.facility_id) & (report >= np.datetime64(artefact.start_date)) & (report <= np.datetime64(artefact.end_date))
                    # Keep delay within 21d: rows with earlier onset remain on
                    # the last pre-silence day, while true silent-period onsets
                    # backfill on the published resumption date.
                    pre = silent & (onset < np.datetime64(artefact.start_date))
                    report[pre] = np.maximum(onset[pre], np.datetime64(artefact.start_date) - np.timedelta64(1, "D"))
                    report[silent & ~pre] = np.datetime64(artefact.backfill_date)
                demo_idx = rng.choice(len(demography), n, p=demographic_weights)
                age = rng.integers(lower[demo_idx], upper[demo_idx] + 1)
                sex = demography.sex.to_numpy()[demo_idx].astype(object)
                invalid_demo = rng.random(n) < 0.01
                invalid_age = invalid_demo & (rng.random(n) < 0.6)
                age[invalid_age] = 212
                sex[invalid_demo & ~invalid_age] = "UNKNOWN"
                orphan = rng.random(n) < 0.005
                chosen_facility[orphan] = "ORPHAN_FACILITY"
                in_outbreak = outbreak_flags[d, day, disease]
                base_admission = np.array([0.18, 0.08, 0.045, 0.13, 0.015])[disease]
                admission = rng.random(n) < np.minimum(0.85, base_admission * np.where(in_outbreak, 1.65, 1))
                lab = rng.random(n) < np.array([0.68, 0.30, 0.23, 0.72, 0.02])[disease]
                death = admission & (rng.random(n) < np.array([0.010, 0.006, 0.007, 0.012, 0.004])[disease] * np.where(age >= 65, 2.5, 1.0))
                # Repeated synthetic patient IDs simulate cross-facility visits;
                # encounter IDs remain distinct except deliberate resubmissions.
                seq = np.arange(unique_rows, unique_rows + n)
                patient = np.char.add("SYNTH-", rng.integers(0, max(10, int(district.population * 0.4)), n).astype(str))
                patient = np.char.add(np.char.add(district.district_id + "-", patient), "")
                frame = pd.DataFrame(dict(encounter_id=np.char.add("E", np.char.zfill(seq.astype(str), 12)), patient_id=patient, facility_id=chosen_facility, onset_date=onset.astype(str), report_date=report.astype(str), age_years=age, sex=sex, icd10_code=ICD[disease, rng.integers(0, 5, n)], lab_confirmed=lab.astype(int), admitted=admission.astype(int), outcome=np.where(death, "died", "recovered"), revision=np.ones(n, dtype=int)))
                # Additional timestamp/code defects provide meaningful quarantine.
                missing = rng.random(n)
                frame.loc[missing < 0.001, "onset_date"] = ""
                frame.loc[(missing >= 0.001) & (missing < 0.002), "report_date"] = "2020-01-01"
                frame.loc[(missing >= 0.002) & (missing < 0.003), "icd10_code"] = "INVALID"
                duplicates = frame.loc[rng.random(n) < 0.02]
                frame = pd.concat([frame, duplicates], ignore_index=True)
                path = out / f"encounters_d{district.district_id}_{year}_{part:03}.csv"
                frame.to_csv(path, index=False, lineterminator="\n")
                if path.stat().st_size >= 50_000_000:
                    raise RuntimeError(f"Chunk exceeds 50 MB: {path}; reduce --chunk-rows")
                manifest.append(dict(path=str(path.relative_to(root)).replace("\\", "/"), rows=len(frame), bytes=path.stat().st_size, sha256=sha256(path)))
                unique_rows += n
                duplicate_rows += len(duplicates)
        if (d + 1) % 8 == 0:
            print(f"Encounters: {d+1}/64 districts, {unique_rows:,} unique, {duplicate_rows:,} resubmissions", flush=True)
    return manifest, unique_rows, duplicate_rows


def write_stocks(root: Path, facilities: pd.DataFrame, districts: pd.DataFrame, dates, counts, scale: float, seed: int):
    out = root / "incoming" / "stocks"
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(np.random.SeedSequence([seed, 50]))
    district_index = {value: i for i, value in enumerate(districts.district_id)}
    facility_district = np.array([district_index[value] for value in facilities.district_id])
    beds = facilities.beds.to_numpy()
    facility_share = beds / facilities.groupby("district_id").beds.transform("sum").to_numpy()
    manifests = []
    for month in pd.period_range(START, END, freq="M"):
        day_ids = np.flatnonzero((dates.year == month.year) & (dates.month == month.month))
        frames = []
        for day_id in day_ids:
            clinical = counts[facility_district, day_id, :] / max(scale, 0.0001)
            strain = clinical[:, :4].sum(axis=1) * facility_share / (beds * 15 + 1)
            occupancy = np.clip(0.43 + 0.22 * np.sin(2 * np.pi * dates[day_id].dayofyear / 365.25) + strain + rng.normal(0, 0.07, len(facilities)), 0.15, 0.99)
            replenishment = 1 + 0.12 * np.sin(2 * np.pi * dates[day_id].dayofyear / 30)
            frames.append(pd.DataFrame(dict(facility_id=facilities.facility_id, date=str(dates[day_id].date()), iv_saline=np.maximum(0, (beds * 7 * replenishment - clinical[:, 0] * facility_share * 2)).astype(int), ns1_kits=np.maximum(0, (beds * 2 * replenishment - clinical[:, 0] * facility_share)).astype(int), ors=np.maximum(0, (beds * 15 * replenishment - clinical[:, 1] * facility_share * 2)).astype(int), antimalarials=np.maximum(0, (beds * 2 * replenishment - clinical[:, 3] * facility_share)).astype(int), occupied_beds=np.minimum(beds, np.rint(beds * occupancy)).astype(int))))
        frame = pd.concat(frames, ignore_index=True)
        path = out / f"stocks_{month}.jsonl"
        frame.to_json(path, orient="records", lines=True)
        manifests.append(dict(path=str(path.relative_to(root)).replace("\\", "/"), rows=len(frame), bytes=path.stat().st_size, sha256=sha256(path)))
    return manifests


def generate(root: Path, scale: float = 0.01, seed: int = 4345, chunk_rows: int = 100_000, force: bool = False) -> dict:
    if not (0 < scale <= 10):
        raise ValueError("Scale must be positive and <=10")
    if not (100 <= chunk_rows <= 200_000):
        raise ValueError("Chunk rows must be between 100 and 200000 to protect bounded memory/files")
    started = time.perf_counter()
    manifest_path = root / "generation_manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old.get("scale") == scale and old.get("seed") == seed and not force:
            paths = [root / item["path"] for item in old["encounter_files"] + old["stock_files"]]
            if all(path.exists() for path in paths):
                print("Existing matching seeded feed retained; use --force to regenerate.", flush=True)
                return old
        if not force:
            raise FileExistsError("Existing generator output has another seed/scale; use a separate --root or --force")
    # Only the generator's own allowlisted file patterns are replaced. A separate
    # root is strongly recommended for each benchmark profile.
    if force:
        for subdir, pattern in [("encounters", "encounters_d*.csv"), ("stocks", "stocks_*.jsonl")]:
            for path in (root / "incoming" / subdir).glob(pattern):
                path.unlink()
    districts = pd.read_csv(root / "reference" / "districts.csv", dtype={"district_id": str}).sort_values("district_id").reset_index(drop=True)
    population = pd.read_csv(root / "reference" / "population.csv", dtype={"district_id": str, "age_band": str})
    weather = pd.read_parquet(root / "reference" / "weather.parquet")
    weather["date"] = pd.to_datetime(weather.date)
    weather["district_id"] = weather.district_id.astype(str).str.zfill(2)
    geojson = json.loads((root / "reference" / "districts.geojson").read_text())
    facilities = make_facilities(districts, geojson, seed)
    facilities.to_csv(root / "reference" / "facilities.csv", index=False)
    outbreaks = make_outbreaks(districts, seed)
    artefacts = artefact_table(facilities, districts)
    truth = root / "truth"
    truth.mkdir(parents=True, exist_ok=True)
    outbreaks.to_csv(truth / "ground_truth_outbreaks.csv", index=False)
    artefacts.to_csv(truth / "reporting_artefacts.csv", index=False)
    dates, counts, flags, topup = daily_counts(districts, weather, outbreaks, scale, seed)
    files, unique, duplicates = write_encounters(root, districts, population, facilities, dates, counts, flags, artefacts, seed, chunk_rows)
    stock_files = write_stocks(root, facilities, districts, dates, counts, scale, seed)
    manifest = dict(schema_version=SCHEMA_VERSION, synthetic=True, scale=scale, seed=seed, period=[START, END], minimum_required_rows=math.ceil(MIN_SF1_ROWS * scale), unique_encounters=unique, duplicate_resubmissions=duplicates, encounter_rows=unique+duplicates, facilities=len(facilities), unique_outbreak_events=outbreaks.event_id.nunique(), district_event_rows=len(outbreaks), background_topup_rows=topup, elapsed_seconds=round(time.perf_counter()-started, 3), generated_utc=datetime.now(timezone.utc).isoformat(), encounter_files=files, stock_files=stock_files, reference_hashes={name: sha256(root / "reference" / name) for name in ["districts.csv", "population.csv", "weather.parquet", "districts.geojson"]}, mechanisms=dict(noise="Negative binomial: variance mu+mu^2/15", weather_lag_dengue_days=[35, 62], weather_lag_diarrhoea_days=[7, 13], duplicates="~2% exact encounter resubmissions", orphan_keys="~0.5%", demographic_invalid="~1% either age 212 or sex UNKNOWN", additional_defects="~0.1% missing onset; ~0.1% report before onset; ~0.1% invalid ICD", reporting="Right-skewed 0–21d delay, Friday/Saturday and selected fixed-holiday reporting dips; five Dhaka facilities silent three weeks then backfill", severity="Disease-specific admissions/lab probabilities; admissions rise during injected outbreaks; deaths nested within admission", limitations="Constructed mechanisms for testing analytics, not a fitted epidemiological model or clinically validated disease burden. Fixed national holidays are illustrative and exclude lunar holiday dates. Synthetic 90+ ages are uniform 90..100. Stock quantities and treatment conversion are teaching assumptions, not procurement advice."))
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({key: value for key, value in manifest.items() if key not in ["encounter_files", "stock_files", "reference_hashes", "mechanisms"]}, indent=2), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--scale", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=4345)
    parser.add_argument("--chunk-rows", type=int, default=100_000)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    generate(args.root, args.scale, args.seed, args.chunk_rows, args.force)


if __name__ == "__main__":
    main()
