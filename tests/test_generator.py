"""Generator checks focus on integrity and realism, without heavy line lists."""
import json
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from generator.generate import artefact_table, daily_counts, make_outbreaks, write_encounters


def test_fourteen_day_fixture_is_deterministic_and_extends_dates(tmp_path):
    source = Path(__file__).parent / "fixtures" / "create_hidden_drop.py"
    spec = importlib.util.spec_from_file_location("hidden_drop_fixture", source)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    ref = tmp_path / "data" / "reference"
    ref.mkdir(parents=True)
    pd.DataFrame([dict(facility_id="FAC_01_001", district_id="01", beds=100)]).to_csv(ref / "facilities.csv", index=False)
    a = fixture.make_drop(ref.parent, tmp_path / "drop_a")
    b = fixture.make_drop(ref.parent, tmp_path / "drop_b")
    assert a == b
    assert a["onset_period"] == ["2026-01-01", "2026-01-14"]
    assert a["encounter_rows"] == 628
    assert a["unique_encounters"] == 616
    assert a["duplicate_resubmissions"] == 12
    assert a["deliberately_invalid_records"] == 6
    rows = pd.read_csv(tmp_path / "drop_a" / "encounters" / "hidden_fourteen_days.csv")
    assert rows.encounter_id.duplicated().sum() == 12
    assert rows.icd10_code.eq("INVALID").sum() == 1


def test_seeded_reporting_silence_backfill_and_defects_without_network(tmp_path):
    """An isolated district fixture proves the artefact survives quality rules."""
    districts = pd.DataFrame([dict(district_id="13", district_name="Dhaka", population=100000, division="Dhaka")])
    population = pd.DataFrame([dict(district_id="13", age_band="0-4", sex="M", population=70000), dict(district_id="13", age_band="50-54", sex="F", population=30000)])
    facilities = pd.DataFrame([dict(facility_id="FAC_13_001", district_id="13", beds=100)])
    dates = pd.date_range("2021-01-01", "2025-12-31")
    counts = np.zeros((1, len(dates), 5), dtype=np.int64)
    counts[0, (dates >= "2024-09-01") & (dates <= "2024-09-21"), 0] = 100
    flags = np.zeros_like(counts, dtype=bool)
    artefacts = artefact_table(facilities, districts)
    files, unique, duplicates = write_encounters(tmp_path, districts, population, facilities, dates, counts, flags, artefacts, 4345, 1000)
    assert unique == 2100
    assert 0 < duplicates < 120
    frames = [pd.read_csv(tmp_path / item["path"]) for item in files]
    sample = pd.concat(frames, ignore_index=True)
    assert len(sample) == unique + duplicates
    assert sample.encounter_id.nunique() == unique
    report = pd.to_datetime(sample.report_date)
    onset = pd.to_datetime(sample.onset_date)
    valid = report.ge(onset) & onset.notna()
    assert (report[valid] - onset[valid]).le(pd.Timedelta(days=21)).all()
    assert not report.between("2024-09-01", "2024-09-21").any()
    assert report.eq(pd.Timestamp("2024-09-22")).sum() > 1500
    assert sample.facility_id.eq("ORPHAN_FACILITY").any()
    assert (sample.age_years.eq(212) | sample.sex.eq("UNKNOWN")).any()
    assert sample.loc[sample.outcome.eq("died"), "admitted"].eq(1).all()
    repeat = tmp_path / "repeat"
    repeat_files, repeat_unique, repeat_duplicates = write_encounters(repeat, districts, population, facilities, dates, counts, flags, artefacts, 4345, 1000)
    assert (repeat_unique, repeat_duplicates) == (unique, duplicates)
    assert [item["sha256"] for item in files] == [item["sha256"] for item in repeat_files]


@pytest.fixture(scope="module")
def public_references():
    root = Path(__file__).resolve().parents[1] / "data" / "reference"
    if not (root / "weather.parquet").exists():
        pytest.skip("Run generator.acquire to populate the cached public references")
    districts = pd.read_csv(root / "districts.csv", dtype={"district_id": str})
    weather = pd.read_parquet(root / "weather.parquet")
    weather["date"] = pd.to_datetime(weather.date)
    weather["district_id"] = weather.district_id.astype(str).str.zfill(2)
    return root, districts, weather


