# EpiScope

**Bangladesh disease surveillance, forecasting and response planning.** CSE 4345 Big Data Analytics, Premier University.

This repository contains the implementation and technical documentation. The current validation state is recorded in `docs/validation.md`; do not treat an unexecuted benchmark or an automated usability check as completed field evidence.

All patient encounters, facilities, stocks and outbreak events are synthetic. District boundaries, age/sex population estimates and daily weather are attributable public inputs. This is a teaching prototype; its constructed disease burden and generic planning costs are not validated operational estimates.

## Open the dashboard

The instructor can view the completed dashboard without rebuilding the 25-million-row dataset. Only Python 3.12 or newer and a browser are needed; no account, API key, Java, Spark, Node.js or Python package installation is required for this preview.

### First visit on Windows

Install Python if needed using the [official Windows instructions](https://docs.python.org/3.12/using/windows.html). Obtain this repository using either Git or the green **Code > Download ZIP** button on GitHub. Extract the ZIP completely before running anything.

With Git installed, open PowerShell and run:

```powershell
git clone https://github.com/Shadman1003/episcope-bda.git
cd episcope-bda
py -3 -m serving.server --port 8765
```

For Download ZIP, open PowerShell in the extracted `episcope-bda-main` folder containing `README.md` and run the last command only. If `py` is unavailable but `python --version` shows Python 3.12 or newer, use `python -m serving.server --port 8765` instead.

### First visit on Linux or macOS

```bash
git clone https://github.com/Shadman1003/episcope-bda.git
cd episcope-bda
python3 -m serving.server --port 8765
```

If using Download ZIP, enter its extracted folder and run the final command. The preview server is portable Python; full backend rebuilds use Linux/WSL as documented below.

### Confirm that it works

Keep the terminal open and visit **http://127.0.0.1:8765** in a browser on the same PC. Expect the EpiScope national overview, 64 districts, disease selectors and linked charts. The health endpoint http://127.0.0.1:8765/api/health should report `ok: true` and `data_ready: true`. Stop the server with **Ctrl+C**. Your PC does not need Shadman's D: folder or his Python environment.

If the port is busy, use `--port 8766` and open that port instead. If the module is missing, change into the folder containing `README.md`, `serving/` and `dashboard/`. If data is unavailable, confirm both `dashboard/data/dashboard.json` and `manifest.json` were included in the clone/extracted ZIP. A localhost link works while the server is running on that PC; GitHub itself is not hosting this dashboard.

The repository includes a protected aggregate release. The generic preview command is:

```bash
python -m serving.server --port 8765
```

Open <http://127.0.0.1:8765>. Preview uses the Python standard library and the vendored D3 v7.9.0 bundle; Spark, Java, dependency installation and a CDN are unnecessary. The included JSON contains suppressed and binned aggregate products, without patient-level records. Use the supplied server because it enforces API access and district-scope rules.

## Rebuild from Ubuntu WSL or Linux

Prerequisites: Python 3.12, OpenJDK 11 or 17, curl, internet access for the first reference/dependency download, and disk space for generated feeds and lake files. The numerical and Spark environment is pinned in `requirements.txt`; heavy transformations use PySpark 3.5.3. Initial setup includes downloading Spark.

For a concrete matching environment, use **Ubuntu 24.04 with Python 3.12**. On Windows, install that distribution under WSL2 using [Microsoft's WSL instructions](https://learn.microsoft.com/en-us/windows/wsl/install); run the following commands in the Ubuntu terminal, not PowerShell:

```bash
sudo apt update
sudo apt install -y python3 python3-venv openjdk-17-jre-headless curl git
python3 --version
java -version
git clone https://github.com/Shadman1003/episcope-bda.git
cd episcope-bda
```

Confirm Python reports 3.12 and Java reports 11 or 17. Use a fresh clone for a new scale. A 16 GB machine and at least 15 GB of free working space are practical starting points; peak usage and runtime depend on the host. The shipped licensed reference cache can avoid downloading the reference datasets again:

```bash
python3 -m zipfile -e resources/public-reference-cache.zip data
```

This optional step restores public reference files only. The build below still generates encounters and processes the lake. `run_wsl.sh` creates the pinned virtual environment automatically. The measured 11.25-minute result excludes first-time dependency installation/cache extraction and is not a promise for another PC.

From a clean clone, one command acquires public references, generates the full SF=1 synthetic feed, builds bronze/silver/gold tables, runs analytics and exports the protected dashboard:

```bash
bash run_wsl.sh all --scale 1
```

For a smaller initial build, use `bash run_wsl.sh all --scale 0.01`. Keep different scales and seeds in separate roots; immutable bronze history is retained. On the existing D: working copy, the local `data/` backend remains the SF=0.01 incremental acceptance study. It is separate from the shipped SF=1 dashboard and downloaded full-scale aggregate evidence in `results/cloud/`. Do not label the preserved local backend as SF=1 or overwrite it to preview the dashboard. A fresh full-scale backend can be built in a separate profile:

```bash
mkdir -p runs/full-rebuild/dashboard
cp -a dashboard/. runs/full-rebuild/dashboard/
bash run_wsl.sh all --scale 1 --root "$PWD/runs/full-rebuild/data"
```

A separate-root build writes its results and dashboard data beside that data root. The copy supplies the frontend assets; the build replaces its aggregate JSON with the newly generated release. Preview it with `python -m serving.server --root runs/full-rebuild/dashboard --port 8765`.

Run the complete Python checks with:

```bash
bash run_wsl.sh test
```

The existing Windows WSL1 instance became unresponsive after a successful local test run. Full-scale validation therefore uses actual Ubuntu GitHub Actions runners, as recorded below. Recovering that local subsystem is an environment task; the standard-library dashboard preview does not depend on WSL. An optional [Docker environment](docs/docker.md) is supplied, with its untested status documented.

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

## Measured results

The primary fresh profiles ran in [GitHub Actions run 39705416049](https://github.com/Shadman1003/episcope-bda/actions/runs/39705416049) at source `b25ea19783c9ad49a7d55fb206a7311abe079c66`, with seed 4345. Every profile generated a fresh encounter feed and lake while reusing checksummed public references.

| SF | Unique generated encounters | Submitted encounter rows | Complete command, seconds | Lake output, bytes |
|---|---:|---:|---:|---:|
| 0.25 | 6,286,618 | 6,412,101 | 319.112 | 959,030,026 |
| 0.5 | 12,606,672 | 12,858,721 | 470.751 | 1,889,522,728 |
| 1 | 25,204,173 | 25,708,357 | 674.915 | 3,729,563,409 |

SF=1 completed in **11.25 minutes**, meeting the 45-minute target on the recorded runner. These are complete child-command times, including Python/JVM startup and shutdown; dependency installation and reference-cache extraction are excluded. Python 3.12.3, Java 11, Spark 3.5.3, `local[4]`, a 4 GiB driver heap and 16 shuffle partitions were used. Runners exposed four logical CPUs and about 16.76 GB RAM; SF 0.25/0.5 used AMD EPYC 7763, while SF=1 used Intel Xeon Platinum 8573C. Host differences and caches affect comparisons; these measurements do not establish an isolated whole-pipeline code speedup. Full stages, input/output sizes and hardware are in [`results/scalability.json`](results/scalability.json).

The complete suite passed **41 tests in 36.36 seconds** in [test run 39706621713](https://github.com/Shadman1003/episcope-bda/actions/runs/39706621713), at source `bb16e184758956f8fbb1c6c497420f82ff7648d9`. A workflow-only follow-up corrected Spark worker Python selection; the measured backend implementation is unchanged. Final browser/API checks passed on the actual 2,568,942-byte SF=1 release, including five coordinated views, 390/768/1440-pixel layouts, keyboard controls, 14 detector settings, 24 allocation scenarios, six budget curves and district access boundaries. See [`docs/validation.md`](docs/validation.md) for release/source hashes and executed evidence.

The full-scale forecast backtest covers 52 mature target weeks, four diseases and horizons 1â€“4, with 53,248 scored district-week prediction records per model:

| Model | MAE | 80% interval coverage | WIS |
|---|---:|---:|---:|
| Lagged-weather ridge | 56.730 | 0.7782 | 47.154 |
| Seasonal naive | 97.564 | 0.7710 | 81.991 |

The ridge model reduces pooled MAE by 41.85% and WIS by 42.49% relative to seasonal naive; coverage remains below the nominal 0.80. These are pooled district-level prediction errors, not errors of summed national forecasts. Calibration selected CUSUM threshold 10: it detected 26/26 calibration events and 10/10 holdout events, with a seven-day median issue-date delay and 4.562 false-alert episodes per district-year on holdout. The holdout contains only ten constructed events. Display defaults remain EARS C2 at 3 and CUSUM at 5; evaluation does not silently replace them.

At budget 74,979.584 teaching units and a 20% population equity floor, the LP's weighted unmet demand is 20,559.521 versus 38,843.235 for the population baseline, a 47.07% reduction. These are synthetic planning results with generic costs and continuous capacity, requiring local validation before operational use. National metrics, calibration/holdout rows and the complete 24-scenario grid are in [`results/evaluation_summary.json`](results/evaluation_summary.json).

The independent full-scale query/UI study retained twelve fresh samples, matching outputs, sixteen physical plans and all four corresponding Spark UI captures. Its measured medians are:

| Controlled comparison | Before median (s) | After median (s) | Ratio |
|---|---:|---:|---:|
| Registry join: merge to broadcast | 6.640693 | 1.447631 | 4.59x |
| Year predicate: Python UDF to native pruning | 19.749418 | 0.062374 | 316.63x |

The partition query selects a small 21,904-record ISO-year edge partition. These query-specific ratios are not whole-pipeline speedups. The [validation record](docs/validation.md) identifies the separate study source, hardware, sample timings and initial failures/corrections.

## Reproduce and inspect the evidence

[`resources/validation-evidence.zip`](resources/validation-evidence.zip) retains the bounded aggregate profiles, corrected query evidence and successful test log after the temporary CI artifacts expire. Its companion manifest records archive/member checksums. It contains public provenance and protected aggregates; raw clinical feeds, lake records, salts and credentials are excluded. Readable provenance and measurement copies are under [`docs/evidence/cloud/`](docs/evidence/cloud/).

After extracting the archive into a new folder, verify all three profiles without changing the working release:

```bash
python -m zipfile -e resources/validation-evidence.zip work/validation-evidence
python scripts/import_validation_artifacts.py --directory work/validation-evidence/scaling --optimisations-directory work/validation-evidence/optimisations --validate-only
```

The importer checks file inventories, SHA256/size, exact scale/source/count consistency, evaluation regeneration and release privacy. `--stage` prepares a separate review copy. `--promote` backs up overwritten release files under ignored `work/` before importing, while preserving the local demo backend and incremental evidence.

Fresh local experiments can be run with `bash run_wsl.sh benchmark`. The controlled query study uses `bash run_wsl.sh optimisations --root runs/EXPERIMENT_ID/sf1_0/data`. It compares sort-merge/broadcast joins and opaque/native partition predicates with three fresh executions per case. Timings, per-sample execution IDs and plans belong in [`results/performance/`](results/performance/); query speedups have a narrower scope than the end-to-end run.


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
| AN-4, AN-5 | `analytics/forecast.py`, 52 mature target weeks, horizons 1â€“4, baseline/MAE/coverage/WIS | Forecast evaluation |
| AN-6 | `analytics/optimise.py`, explicit teaching resource conversions and upper intervals | Demand |
| AN-7, AN-8 | HiGHS LP, population baseline and multi-budget equity grid | Allocation evaluation |
| AN-9 | `analytics/seasonal.py`, annual decomposition and calendar | Preparedness |
| VIS-1â€“VIS-4 | `dashboard/`, `serving/export.py`, five linked views and <=10MB guard | Dashboard |
| VIS-5â€“VIS-7 | scenario controls, accessible/mobile layout, uncertainty and freshness | Visualisation |
| ENG-1 | `run_wsl.sh`, pinned runtime, explicit DAG | Reproducibility |
| ENG-2 | `orchestration/performance.py`, measured `results/scalability.json` after execution | Performance |
| ENG-3 | controlled query plans/timing and captured Spark UI evidence after execution | Optimisation |
| ENG-4 | `tests/`, explicit DAG with bounded retries | Testing |

Additional S/C features such as HDFS/MinIO storage, a Structured Streaming path, cross-facility patient episode deduplication, ACID time travel and a calibrated SEIR scenario are not implied by the baseline implementation. Their current status is explicit in the validation notes.
