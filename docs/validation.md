# Validation record

This file distinguishes implemented code from executed evidence. Integration and full-scale performance measurements are in progress. It will be updated from actual run outputs before handover.

## Verified before integration

- Public inputs: 64 district geometries, 2,560 real WorldPop district/age/sex cells, and 116,864 real daily district weather observations covering 2021–2025.
- Demo generator SF=0.01: 252,391 distinct encounters and 5,067 exact resubmissions, 640 facilities, 1,168,640 daily stock snapshots, and 36 injected outbreak events (99 district-event rows). Generation took 21.252 seconds in the initial acquisition environment.
- Generator: five tests passed, including deterministic file hashes and explicit synthetic defects.
- Analytics: nine numerical invariant tests passed using the existing WSL numerical environment. This does not substitute for Spark integration.
- Serving: twelve privacy, API-scope and payload tests passed; automated browser interaction checks passed on an explicitly labelled fixture, including a 390px mobile viewport. Two independent non-CS user sessions have not been conducted.

## Pending execution

- Clean end-to-end Spark run and real dashboard release.
- Complete test suite in the pinned runtime.
- Incremental fixture, late-week changes, and repeat-load evidence.
- All three required scaling profiles and two measured Spark query comparisons.
- Spark UI screenshots and real dashboard browser screenshots.

## Deliberate scope boundaries

Local filesystem Parquet is the working storage backend; HDFS/MinIO is not claimed. No ACID time travel, SEIR calibration, record-level extracts or cross-facility episode deduplication are claimed. Exact encounter-ID resubmissions are deduplicated. The API implements optional token-based local roles, not a production authentication service. Facility-report completeness is a relative reporting-activity proxy because a line list cannot distinguish a genuine zero-case report from an absent submission. The report, policy brief, slides and video are intentionally deferred.
