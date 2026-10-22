#!/usr/bin/env python3
"""Retain verified aggregate validation evidence after CI artifacts expire.

This packages existing downloads only. It does not download, generate data,
change the dashboard release, or include failed/partial optimisation captures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import zipfile

REPOSITORY = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 10_000_000
MAX_ARCHIVE_BYTES = 50_000_000
PROFILE_FILES = {
    "measurement.json", "generation_manifest.json", "reference_provenance.json",
    "dashboard/data/dashboard.json", "dashboard/data/manifest.json",
    *(f"results/{name}" for name in (
        "evaluation_summary.json", "run_all.json", "analytics_latest.json",
        "quality_latest.json", "forecast_summary.csv", "detection_tradeoff.csv",
        "allocation_scenarios.csv")),
}
LABELS = (
    "join_before_sort_merge", "join_after_broadcast",
    "pruning_before_opaque_predicate", "pruning_after_native_predicate",
)
TEST_FILES = {"python-tests.txt", "source-revision.txt"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def ordinary_root(path):
    absolute = path.absolute()
    if any(linked(parent) for parent in (absolute, *absolute.parents)):
        raise ValueError("Evidence paths cannot use symbolic links or junctions")
    root = absolute.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Evidence root must be a directory")
    pending = [root]
    while pending:
        for child in pending.pop().iterdir():
            if linked(child):
                raise ValueError("Evidence trees cannot contain symbolic links or junctions")
            if child.is_dir():
                pending.append(child)
            elif not child.is_file():
                raise ValueError("Evidence trees must contain ordinary files only")
    return root


def relative_name(value):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("Evidence inventory path must be a plain relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or not path.parts or any(part in (".", "..") for part in path.parts):
        raise ValueError("Evidence inventory path is absolute, noncanonical or traverses directories")
    return path


def read_bounded(path, root):
    if linked(path) or not path.is_file() or not path.resolve(strict=True).is_relative_to(root):
        raise ValueError("Evidence file is not an ordinary contained file")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Evidence file exceeds 10 MB: {path.name}")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Evidence file changed beyond its size limit")
    return data


def load_bytes(data):
    def nonfinite(value):
        raise ValueError(f"Non-finite JSON number: {value}")
    value = json.loads(data.decode("utf-8-sig"), parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError("Evidence metadata must be a JSON object")
    return value


def validate_import(directory, optimisations):
    command = [sys.executable, str(REPOSITORY / "scripts/import_validation_artifacts.py"),
               "--directory", str(directory), "--optimisations-directory", str(optimisations), "--validate-only"]
    result = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError("Aggregate validation failed: " + (result.stderr.strip() or result.stdout.strip())[:1500])
    receipt = json.loads(result.stdout)
    if receipt.get("status") != "validated" or receipt.get("optimisation_status") != "complete":
        raise ValueError("Require validated scaling evidence and a complete independent optimisation capture")
    if not receipt.get("optimisation_study"):
        raise ValueError("Independent optimisation study provenance is missing")
    return receipt


def add_manifest_bundle(members, manifest_path, source_root, namespace, *, optimisation=False):
    folder = manifest_path.parent
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", folder.name):
        raise ValueError("Artifact folder name cannot be used as a portable archive path")
    prefix = f"{namespace}/{folder.name}"
    manifest_data = read_bounded(manifest_path, source_root)
    manifest = load_bytes(manifest_data)
    names = set()
    for row in manifest["files"]:
        name = row["file" if optimisation else "path"]
        relative = relative_name(name)
        if name in names or name == manifest_path.name:
            raise ValueError("Evidence inventory contains a duplicate or self-reference")
        if optimisation:
            allowed = name == "optimisations.json" or name in {f"{label}_plan.txt" for label in LABELS}
            allowed |= name in {f"{label}_sample-{sample}_plan.txt" for label in LABELS for sample in (1, 2, 3)}
            allowed |= bool(re.fullmatch(r"spark-optimisation-[A-Za-z0-9_.-]+\.(?:png|json)", name))
        else:
            allowed = name in PROFILE_FILES
        if not allowed:
            raise ValueError(f"Evidence inventory contains an unexpected file: {name}")
        data = read_bounded(folder.joinpath(*relative.parts), source_root)
        if type(row.get("bytes")) is not int or row["bytes"] != len(data) or row.get("sha256") != digest(data):
            raise ValueError("Evidence inventory changed after validation or has an invalid checksum")
        archive_name = f"{prefix}/{name}"
        if archive_name in members:
            raise ValueError("Artifact folders produce duplicate archive names")
        members[archive_name] = data
        names.add(name)
    if not optimisation and names != PROFILE_FILES:
        raise ValueError("Require the complete twelve-file aggregate profile")
    if optimisation and manifest.get("status") != "complete":
        raise ValueError("Partial optimisation artifacts cannot be archived")
    manifest_member = f"{prefix}/{manifest_path.name}"
    if manifest_member in members:
        raise ValueError("Duplicate artifact manifest archive name")
    members[manifest_member] = manifest_data
    return prefix, manifest


def verified_tests(directory, members):
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
    if actual != TEST_FILES:
        raise ValueError("Test artifact must contain exactly python-tests.txt and source-revision.txt")
    log = read_bounded(directory / "python-tests.txt", directory)
    lines = [line.strip() for line in log.decode("utf-8-sig").splitlines() if line.strip()]
    if len(lines) != 2 or not re.fullmatch(r"\.{41}\s+\[100%\]", lines[0]) or not re.fullmatch(r"41 passed in [0-9]+(?:\.[0-9]+)?s", lines[1]):
        raise ValueError("Test log must be the concise successful 41-test run, without failures, skips or extra output")
    revision = read_bounded(directory / "source-revision.txt", directory)
    source = revision.decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Test source revision must be a complete lowercase Git SHA")
    for name, data in (("python-tests.txt", log), ("source-revision.txt", revision)):
        members[f"tests/{name}"] = data
    run_id = next((match.group(1) for parent in (directory, *directory.parents)
                   if (match := re.fullmatch(r"run([0-9]+)", parent.name))), None)
    return {"source_revision": source, "passed": 41, "summary": lines[1], "github_run_id": run_id,
            "run_id_source": "extracted artifact parent directory" if run_id else "not recorded in the two-file artifact"}


def build_members(directory, optimisations, tests, receipt):
    profiles = sorted(directory.rglob("artifact_manifest.json"))
    study_profiles = list(optimisations.rglob("artifact_manifest.json"))
    captures = list(optimisations.rglob("capture_manifest.json"))
    if len(profiles) != 3 or len(study_profiles) != 1 or len(captures) != 1:
        raise ValueError("Require exactly three main core artifacts, one independent SF1 core and one independent capture")
    members, scaling = {}, []
    for path in profiles:
        prefix, manifest = add_manifest_bundle(members, path, directory, "scaling")
        measurement = load_bytes(members[f"{prefix}/measurement.json"])
        if manifest["source_revision"] != receipt["source_revision"] or measurement["github"]["GITHUB_RUN_ID"] != receipt["github_run_id"]:
            raise ValueError("Scaling source/run provenance changed after validation")
        scaling.append({"prefix": prefix, "scale": measurement["scale"], "github_run_id": measurement["github"]["GITHUB_RUN_ID"], "source_revision": manifest["source_revision"]})
    core_prefix, core_manifest = add_manifest_bundle(members, study_profiles[0], optimisations, "optimisations")
    capture_prefix, capture_manifest = add_manifest_bundle(members, captures[0], optimisations, "optimisations", optimisation=True)
    study = receipt["optimisation_study"]
    if core_manifest["source_revision"] != study["source_revision"] or capture_manifest["source_revision"] != study["source_revision"]:
        raise ValueError("Independent study source provenance changed after validation")
    core_measurement = load_bytes(members[f"{core_prefix}/measurement.json"])
    if core_measurement["github"]["GITHUB_RUN_ID"] != study["github_run_id"]:
        raise ValueError("Independent study workflow run changed after validation")
    test_proof = verified_tests(tests, members)
    provenance = {"scaling": sorted(scaling, key=lambda row: row["scale"]), "optimisation_study": study,
                  "optimisation_core_prefix": core_prefix, "optimisation_capture_prefix": capture_prefix, "tests": test_proof}
    return members, provenance


def publish(members, provenance, output):
    output = output.absolute()
    if output.suffix.lower() != ".zip":
        raise ValueError("Evidence archive output must end in .zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    if any(linked(path) for path in (output, output.parent, *output.parent.parents)):
        raise ValueError("Archive output cannot use symbolic links or junctions")
    manifest_path = output.with_suffix(".manifest.json")
    if linked(manifest_path):
        raise ValueError("Archive manifest output cannot be a symbolic link or junction")
    temporaries = []
    try:
        for target in (output, manifest_path):
            with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as temporary:
                temporaries.append(Path(temporary.name))
        with zipfile.ZipFile(temporaries[0], "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(members.items()):
                relative_name(name)
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compresslevel=9)
        archive_data = temporaries[0].read_bytes()
        if len(archive_data) >= MAX_ARCHIVE_BYTES:
            raise ValueError("Validation archive must be smaller than 50 MB")
        with zipfile.ZipFile(temporaries[0]) as archive:
            if archive.testzip() is not None or sorted(archive.namelist()) != sorted(members):
                raise ValueError("Completed archive failed integrity verification")
        manifest = {"schema_version": "1.0", "scope": "Validated aggregate evidence only: three-scale study, independent SF1 query/UI study, and successful 41-test proof. No clinical feeds, lake tables, failed captures, credentials or secrets.",
                    "archive_bytes": len(archive_data), "archive_sha256": digest(archive_data),
                    "payload_bytes": sum(map(len, members.values())), "provenance": provenance,
                    "files": [{"path": name, "bytes": len(data), "sha256": digest(data)} for name, data in sorted(members.items())],
                    "reproduction_commands": [
                        "python -m zipfile -e resources/validation-evidence.zip work/restored-validation",
                        "python scripts/import_validation_artifacts.py --directory work/restored-validation/scaling --optimisations-directory work/restored-validation/optimisations --validate-only",
                        "python scripts/import_validation_artifacts.py --directory work/restored-validation/scaling --optimisations-directory work/restored-validation/optimisations --stage"],
                    "reproduction_note": "Commands assume the default archive location and an empty extraction directory. Stage writes reviewable files under work/; it does not promote the dashboard. Test proof is retained unchanged under tests/."}
        temporaries[1].write_bytes(encoded(manifest))
        # Publish complete files by rename, with the descriptor last. Readers
        # verify its archive digest rather than accepting a partially copied ZIP.
        os.replace(temporaries[0], output)
        os.replace(temporaries[1], manifest_path)
        return manifest
    finally:
        for path in temporaries:
            path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="Three primary core artifacts")
    parser.add_argument("--optimisations-directory", type=Path, required=True, help="Independent SF1 core plus complete query/UI artifact")
    parser.add_argument("--tests-directory", type=Path, required=True, help="Successful two-file 41-test artifact")
    parser.add_argument("--output", type=Path, default=REPOSITORY / "resources/validation-evidence.zip")
    args = parser.parse_args(argv)
    try:
        directory, optimisations, tests = map(ordinary_root, (args.directory, args.optimisations_directory, args.tests_directory))
        receipt = validate_import(directory, optimisations)
        members, provenance = build_members(directory, optimisations, tests, receipt)
        manifest = publish(members, provenance, args.output)
        print(json.dumps({"status": "packaged", "archive": str(args.output), "archive_bytes": manifest["archive_bytes"], "archive_sha256": manifest["archive_sha256"], "files": len(members), "scaling_run_id": receipt["github_run_id"], "optimisation_run_id": provenance["optimisation_study"]["github_run_id"], "test_source_revision": provenance["tests"]["source_revision"]}, indent=2))
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError) as error:
        print(f"Validation evidence was not packaged: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
