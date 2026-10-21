# Validation record

Updated 22 October 2026. The Ubuntu WSL1 runtime recovered after the user restarted Windows. The SF=0.01 end-to-end run, complete automated test suite and incremental acceptance checks have since completed. Fresh SF 0.25, 0.5 and 1 experiments are in progress; their results and measured Spark optimisation comparisons remain pending below.

## Executed evidence

| Check | Recorded result | Evidence |
|---|---|---|
| SF=0.01 `all` run | Passed in **495.682 s**; reference acquisition, generation entry point, Spark pipeline, analytics and export completed | [`results/run_all.json`](../results/run_all.json) |
| Complete Python suite | **37 passed** in **97.23 s**, one NumPy warning | [`docs/evidence/python-tests.txt`](evidence/python-tests.txt) |
| Fourteen-day incremental CLI run | Pipeline, analytics and export passed in **348.137 s** | [`results/run_incremental.json`](../results/run_incremental.json) |
| Adversarial incremental acceptance | Passed: valid records, late-week recomputation, quarantine, manifest lineage, restored SQL views and exact-repeat no-op | [`results/incremental_acceptance.json`](../results/incremental_acceptance.json) |
| Actual-release browser/API checks | Passed on the initial exported gold release; linked views, controls, responsive layouts and access scope | [`docs/evidence/dashboard-browser-qa.json`](evidence/dashboard-browser-qa.json), [`browser-run-results.json`](evidence/browser-run-results.json) |

The recorded host had Python 3.12.3, four logical CPUs and 8,464,728,064 bytes of RAM, with Spark `local[2]` and a 1 GiB driver heap. These are the measured settings, rather than a claim about a 16 GB reference machine. The test warning concerns NumPy's `longdouble` type probe; the numerical analytics use float64.

The 495.682-second result reused the seeded SF=0.01 feed and cached public downloads. Its generator entry point took 2.024 seconds and skipped regenerating matching files. Spark transformations and analytics executed, but this is **not** a fresh full-scale performance measurement. Recorded stages were 8.014 seconds for references, 196.122 seconds for bronze/silver/gold, 251.355 seconds for analytics and 17.372 seconds for export; total time also includes startup and orchestration overhead.

Verified demo inputs are 64 district geometries, 2,560 public WorldPop district/age/sex cells and 116,864 public daily district weather observations for 2021-2025. The seeded generator manifest records 252,391 unique synthetic encounters, 5,067 exact resubmissions, 640 synthetic facilities, 1,168,640 stock snapshots and 36 injected outbreak events represented by 99 district-event rows. Initial generation took 21.252 seconds. These are constructed teaching data, not observed disease burden.

## Incremental acceptance

The local fourteen-day fixture is separate from the instructor's hidden assessment. The completed CLI run extended the onset range through 14 January 2026 and regenerated analytics and the dashboard. A subsequent acceptance harness added orphan, negative and duplicate stock examples, then checked the production pipeline and its exact rerun.

The acceptance record contains:

- **248,507** valid encounters, including **610** new valid encounter identities, and **1,168,649** valid stocks, including **9** new facility/date snapshots.
- Six defective encounter rows and twelve duplicate fixture rows in encounter quarantine; one orphan stock row, one negative stock row and one duplicate stock snapshot in the stock quarantine tables.
- A late influenza onset in the week beginning **22 December 2025** changing the checked district/week count from **0 to 1**.
- An exact repeat returning `idempotent_noop: true`, zero new batches and **0.0781248 s**, with encounter counts unchanged. This is the pipeline no-op timing; it does not include another analytics/export run.
- Unique retained encounter IDs and removal of `patient_id` and `age_years` from silver encounters.
- A `lineage_id` column in every gold table, sampled lineage IDs resolving to saved manifests, and the restored `gold_weekly` SQL view matching the persisted table count.

