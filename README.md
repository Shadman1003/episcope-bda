# EpiScope

**Bangladesh disease surveillance, forecasting and response planning.** CSE 4345 Big Data Analytics, Premier University. Four-member group approved by the instructor, as confirmed by the team on 21 October 2026.

**Presentation/video: deferred. Technical report and policy brief: deferred.** This repository contains the implementation and technical documentation. The current validation state is recorded in `docs/validation.md`; do not treat an unexecuted benchmark or an automated usability check as completed field evidence.

All patient encounters, facilities, stocks and outbreak events are synthetic. District boundaries, age/sex population estimates and daily weather are attributable public inputs. This is a teaching prototype; its constructed disease burden and generic planning costs are not validated operational estimates.

## Run from Ubuntu WSL

Prerequisites: Ubuntu with Python 3.12, OpenJDK 11 or 17, curl, internet access for the first reference/dependency download, and sufficient disk space for generated data. Runtime is pinned in `requirements.txt`; all heavy transformations use PySpark 3.5.3. The first setup can take several minutes because Spark is a large download.

From a clean clone, one command builds public references, the full SF=1 synthetic feed, bronze/silver/gold tables, analytics and the privacy-protected dashboard release:

```bash
bash run_wsl.sh all --scale 1
```

For an initial smaller run:

```bash
bash run_wsl.sh all --scale 0.01
```

Use a separate `--root` for a different scale or seed; the immutable bronze history is retained. Do not reuse a demo root and silently label it as full scale. On this machine the working copy is at `/mnt/d/Study/8th Semester/BDA/Assignment/episcope`.

Start the dashboard:

```bash
bash run_wsl.sh serve --port 8765
```

Open <http://127.0.0.1:8765>. Use this server because it enforces the release and role access rules. It serves local vendored D3 v7 without a CDN. A plain static server bypasses the intended API access controls.

Run the production checks:

```bash
bash run_wsl.sh test
```

An optional [Docker environment](docs/docker.md) is included for Linux hosts. It has not been executed on this machine; the validation evidence applies to Ubuntu WSL.

## Hidden 14-day incremental drop (ING-4)

The drop may contain CSV, JSONL or newline-delimited JSON files in a directory, including `encounters/`, `stocks/` and optional facility registry additions. Filenames and dates are not hardcoded. Source type is identified from the schema; CSV header order can vary. The encounter contract is:

```text
encounter_id,patient_id,facility_id,onset_date,report_date,age_years,sex,
icd10_code,lab_confirmed,admitted,outcome
```

Stock fields: `facility_id,date,iv_saline,ns1_kits,ors,antimalarials,occupied_beds`. Facility registry fields: `facility_id,district_id,facility_name,facility_type,latitude,longitude,beds,icu_beds`. Dates use ISO `YYYY-MM-DD`; flags accept `true/false` or `1/0`. District codes are the stable two-digit keys in the published registry, not an assertion that these are official BBS district codes.

```bash
bash run_wsl.sh incremental --drop /absolute/path/to/hidden_drop
bash run_wsl.sh incremental --drop /absolute/path/to/hidden_drop
```

The second load is a checksum-based ingestion no-op. An incomplete preceding run is retried rather than incorrectly marked processed. Revised encounter IDs use the most recently ingested valid version, and late onsets update historical weeks. The conservative implementation rebuilds conformed marts from immutable bronze when an input changes. This favors correctness over minimum incremental compute; it is not an ACID/time-travel lake.

An adversarial local fixture can be generated without changing production code:

```bash
python tests/fixtures/create_hidden_drop.py --data-root data --output work/hidden_drop
bash run_wsl.sh incremental --drop work/hidden_drop
```

The fixture includes later dates, a late onset, exact resubmissions, quality defects and new stocks. It is not the instructor's hidden test.

## Data and architecture

```text
public geography / WorldPop / ERA5 + seeded encounters / facilities / stocks
     -> immutable bronze files + SHA256 manifest
     -> Spark quality rules, quarantine, salted patient hashes, revision dedup
     -> partitioned silver Parquet + registered Spark SQL tables
     -> dense daily/weekly district-disease gold + stock/quality/lineage
     -> as-of EARS C2 / CUSUM, climate forecasts, LP allocation, seasonal calendar
     -> bounded and suppressed aggregate release -> coordinated D3 views
```