def test_reference_dimensions_population_conservation(public_references):
    root, districts, weather = public_references
    population = pd.read_csv(root / "population.csv", dtype={"district_id": str})
    assert len(districts) == 64
    assert districts.district_id.nunique() == 64
    assert districts.population.sum() > 100_000_000
    assert len(population) == 64 * 40
    assert set(population.sex) == {"M", "F"}
    assert population.groupby("district_id").population.sum().to_dict() == districts.set_index("district_id").population.to_dict()
    assert len(weather) == 64 * 1826
    assert not weather.duplicated(["district_id", "date"]).any()
    assert weather.groupby("district_id").date.nunique().eq(1826).all()
    assert weather.rainfall_mm.ge(0).all()
    assert weather.humidity_pct.between(0, 100).all()
    assert not weather.isna().any().any()
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["population"]["doi"] == "10.5258/SOTON/WP00842"
    assert provenance["weather"]["model"] == "era5"


def test_generator_seed_minimum_events_and_climate_risk(public_references):
    _, districts, weather = public_references
    outbreaks = make_outbreaks(districts, 4345)
    assert outbreaks.event_id.nunique() >= 30
    assert "slow_growth" in outbreaks["shape"].values
    national = outbreaks[outbreaks.is_national]
    assert national.district_id.nunique() == 64
    assert set(national.disease) == {"influenza"}
    a = daily_counts(districts, weather, outbreaks, 0.01, 4345)
    b = daily_counts(districts, weather, outbreaks, 0.01, 4345)
    assert np.array_equal(a[1], b[1])
    assert int(a[1].sum()) >= 250_000
    assert a[1].shape == (64, 1826, 5)
    assert a[1].min() >= 0
    assert all(a[2][:, :, i].any() for i in range(4))
    malaria = a[1][:, :, 3].sum(axis=1)
    hill = districts.district_name.isin(["Bandarban", "Khagrachhari", "Rangamati", "Cox's Bazar"])
    assert malaria[hill].sum() > malaria[~hill].sum()
    changed_seed = daily_counts(districts, weather, make_outbreaks(districts, 4346), 0.01, 4346)
    assert not np.array_equal(a[1], changed_seed[1])
    # Count generation is small even for full SF1: it does not expand line lists.
    full = daily_counts(districts, weather, outbreaks, 1.0, 4345)
    assert full[1].sum() >= 25_000_000
    assert full[1].sum() > 90 * a[1].sum()


def test_demo_line_list_defects_severity_and_bounded_chunks(public_references):
    root, _, _ = public_references
    manifest_path = root.parent / "generation_manifest.json"
    if not manifest_path.exists():
        pytest.skip("Run generator.generate --scale .01 for feed integration checks")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["facilities"] >= 600
    assert manifest["encounter_rows"] >= manifest["minimum_required_rows"]
    assert manifest["unique_outbreak_events"] >= 30
    assert all(item["bytes"] < 50_000_000 for item in manifest["encounter_files"] + manifest["stock_files"])
    # Sampling bounded files is sufficient for validation; never collect SF1.
    frames = [pd.read_csv(root.parent / item["path"]) for item in manifest["encounter_files"][:20]]
    sample = pd.concat(frames, ignore_index=True)
    assert sample.encounter_id.duplicated().any()
    assert sample.patient_id.str.contains("SYNTH").all()
    assert sample.facility_id.eq("ORPHAN_FACILITY").any()
    assert (sample.age_years.eq(212) | sample.sex.eq("UNKNOWN")).any()
    valid_dates = pd.to_datetime(sample.report_date, errors="coerce") - pd.to_datetime(sample.onset_date, errors="coerce")
    nondefect = valid_dates[valid_dates.ge(pd.Timedelta(0))].dropna()
    assert nondefect.le(pd.Timedelta(days=21)).all()
    assert sample.loc[sample.outcome.eq("died"), "admitted"].eq(1).all()
    assert set(sample.sex) <= {"M", "F", "UNKNOWN"}