The acceptance pipeline reconciled 1,426,738 encounter/stock input rows to 1,417,156 retained rows and 9,582 quarantined rows. **Duplicates are a subset of quarantine**: 4,993 duplicate or superseded rows are included in that 9,582 and must not be added again. Rule counts can overlap when one row violates several rules. The older first-run quality summary predates combined stock/duplicate accounting and should not be compared as though its quarantine definition were identical.

Each published gold table points to the manifest used when that table was written. The final acceptance harness refreshed pipeline marts without rerunning analytics; its record therefore contains different valid lineage IDs for those stages. It verifies resolvable dependencies, not that every table was republished in one transaction.

## Dashboard evidence

Automated checks used the actual baseline release with SHA256 `a362ba70111874d39fd58c602874c1cc446557178164df2c915a0cce02aaf825` and a 2,373,464-byte main payload. They checked all five views, coordinated selections, forecast navigation, detector and allocation controls, keyboard interaction, 390/768/1440-pixel layouts, scoped API responses, denied static-release access and absence of browser exceptions. The incremental run subsequently exported a 2,435,880-byte payload; baseline browser evidence must not be presented as a new browser run against that later hash.

Desktop, mobile and district-scope screenshots and initial Spark UI captures are retained in [`docs/evidence/`](evidence/). Initial application captures establish that the application ran; they do not replace pending before/after optimisation measurements. **Two independent non-CS usability sessions have not been conducted.** Automated interaction checks do not establish user comprehension or field usability.

## Scaling and optimisation evidence pending

The scaling runner creates a fresh timestamped directory for each experiment and separate SF 0.25, 0.5 and 1 profiles. It reuses public downloads, generates a fresh synthetic feed and lake, and rejects an idempotent no-op as a substitute for measured transformations. Runs execute serially on this host.

<!-- SCALING_EVIDENCE_START: replace pending cells only from completed run records. -->
| Profile | Status | Generated encounter rows | Total seconds | Lake output bytes | Within 45 minutes |
|---|---|---|---|---|---|
| SF=0.25 | Pending | Pending | Pending | Pending | Pending |
| SF=0.5 | Pending | Pending | Pending | Pending | Pending |
| SF=1 | Pending | Pending | Pending | Pending | Pending |
<!-- SCALING_EVIDENCE_END -->

<!-- OPTIMISATION_EVIDENCE_START: add actual matching-result checks, medians, plans and application screenshots. -->
The merge/broadcast join and opaque/native partition-predicate comparisons are implemented but their measured before/after results are pending. Record query medians and matching outputs under `results/performance/`, with Spark UI evidence for the corresponding application. Query speedup must remain separate from whole-pipeline runtime.
<!-- OPTIMISATION_EVIDENCE_END -->

The SF=1 45-minute criterion is not established by the demo, an extrapolation or a partial scaling run. Completed records belong in `results/scalability.json`; failures, retries, stage times, input/output sizes and hardware must remain visible. Update this section when those records finish.

## Scope boundaries and remaining validation

The optional Docker files are **untested**: no image build, Compose execution or container performance run has completed on this host. See [`docs/docker.md`](docker.md). The executed environment above is Ubuntu WSL.

Local filesystem Parquet is the working storage backend. HDFS/MinIO, Structured Streaming, ACID time travel, calibrated SEIR scenarios, record-level extracts and cross-facility patient-episode deduplication are not implemented claims. Exact encounter-ID resubmissions and superseded valid versions are handled separately from cross-facility episode matching. As-of analytical cohorts use retained report dates; they do not reconstruct overwritten ingestion/revision history.

The API provides optional local bearer-token roles, rather than production authentication. Facility completeness is a relative reporting-activity proxy: an encounter line list alone cannot distinguish a genuine zero-case submission from an absent report. Treatment conversions and budget units require local validation, and continuous bed/team capacity is not a dispatch manifest.

The technical report, policy brief, slides, signed contribution records, individual reflections and recorded presentation remain deferred. No human usability sessions or unexecuted optional features should be inferred from the automated evidence.
