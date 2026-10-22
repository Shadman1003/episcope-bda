#!/usr/bin/env python3
"""Verify downloaded aggregate CI evidence; validate only unless explicitly promoted.

Extract each GitHub artifact into its own folder below --directory. Three complete
scale bundles are required. A separate bounded optimisation bundle is optional.
No Spark, clinical files, network requests or Git operations are used.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from scripts.ci_validate import (MAX_ARTIFACT_BYTES, MAX_FILE_BYTES,
                                 SAFE_RESULTS, reject_record_identifiers, validate_completed)
from scripts.ci_capture_optimisations import DIRECT_IDENTIFIER, LABELS, validate_query_result
from scripts.summarise_evaluation import build_summary
from serving.export import validate_release

PROFILE_FILES = {
    "measurement.json", "generation_manifest.json", "reference_provenance.json",
    "dashboard/data/dashboard.json", "dashboard/data/manifest.json",
    "results/evaluation_summary.json", *(f"results/{name}" for name in SAFE_RESULTS),
}
SCALES = {.25: "sf0_25", .5: "sf0_5", 1.: "sf1"}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    def nonfinite(value):
        raise ValueError(f"Non-finite JSON number in {path.name}: {value}")
    value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path.name}")
    return value


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n").encode("utf-8")


def safe_relative(value):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("Artifact path is not a plain relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("..", ".") for part in path.parts) or path.as_posix() != value:
        raise ValueError("Artifact path is absolute, noncanonical or traverses directories")
    return path


def verify_inventory(folder, manifest_name, entries, *, optimisation=False):
    listed = set()
    total = 0
    for row in entries:
        name = row.get("file" if optimisation else "path")
        relative = safe_relative(name)
        if name in listed or name == manifest_name:
            raise ValueError("Duplicate or self-referencing artifact manifest entry")
        if optimisation:
            allowed = name == "optimisations.json" or name in {f"{label}_plan.txt" for label in LABELS}
            allowed |= name in {f"{label}_sample-{sample}_plan.txt" for label in LABELS for sample in (1, 2, 3)}
            allowed |= bool(re.fullmatch(r"spark-optimisation-[A-Za-z0-9_.-]+\.(?:png|json)", name))
        else:
            allowed = name in PROFILE_FILES
        if not allowed:
            raise ValueError(f"Unexpected artifact file: {name}")
        source = folder.joinpath(*relative.parts)
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(folder):
            raise ValueError(f"Artifact source is not an ordinary contained file: {name}")
        size = source.stat().st_size
        if type(row.get("bytes")) is not int or size != row["bytes"] or size > MAX_FILE_BYTES:
            raise ValueError(f"Artifact byte size mismatch or oversized file: {name}")
        if not isinstance(row.get("sha256"), str) or sha256(source) != row["sha256"]:
            raise ValueError(f"Artifact checksum mismatch: {name}")
        listed.add(name)
        total += size
    actual = set()
    for path in folder.rglob("*"):
        if path.is_symlink():
            raise ValueError("Artifact trees cannot contain symbolic links")
        if path.is_file():
            actual.add(path.relative_to(folder).as_posix())
    if actual != listed | {manifest_name}:
        raise ValueError(f"Unmanifested or missing artifact files: {sorted(actual ^ (listed | {manifest_name}))}")
    if not optimisation and listed != PROFILE_FILES:
        raise ValueError("A scale artifact does not contain the complete aggregate evidence set")
    cap = 25_000_000 if optimisation else MAX_ARTIFACT_BYTES
    if total+ (folder/manifest_name).stat().st_size > cap:
        raise ValueError("Aggregate artifact size exceeds its publication cap")
    return listed, total


def finite_positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"Expected a finite positive {name}")
    return value


def validate_profile(folder):
    manifest = load(folder/"artifact_manifest.json")
    _, size = verify_inventory(folder, "artifact_manifest.json", manifest["files"])
    if manifest.get("payload_bytes") != size:
        raise ValueError("Artifact manifest byte total is inconsistent")
    measurement, generation = load(folder/"measurement.json"), load(folder/"generation_manifest.json")
    run = load(folder/"results/run_all.json")
    scale, revision = measurement.get("scale"), measurement.get("source_revision")
    if scale not in SCALES or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Scale or source revision is invalid")
    validate_completed(run, generation, scale, revision)
    encounter_files, stock_files = generation.get("encounter_files", []), generation.get("stock_files", [])
    if not encounter_files or not stock_files or sum(row["rows"] for row in encounter_files) != generation["encounter_rows"]:
        raise ValueError("Generation per-file encounter totals are inconsistent")
    if measurement.get("input_bytes") != sum(row["bytes"] for row in encounter_files+stock_files):
        raise ValueError("Measured input bytes differ from the generated feed manifest")
    if manifest.get("source_revision") != revision or measurement.get("status") != "ok":
        raise ValueError("Artifact and measurement source/status disagree")
    for key in ("scale", "seed", "generated_encounter_rows", "generated_unique_encounters", "facilities", "stock_rows", "output_bytes", "hardware"):
        if measurement.get(key) != run.get(key):
            raise ValueError(f"Measurement and pipeline disagree on {key}")
    elapsed = finite_positive(measurement.get("total_seconds"), "complete-command duration")
    inner = finite_positive(measurement.get("orchestrator_seconds"), "orchestrator duration")
    if inner != run.get("total_seconds") or inner > elapsed+1:
        raise ValueError("Outer and orchestrator timings are inconsistent")
    if measurement.get("under_45_minutes") is not (elapsed <= 2700):
        raise ValueError("The measured 45-minute acceptance flag is inconsistent")
    expected_stages = [{key: row[key] for key in ("stage", "seconds", "attempt", "status")} for row in run["stages"]]
    if measurement.get("stages") != expected_stages:
        raise ValueError("Measurement and pipeline stage timings disagree")
    github = measurement.get("github", {})
    if github.get("GITHUB_SHA") != revision or not str(github.get("GITHUB_RUN_ID", "")).isdigit():
        raise ValueError("GitHub run/source provenance is missing or inconsistent")
    payload, release = load(folder/"dashboard/data/dashboard.json"), load(folder/"dashboard/data/manifest.json")
    validate_release(payload)
    dashboard = measurement.get("dashboard", {})
    if dashboard != {"sha256": sha256(folder/"dashboard/data/dashboard.json"), "bytes": (folder/"dashboard/data/dashboard.json").stat().st_size}:
        raise ValueError("Protected dashboard differs from the measured release")
    if release.get("sha256") != dashboard["sha256"] or release.get("bytes") != dashboard["bytes"]:
        raise ValueError("Dashboard manifest does not identify its payload")
    if len(payload.get("districts", [])) != 64:
        raise ValueError("Dashboard does not contain the 64-district release")
    for name in ("run_all.json", "analytics_latest.json", "quality_latest.json", "evaluation_summary.json"):
        reject_record_identifiers(load(folder/"results"/name))
    summary = build_summary(folder/"results", folder/"generation_manifest.json")
    recorded = load(folder/"results/evaluation_summary.json")
    # Timestamps and portable source names can change during extraction; measured sections cannot.
    for key in ("generation", "run", "detection", "reporting_artefact", "forecasting", "allocation", "warnings", "status"):
        if summary[key] != recorded.get(key):
            raise ValueError(f"Packaged evaluation summary differs from its source aggregates: {key}")
    if manifest.get("evaluation_status") != summary["status"]:
        raise ValueError("Manifest evaluation status is inconsistent")
    return {"folder": folder, "measurement": measurement, "generation": generation, "summary": recorded}


def collect(directory):
    if directory.is_symlink():
        raise ValueError("Extraction root cannot be a symbolic link")
    directory = directory.resolve(strict=True)
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("Extracted evidence cannot contain symbolic links")
    profiles = {}
    for path in directory.rglob("artifact_manifest.json"):
        profile = validate_profile(path.parent)
        scale = profile["measurement"]["scale"]
        if scale in profiles:
            raise ValueError(f"More than one complete artifact for SF {scale}")
        profiles[scale] = profile
    if set(profiles) != set(SCALES):
        raise ValueError(f"Require exactly SF 0.25, 0.5 and 1; found {sorted(profiles)}")
    measurements = [profiles[scale]["measurement"] for scale in sorted(profiles)]
    for key in ("source_revision", "seed", "cache_archive_sha256"):
        if len({row.get(key) for row in measurements}) != 1:
            raise ValueError(f"The three scale runs use different {key}")
    run_ids = {row["github"]["GITHUB_RUN_ID"] for row in measurements}
    if len(run_ids) != 1:
        raise ValueError("The three scale runs must belong to one GitHub workflow run")
    captures = list(directory.rglob("capture_manifest.json"))
    if len(captures) > 1:
        raise ValueError("More than one optimisation artifact was supplied")
    optimisation = None
    if captures:
        path = captures[0]
        manifest = load(path)
        files, size = verify_inventory(path.parent, path.name, manifest["files"], optimisation=True)
        if manifest.get("source_revision") != measurements[0]["source_revision"] or manifest.get("preceding_pipeline_scale") != 1:
            raise ValueError("Optimisation evidence belongs to another source or scale")
        if manifest.get("preceding_pipeline_record_sha256") != next(row["sha256"] for row in measurements[-1]["source_files"] if row["path"] == "results/run_all.json"):
            raise ValueError("Optimisation evidence does not identify the completed SF1 pipeline")
        if manifest.get("artifact_bytes") != size:
            raise ValueError("Optimisation manifest byte total is inconsistent")
        if "optimisations.json" in files:
            validate_query_result(load(path.parent/"optimisations.json"))
        for name in files:
            if name.endswith("_plan.txt") and DIRECT_IDENTIFIER.search((path.parent/name).read_text(encoding="utf-8")):
                raise ValueError("Direct identifiers unexpectedly appear in a controlled query plan")
        optimisation = {"folder": path.parent, "manifest": manifest, "files": files}
    return profiles, optimisation


def promotion_files(profiles, optimisation):
    files = {}
    measurements = [profiles[scale]["measurement"] for scale in sorted(profiles)]
    run_id = measurements[0]["github"]["GITHUB_RUN_ID"]
    for scale, profile in profiles.items():
        for source in profile["folder"].rglob("*"):
            if source.is_file():
                files[Path("results/cloud")/SCALES[scale]/source.relative_to(profile["folder"])] = source.read_bytes()
    final = profiles[1.]["folder"]
    for name in ("evaluation_summary.json", "forecast_summary.csv", "detection_tradeoff.csv"):
        files[Path("results")/name] = (final/"results"/name).read_bytes()
    for name in ("dashboard.json", "manifest.json"):
        files[Path("dashboard/data")/name] = (final/"dashboard/data"/name).read_bytes()
    files[Path("results/scalability.json")] = encoded({
        "experiment_id": f"github-actions/{run_id}", "runs": measurements,
        "interpretation": "Three fresh generated-feed/lake runs with cached public references. Complete-command timings include Python/JVM startup and shutdown. Different runner hardware can affect scaling; earlier local baselines cannot isolate code speedup across hosts."})
    table = io.StringIO(newline="")
    fields = ["scale", "generated_unique_encounters", "generated_encounter_rows", "input_bytes", "output_bytes", "total_seconds", "under_45_minutes"]
    writer = csv.DictWriter(table, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows({key: row[key] for key in fields} for row in measurements)
    files[Path("results/cloud/scalability.csv")] = table.getvalue().encode("utf-8")
    if optimisation:
        for name in optimisation["files"] | {"capture_manifest.json"}:
            source = optimisation["folder"]/name
            files[Path("results/cloud/optimisations")/name] = source.read_bytes()
            if name == "optimisations.json" or name.endswith("_plan.txt"):
                files[Path("results/performance")/name] = source.read_bytes()
            elif name.startswith("spark-optimisation-") or name == "capture_manifest.json":
                files[Path("docs/evidence/optimisations")/name] = source.read_bytes()
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path, help="Parent of separately extracted artifact folders")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-only", action="store_true", help="Verify and describe evidence without writing (default)")
    mode.add_argument("--stage", action="store_true", help="Write intended promotion files under ignored work/ for review")
    mode.add_argument("--promote", action="store_true", help="Back up overwritten files, then import final aggregate evidence")
    args = parser.parse_args(argv)
    try:
        profiles, optimisation = collect(args.directory)
        files = promotion_files(profiles, optimisation)
        receipt = {"status": "validated", "mode": "promote" if args.promote else "stage" if args.stage else "validate-only",
                   "created_utc": datetime.now(timezone.utc).isoformat(),
                   "source_revision": profiles[1.]["measurement"]["source_revision"],
                   "github_run_id": profiles[1.]["measurement"]["github"]["GITHUB_RUN_ID"],
                   "scales": sorted(profiles), "sf1_under_45_minutes": profiles[1.]["measurement"]["under_45_minutes"],
                   "optimisation_status": optimisation["manifest"].get("status") if optimisation else "not_supplied",
                   "warnings": {str(scale): profile["summary"]["warnings"] for scale, profile in profiles.items()},
                   "files": [{"path": path.as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for path, data in sorted(files.items())]}
        if args.promote or args.stage:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = REPOSITORY/"work/cloud-import-backups"/stamp
            destination = REPOSITORY if args.promote else REPOSITORY/"work/cloud-import-staging"/stamp
            # Complete the backup before mutating any existing release file.
            if args.promote:
                for relative in files:
                    target = destination/relative
                    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent.is_relative_to(REPOSITORY)):
                        raise ValueError("Promotion destination contains a symbolic link")
                    if target.exists():
                        saved = backup/relative
                        saved.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, saved)
                receipt["backup_directory"] = str(backup)
            for relative, data in files.items():
                target = destination/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name+".import-tmp")
                temporary.write_bytes(data)
                temporary.replace(target)
            receipt["destination"] = str(destination)
            receipt_path = destination/"results/cloud/import_receipt.json"
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_bytes(encoded(receipt))
        print(encoded(receipt).decode(), end="")
    except (OSError, ValueError, TypeError, KeyError, StopIteration) as error:
        print(f"Validation artifacts were not imported: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
