# EpiScope: technical proposal

**Course:** CSE 4345, Big Data Analytics. **Setting:** hypothetical Bangladesh disease-surveillance teaching case. **Team:** four members, with instructor approval recorded in `team.json`. Final report, policy brief and presentation are deferred.

## Problem and intended users

Health, climate and logistics feeds arrive in different formats, with duplicates, missing values and delays. EpiScope combines them into district-level surveillance for dengue, diarrhoeal disease, influenza and malaria across all 64 districts. It detects unusual rises, forecasts cases and admissions one to four weeks ahead, compares constrained resource-allocation policies, and produces a division-level preparedness calendar. Policymakers need a concise national picture; epidemiologists need transparent thresholds; district officers and hospitals need local demand, stock and uncertainty. Patients and regulators need privacy, limited data release and clear disclosure that the encounter data are synthetic.

## Architecture, methods and trade-offs

The implementation uses cached public geography, population and five-year climate references with a deterministic synthetic encounter, facility and stock generator. Apache Spark performs heterogeneous ingestion, rule-based quarantine, salted patient hashing, encounter-level deduplication and dense district/disease aggregation. Immutable bronze bytes and SHA256 manifests support provenance. Silver and gold use partitioned Parquet and Spark SQL tables. Daily batch processing matches procurement and district review decisions; incremental drops reuse the same pipeline and retain source lineage.

EARS C2 and CUSUM monitor seven-day-mature onset cohorts. A feed-derived backfill guard limits reporting artefacts. Detection is evaluated separately against truth; calibration and held-out threshold curves quantify sensitivity, false episodes per district-year and actual issue-date delay. Direct ridge models use available case histories, seasonal harmonics and climate lags of four, six and eight weeks. Parallel district models receive a 52-week rolling-origin evaluation against seasonal naive forecasts, using MAE, nominal 80% coverage and WIS. Report-time cohorts and mature training labels prevent retrospective availability leakage.

Resource demand uses explicit, adjustable conversion assumptions. A SciPy HiGHS LP enforces purchasing budget, division transport and population equity constraints. Six budget levels and four equity floors are compared with a population-proportional baseline. A D3 v7 dashboard coordinates a district map, observed/forecast time series, seasonal heatmap, allocation comparison and early-warning table. A precomputed scenario grid supports what-if controls without sending encounters to the browser.

| Conflict | Concrete design and evidence |
|---|---|
| C1: sensitivity and alert burden | Two methods, threshold curves, calibration-only operating recommendation; count the seven-day maturity cost in delay. |
| C2: locality and privacy | District aggregation, suppression of counts 1–4 and coupled suppression to resist subtraction; aggregate browser payload under 10 MB. |
| C3: efficiency and equity | Compare equity floors 0, 0.2, 0.5 and 0.8 over six budgets; quantify weighted unmet demand and baseline benefit. |
| C4: freshness and cost | Daily batch and reproducible incremental recomputation; report stage runtime and hardware rather than assume a streaming service is needed. |
| C5: accuracy and explanation | Interpretable lagged regression, honest baseline comparisons and visible intervals; retain poor results rather than claim unmeasured gains. |

## Proposed responsibilities

| Member and student ID | Suggested lead area |
|---|---|
| Shadman Al Muksit — 0222220005101003 | Data engineering and integration |
| Md. Tarek Hossen — 0222220005101004 | Analytics and evaluation |
| Abir Ahamed Omi — 0222220005101021 | D3 dashboard and serving |
| Md. Sariful Islam — 0222220005101023 | Quality, privacy and performance |

These assignments are suggestions for team agreement. They do not assert completed human contributions. Each member must review another component, understand the full system, and record their actual work honestly.

## Risk register and acceptance evidence

| Risk | Mitigation and evidence |
|---|---|
| Public source outage or rate limit | Cache original responses; retain provenance and licences; fail explicitly rather than substitute undisclosed synthetic references. |
| Late reports resemble an outbreak | Preserve onset/report dates, reconstruct as-of features, and measure the injected backfill against a naive comparator. |
| Temporal leakage inflates accuracy | Restrict training labels and weather to origin availability; automated future-perturbation tests must pass. |
| Small counts identify people | Hash before silver; release aggregate, suppressed counts; test export privacy and payload size. |
| National scale exceeds laptop limits | Chunk generation, broadcast references, partitioned Parquet and AQE; measure SF 0.25, 0.5 and 1, including any failed target. |
| Allocation assumptions appear authoritative | State generic cost units, clinical conversion assumptions, continuous capacity and uncertainty; compare constraints and baseline quantitatively. |
| Unequal or unverifiable teamwork | Keep proposed roles distinct from actual contribution records; members provide their own commits, agreed shares and reflections. |

Acceptance requires a clean end-to-end run, an incremental rerun demonstrating idempotency and quarantine, evaluated analytics, five linked D3 views, privacy tests, and measured scaling evidence. Technical method references and limitations are in `analytics/METHODS.md`; the README maps requirement IDs to implementation and verification files.