See `generator/README.md` for data provenance, `docs/architecture.md` for design decisions, `analytics/METHODS.md` for methods and limitations, and `docs/dashboard.md` for disclosure control, interaction and role behavior. Original public downloads and generated feeds remain under `data/` and are excluded from Git. Downloaded source checksums and the synthetic generation manifest are kept locally for audit.

## Evaluation and scalability

```bash
bash run_wsl.sh benchmark
bash run_wsl.sh optimisations --root runs/EXPERIMENT_ID/sf1_0/data
```

The scaling runner executes SF in `{0.25,0.5,1}`, records actual stage runtime, output bytes and hardware, and records whether each run took at most 45 minutes. It does not replace execution with an extrapolated number. Generated profiles are kept separately in `runs/`. The controlled optimisation study compares a merge join with a broadcast join, and an opaque date predicate with native partition pruning; plans, three-query timings and matching results are retained. Query speedup is not a claim of equal whole-pipeline speedup.

## Team and contribution records

| Member | Student ID | Suggested lead area |
|---|---|---|
| Shadman Al Muksit | 0222220005101003 | Data engineering and integration |
| Md. Tarek Hossen | 0222220005101004 | Analytics and evaluation |
| Abir Ahamed Omi | 0222220005101021 | D3 dashboard and serving |
| Md. Sariful Islam | 0222220005101023 | Quality, privacy and performance |

These areas are suggestions for the team to agree. Each member should review the entire system and make meaningful, attributable changes. No student contribution percentages, signatures, reflections or retrospective commits have been invented. `AI_USAGE.md` discloses the implementation assistance. The final submission tag, report, policy brief and recorded presentation remain for the later handover phase.

The assignment originally requested a private repository and three members. The group size is instructor-approved; repository visibility follows the user's explicit request for a public repository in their own account. Teammates and instructor have not been added automatically.

## Requirement traceability

| Requirement | Implementation/evidence | Planned report location |
|---|---|---|
| ING-1 | `generator/`, `pipeline/run.py`, bronze manifest | Data and generation |
| ING-2 | `pipeline/`, Spark detection/cohorts/parallel forecast groups | Architecture |
| ING-3 | `pipeline/common.py`, partitioned Parquet and SQL catalog | Storage design |
| ING-4 | `pipeline/run.py`, processed signature and incremental fixture | Incremental evaluation |
| DQ-1 | `pipeline/quality.py`, quarantine and adversarial tests | Quality |
| DQ-2 | gold quality table, `results/quality_latest.json`, dashboard quality panel | Quality evaluation |
| DQ-3 | bronze SHA256 manifests, every gold row's `lineage_id` and immutable dependency manifest | Provenance |
| PRV-1 | `pipeline/quality.py`, salt outside Git working tree, age bands | Privacy |
| PRV-2 | `serving/privacy.py`, safe nonoverlapping bins and coupled suppression | Disclosure control |
| PRV-3 | `serving/server.py`, server-side district scope and denied static data | Access control |
| AN-1, AN-3 | `analytics/detection.py`, causal matured cohorts and backfill guard | Detection |
| AN-2 | `analytics/evaluate.py`, isolated ground truth and threshold curves | Detection evaluation |
| AN-4, AN-5 | `analytics/forecast.py`, 52 mature target weeks, horizons 1–4, baseline/MAE/coverage/WIS | Forecast evaluation |
| AN-6 | `analytics/optimise.py`, explicit teaching resource conversions and upper intervals | Demand |
| AN-7, AN-8 | HiGHS LP, population baseline and multi-budget equity grid | Allocation evaluation |
| AN-9 | `analytics/seasonal.py`, annual decomposition and calendar | Preparedness |
| VIS-1–VIS-4 | `dashboard/`, `serving/export.py`, five linked views and <=10MB guard | Dashboard |
| VIS-5–VIS-7 | scenario controls, accessible/mobile layout, uncertainty and freshness | Visualisation |
| ENG-1 | `run_wsl.sh`, pinned runtime, explicit DAG | Reproducibility |
| ENG-2 | `orchestration/performance.py`, measured `results/scalability.json` after execution | Performance |
| ENG-3 | controlled query plans/timing and captured Spark UI evidence after execution | Optimisation |
| ENG-4 | `tests/`, explicit DAG with bounded retries | Testing |

Additional S/C features such as HDFS/MinIO storage, a Structured Streaming path, cross-facility patient episode deduplication, ACID time travel and a calibrated SEIR scenario are not implied by the baseline implementation. Their current status is explicit in the validation notes.
