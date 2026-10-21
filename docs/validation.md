# Validation record

This file distinguishes implemented code from executed evidence. Full integration and performance measurements are blocked by an unresponsive Ubuntu WSL1 host. On 21 October 2026 the small Spark pipeline suite completed, but its Java process did not exit cleanly and new WSL commands stopped responding. The WSL service remains in Stop Pending despite runtime recovery attempts. A Windows restart was requested from the user; no restart of Windows was initiated automatically. Source and data are saved on D:.

## Verified before integration

- Public inputs: 64 district geometries, 2,560 real WorldPop district/age/sex cells, and 116,864 real daily district weather observations covering 2021–2025.
- Demo generator SF=0.01: 252,391 distinct encounters and 5,067 exact resubmissions, 640 facilities, 1,168,640 daily stock snapshots, and 36 injected outbreak events (99 district-event rows). Generation took 21.252 seconds in the initial acquisition environment.
- Generator: five tests passed, including deterministic file hashes and explicit synthetic defects.
- Analytics: nine numerical invariant tests passed in the pinned WSL runtime. A subsequent threshold-grid optimization was checked with 39 independent mathematical equivalence assertions; its Python fixture still awaits rerun. This does not substitute for Spark integration.
- Pipeline: four adversarial Spark tests passed in 50.82 seconds in the pinned WSL runtime. They cover rule defects, null quarantine, salted hashing, immutable ingestion, no-op reruns, and reordered CSV columns. A NumPy long-double probe warning was emitted; numerical analytics use float64.
- Serving: fourteen privacy, API-scope, quality-release and payload tests passed on Windows with the bundled Python runtime after the WSL failure. JavaScript syntax and all Python sources compile. Automated browser interaction checks passed on an explicitly labelled fixture after adding the quality panel, including five D3 views, coordinated filters, threshold/budget controls, heatmap/choropleth interactions, a 390px mobile viewport and no JavaScript exceptions. Two independent non-CS user sessions have not been conducted.

## Pending execution

- Clean end-to-end Spark run and real dashboard release.
- Complete test suite in the pinned runtime.
- Incremental fixture, late-week changes, and repeat-load evidence.
- All three required scaling profiles and two measured Spark query comparisons.
- Spark UI screenshots and real dashboard browser screenshots.

## Recovery sequence

After Windows restarts, first confirm `wsl -d Ubuntu -- echo ready`, then run the complete tests and `bash run_wsl.sh all --scale 0.01`. Reuse the existing cached references and seeded feed. Inspect the real release before incremental and scaling experiments. If Java shutdown hangs again on WSL1, use a supported WSL2 or Linux notebook runtime rather than repeatedly claiming a successful local environment. Check and update this record only from actual outputs.

## Deliberate scope boundaries

Local filesystem Parquet is the working storage backend; HDFS/MinIO is not claimed. No ACID time travel, SEIR calibration, record-level extracts or cross-facility episode deduplication are claimed. Exact encounter-ID resubmissions are deduplicated. The API implements optional token-based local roles, not a production authentication service. Facility-report completeness is a relative reporting-activity proxy because a line list cannot distinguish a genuine zero-case report from an absent submission. The report, policy brief, slides and video are intentionally deferred.
