"""Produce a small, deterministic fourteen-day incremental acceptance feed.

This fixture is synthetic and independent of generator truth. It deliberately
extends the initial teaching period, includes an unusual rise in dengue, a
late-onset report and quality defects. It is a local test, not an instructor's
unknown assessment dataset.

python tests/fixtures/create_hidden_drop.py --data-root data --output work/hidden_drop
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import random


FIELDS = ["encounter_id", "patient_id", "facility_id", "onset_date", "report_date", "age_years", "sex", "icd10_code", "lab_confirmed", "admitted", "outcome", "revision"]


def make_drop(data_root: Path, output: Path, start: str = "2026-01-01", seed: int = 6991) -> dict:
    rng = random.Random(seed)
    with (data_root / "reference" / "facilities.csv").open(newline="") as stream:
        facilities = list(csv.DictReader(stream))
    if not facilities:
        raise ValueError("Generate the reference facility registry first")
    district_id = facilities[0]["district_id"]
    local = [facility for facility in facilities if facility["district_id"] == district_id]
    first_day = date.fromisoformat(start)
    rows = []
    for day in range(14):
        onset = first_day + timedelta(days=day)
        # An unannounced structural rise after day 7 tests runtime adaptability.
        n = 12 if day < 7 else 75
        for number in range(n):
            facility = rng.choice(local)
            sequence = len(rows)
            admitted = int(rng.random() < 0.25)
            rows.append(dict(encounter_id=f"HIDDEN-{seed}-{sequence:06}", patient_id=f"SYNTH-HIDDEN-{seed}-{sequence//2:06}", facility_id=facility["facility_id"], onset_date=onset.isoformat(), report_date=(onset+timedelta(days=rng.randint(0, 3))).isoformat(), age_years=rng.randint(0, 95), sex=rng.choice(["M", "F"]), icd10_code="A90" if rng.random() < 0.85 else "A09", lab_confirmed=int(rng.random() < 0.6), admitted=admitted, outcome="recovered", revision=1))
    # Realistic late-arriving report: onset predates the drop, delay <=21 days.
    rows.append(dict(encounter_id=f"HIDDEN-{seed}-LATE", patient_id=f"SYNTH-HIDDEN-{seed}-LATE", facility_id=local[0]["facility_id"], onset_date=(first_day-timedelta(days=10)).isoformat(), report_date=(first_day+timedelta(days=3)).isoformat(), age_years=38, sex="F", icd10_code="J11", lab_confirmed=0, admitted=0, outcome="recovered", revision=1))
    # Deterministic defects exercise quarantine independently of row order.
    defects = [dict(facility_id="UNKNOWN_FACILITY"), dict(age_years=212), dict(sex="INVALID"), dict(report_date="2020-01-01"), dict(onset_date="not-a-date"), dict(icd10_code="INVALID")]
    for i, mutation in enumerate(defects):
        record = dict(rows[i])
        record.update(mutation)
        record["encounter_id"] = f"HIDDEN-{seed}-DEFECT-{i}"
        rows.append(record)
    # Re-submitted records have the same encounter identity and contents.
    rows.extend(dict(row) for row in rows[:12])
    encounters_dir, stock_dir = output / "encounters", output / "stocks"
    encounters_dir.mkdir(parents=True, exist_ok=True)
    stock_dir.mkdir(parents=True, exist_ok=True)
    encounters_path = encounters_dir / "hidden_fourteen_days.csv"
    with encounters_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    stock_path = stock_dir / "hidden_stock_snapshot.jsonl"
    as_of = (first_day + timedelta(days=13)).isoformat()
    with stock_path.open("w") as stream:
        for facility in local:
            beds = int(facility["beds"])
            stream.write(json.dumps(dict(facility_id=facility["facility_id"], date=as_of, iv_saline=120, ns1_kits=30, ors=250, antimalarials=20, occupied_beds=int(beds*0.85)), separators=(",", ":")) + "\n")
    manifest = dict(synthetic=True, purpose="Incremental acceptance fixture; does not represent instructor assessment data", seed=seed, onset_period=[start, (first_day+timedelta(days=13)).isoformat()], district_id=district_id, encounter_rows=len(rows), unique_encounters=len({row["encounter_id"] for row in rows}), duplicate_resubmissions=12, deliberately_invalid_records=len(defects), late_onset_records=1, stock_rows=len(local), files={str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest() for path in [encounters_path, stock_path]})
    (output / "fixture_manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("work/hidden_drop"))
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--seed", type=int, default=6991)
    args = parser.parse_args()
    print(json.dumps(make_drop(args.data_root, args.output, args.start, args.seed), indent=2))
