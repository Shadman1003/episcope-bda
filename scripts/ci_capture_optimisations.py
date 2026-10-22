"""Measure controlled Spark queries, then capture their live UI for CI.

Run after the SF=1 `all` process exits. The measured query function returns
before Node/Chrome starts. The output directory contains only bounded aggregate
JSON, four representative and twelve per-sample deidentified physical plans,
screenshots and a provenance manifest.

Job-only browser dependency (verified npm latest on 2026-10-22):
PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-save --package-lock=false playwright@1.64.1
Primary package record: https://www.npmjs.com/package/playwright?activeTab=versions
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPOSITORY = Path(__file__).resolve().parents[1]
LABELS = (
    "join_before_sort_merge", "join_after_broadcast",
    "pruning_before_opaque_predicate", "pruning_after_native_predicate",
)
MAX_FILE_BYTES = 10_000_000
MAX_ARTIFACT_BYTES = 25_000_000
DIRECT_IDENTIFIER = re.compile(r"\b(?:patient_id|patient_name|encounter_id)\b|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY", re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_measurement_record(record, expected_scale):
    if record.get("status") != "ok":
        raise ValueError("The preceding all-pipeline measurement did not complete successfully")
    if not math.isclose(float(record.get("scale", -1)), expected_scale):
        raise ValueError("The preceding all-pipeline record has a different scale")
    if any(stage.get("result", {}).get("idempotent_noop") for stage in record.get("stages", [])):
        raise ValueError("An idempotent no-op is not a fresh preceding pipeline measurement")


def validate_query_result(result):
    if result.get("sampling", {}).get("fresh_query_execution_per_sample") is not True:
        raise ValueError("Query comparisons require fresh QueryExecutions for every sample")
    experiments = result.get("experiments", [])
    if sorted(row.get("label", "") for row in experiments) != sorted(LABELS):
        raise ValueError("Expected exactly the four controlled query experiments")
    for row in experiments:
        samples = row.get("seconds", [])
        if len(samples) != 3 or any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in samples):
            raise ValueError("Controlled timings must contain three actual finite samples")
        values = row.get("result", [])
        allowed = {"bed_weighted_encounters"} if row["label"].startswith("join_") else {"encounters"}
        if len(values) != 1 or set(values[0]) != allowed:
            raise ValueError("Only the one-row aggregate query results may be published")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values[0].values()):
            raise ValueError("A published query result contains a non-finite or nonnumeric value")
        records = row.get("samples", [])
        if row.get("sample_results_identical") is not True or len(records) != 3:
            raise ValueError("All three fresh samples must have matching aggregate results")
        for index, record in enumerate(records, start=1):
            if (record.get("sample") != index
                    or record.get("plan_file") != f'{row["label"]}_sample-{index}_plan.txt'
                    or not re.fullmatch(r"[0-9a-f]{64}", record.get("plan_sha256", ""))):
                raise ValueError("A fresh sample has invalid plan provenance")
            for key in ("query_execution_id", "physical_plan_id"):
                if type(record.get(key)) is not int or record[key] < 0:
                    raise ValueError("Fresh sample execution/plan IDs must be nonnegative integers")
    for key in ("query_execution_id", "physical_plan_id"):
        identifiers = [record[key] for row in experiments for record in row["samples"]]
        if len(set(identifiers)) != 12:
            raise ValueError("A timing sample reused a query execution or physical plan")
    comparisons = result.get("comparisons", [])
    if len(comparisons) != 2 or not all(row.get("identical_result") is True for row in comparisons):
        raise ValueError("Before/after queries did not produce matching aggregate results")


def copy_public_file(source, output, *, name=None):
    if source.is_symlink() or not source.is_file():
        raise ValueError("Public artifact sources must be ordinary files")
    size = source.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError("An evidence file exceeds the 10 MB per-file limit")
    current = sum(path.stat().st_size for path in output.iterdir() if path.is_file())
    if current + size > MAX_ARTIFACT_BYTES:
        raise ValueError("The evidence directory exceeds the 25 MB aggregate limit")
    target = output / (name or source.name)
    if target.exists():
        raise ValueError("Refusing to overwrite an existing artifact file")
    shutil.copyfile(source, target)
    return {"file": target.name, "bytes": size, "sha256": sha256(target)}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Completed SF=1 data root")
    parser.add_argument("--output", type=Path, required=True, help="Dedicated empty safe-artifact directory")
    parser.add_argument("--pipeline-record", type=Path, help="Defaults to ROOT/../results/run_all.json")
    parser.add_argument("--expected-scale", type=float, default=1.)
    parser.add_argument("--node", default="node")
    parser.add_argument("--chrome-channel", default="chrome")
    parser.add_argument("--chrome-executable", type=Path)
    parser.add_argument("--allow-partial", action="store_true", help="Return success for honest partial UI capture")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    root = args.root.resolve(strict=True)
    source_record = (args.pipeline_record or root.parent / "results" / "run_all.json").resolve(strict=True)
    pipeline = json.loads(source_record.read_text(encoding="utf-8"))
    validate_measurement_record(pipeline, args.expected_scale)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Use a dedicated empty artifact directory for this execution")
    node = shutil.which(args.node)
    if not node:
        raise RuntimeError("Node.js is unavailable")
    dependency = subprocess.run([node, "-e", "require.resolve('playwright')"], cwd=REPOSITORY, capture_output=True, timeout=10)
    if dependency.returncode:
        raise RuntimeError("Install job-only playwright@1.64.1 before measuring the controlled queries")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    capture_script = REPOSITORY / "tests" / "capture_optimisation_ui.cjs"
    manifest = {
        "status": "starting", "started_at": now(), "source_revision": revision,
        "preceding_pipeline_record_sha256": sha256(source_record),
        "preceding_pipeline_scale": pipeline["scale"],
        "preceding_pipeline_total_seconds": pipeline.get("total_seconds"),
        "timing_scope": "The all-pipeline process finished before this step. Node/Chrome starts only after all controlled-query samples complete. UI capture, artifact copy and Spark shutdown are outside measured query timings.",
        "artifact_policy": "Aggregate query results, four representative and twelve per-sample deidentified plans and bounded live UI PNG/JSON only; no encounters, salts, credentials, event logs or lake files.",
        "files": [],
    }
    spark = None
    stage = "spark_startup"
    exit_code = 1
    # Importing this file or requesting --help does not start or import Spark.
    sys.path.insert(0, str(REPOSITORY))
    try:
        from pipeline.common import make_spark
        from orchestration.performance import benchmark_optimisations
        spark = make_spark(root, app="EpiScope controlled optimisation evidence")
        spark.sparkContext.setLogLevel("WARN")
        manifest["application_id"] = spark.sparkContext.applicationId
        manifest["spark_ui"] = spark.sparkContext.uiWebUrl
        manifest["spark_master"] = spark.sparkContext.master
        manifest["driver_memory"] = spark.conf.get("spark.driver.memory")
        stage = "controlled_query_measurement"
        manifest["measurement_started_at"] = now()
        started = time.monotonic()
        measured = benchmark_optimisations(spark, root)
        manifest["measurement_wall_seconds"] = time.monotonic() - started
        manifest["measurement_completed_at"] = now()
        validate_query_result(measured)
        # Only known one-row aggregate results are published. The actual UI URL
        # can differ from the benchmark module's conventional 4040 default.
        measured = {**measured, "spark_ui": spark.sparkContext.uiWebUrl,
                    "application_id": spark.sparkContext.applicationId,
                    "source_revision": revision,
                    "measurement_completed_at": manifest["measurement_completed_at"]}
        write_json(output / "optimisations.json", measured)
        manifest["files"].append({"file": "optimisations.json", "bytes": (output / "optimisations.json").stat().st_size, "sha256": sha256(output / "optimisations.json")})
        stage = "physical_plan_publication"
        performance = root.parent / "results" / "performance"
        for row in measured["experiments"]:
            plans = [(f'{row["label"]}_plan.txt', row["samples"][-1]["plan_sha256"])]
            plans += [(record["plan_file"], record["plan_sha256"]) for record in row["samples"]]
            for name, expected_hash in plans:
                plan = performance / name
                if sha256(plan) != expected_hash:
                    raise ValueError("A sample/representative physical plan checksum is inconsistent")
                if DIRECT_IDENTIFIER.search(plan.read_text(encoding="utf-8")):
                    raise ValueError("An unexpected direct identifier marker appears in a query plan")
                manifest["files"].append(copy_public_file(plan, output))
        stage = "live_ui_capture_after_measurement"
        with tempfile.TemporaryDirectory(prefix="episcope-ui-") as directory:
            temporary = Path(directory).resolve()
            if temporary.parent != Path(tempfile.gettempdir()).resolve() or not temporary.name.startswith("episcope-ui-"):
                raise ValueError("Unexpected temporary capture directory")
            ready, done, screenshots = temporary / "ready.json", temporary / "captured.json", temporary / "screenshots"
            screenshots.mkdir()
            write_json(ready, {"application_id": spark.sparkContext.applicationId,
                               "ui": spark.sparkContext.uiWebUrl,
                               "source_revision": revision,
                               "measurement_completed_at": manifest["measurement_completed_at"]})
            command = [node, str(capture_script), "--ready-file", str(ready),
                       "--done-file", str(done), "--evidence-dir", str(screenshots),
                       "--chrome-channel", args.chrome_channel, "--budget-ms", "45000"]
            if args.chrome_executable:
                command += ["--chrome-executable", str(args.chrome_executable.resolve())]
            manifest["capture_started_at"] = now()
            try:
                captured = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, timeout=55)
                manifest["capture_process_returncode"] = captured.returncode
                if captured.stdout:
                    print(captured.stdout, end="", flush=True)
                if captured.stderr:
                    print(captured.stderr, end="", file=sys.stderr, flush=True)
            except subprocess.TimeoutExpired:
                manifest["capture_process_returncode"] = None
                manifest["capture_timeout_seconds"] = 55
            manifest["capture_completed_at"] = now()
            for source in sorted(screenshots.iterdir()):
                if source.name.startswith("spark-optimisation-") and source.suffix in {".png", ".json"}:
                    manifest["files"].append(copy_public_file(source, output))
            completion = json.loads(done.read_text(encoding="utf-8")) if done.exists() else {}
            manifest["ui_capture_status"] = completion.get("status", "failed")
            manifest["captured_labels"] = completion.get("captured_labels", [])
            manifest["missing_labels"] = [label for label in LABELS if label not in manifest["captured_labels"]]
            complete = manifest["ui_capture_status"] == "complete" and not manifest["missing_labels"] and manifest["capture_process_returncode"] == 0
            manifest["status"] = "complete" if complete else "partial"
            exit_code = 0 if complete or (args.allow_partial and manifest["captured_labels"]) else 1
    except Exception as error:
        manifest["status"] = "failed"
        manifest["failure_stage"] = stage
        manifest["failure_type"] = type(error).__name__
        print(f"Optimisation evidence failed during {stage}: {type(error).__name__}", file=sys.stderr, flush=True)
    finally:
        if spark is not None:
            # Keep measured evidence and its manifest if a JVM shutdown fails.
            # A shutdown failure still fails this step and is stated explicitly.
            try:
                spark.stop()
                manifest["spark_stopped_at"] = now()
            except Exception as error:
                manifest["spark_shutdown_failure_type"] = type(error).__name__
                manifest["status"] = "failed"
                manifest.setdefault("failure_stage", "spark_shutdown")
                exit_code = 1
        manifest["completed_at"] = now()
        manifest["artifact_bytes"] = sum(path.stat().st_size for path in output.iterdir() if path.is_file())
        write_json(output / "capture_manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "application_id": manifest.get("application_id"),
                      "captured_labels": manifest.get("captured_labels", []),
                      "missing_labels": manifest.get("missing_labels", list(LABELS)),
                      "artifact_files": len(manifest["files"]), "artifact_bytes": manifest["artifact_bytes"]}), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
