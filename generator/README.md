# Reference data and synthetic feeds

All health encounters, facility names, patient identifiers, clinical outcomes and stock snapshots are synthetic. They describe a teaching scenario and cannot support a clinical or procurement decision. Population, geography and weather are public reference data.

Acquire the references once, then generate a feed:

```bash
python -m generator.acquire --root data
python -m generator.generate --root data --scale .01 --seed 4345
```

The default demonstration produces at least 250,000 encounters. Scale factors 0.25, 0.5 and 1 require at least 6.25, 12.5 and 25 million encounters respectively, before duplicate resubmissions. Each benchmark should use a separate data root with the same reference files. A successful generation can be reused without downloading or rewriting it. `--force` replaces only files matching the generator's own encounter and stock naming patterns; it does not clear the lake or other incoming feeds.

## Public sources and attribution

| Reference | Source and licence | Transformation and limits |
|---|---|---|
| District boundaries | [geoBoundaries Bangladesh ADM2 API](https://www.geoboundaries.org/api/current/gbOpen/BGD/ADM2/); source Bangladesh Bureau of Statistics and OCHA ROAP; CC BY 3.0 IGO as declared in this layer's metadata | The simplified 2020 boundary layer contains 64 districts. Source names are retained, including earlier English spellings. Local IDs `01`–`64` follow alphabetical source names; these are not official BBS geocodes. Division labels and geometry centroids are added. |
| District population by age and sex | [WorldPop 2022 age/sex dataset](https://hub.worldpop.org/geodata/summary?id=95740), Bondarenko et al. (2025), University of Southampton, [DOI 10.5258/SOTON/WP00842](https://doi.org/10.5258/SOTON/WP00842); CC BY 4.0 | Forty actual UN-adjusted, constrained 1 km rasters are aggregated by district using pixel centres. These are modelled population estimates, not district census counts. The derived table retains 20 age bands per sex. Minor coastal/border losses are recorded in provenance. A fixed 2022 denominator is used across the simulation. The source distinguishes male/female only. |
| Daily weather | [Open-Meteo historical archive](https://open-meteo.com/en/docs/historical-weather-api), Copernicus Climate Change Service ERA5; [CC BY 4.0](https://open-meteo.com/en/licence) | Actual 2021–2025 reanalysis is queried at each district geometry centroid in `Asia/Dhaka`: daily precipitation sum, mean temperature and mean relative humidity. Values represent the nearest ERA5 grid cell, not district-wide averages or station observations. |

Source responses are preserved in `data/bronze/reference/`. Requests use bounded retries, cache completed responses and respect rate-limit responses. `data/reference/provenance.json` records URLs, licences, transformations, retrieval time and SHA256 hashes. Interrupted acquisitions resume from the cache. Reference availability and publisher services remain external dependencies; no weather or demographic values are silently fabricated when acquisition fails.

## Synthetic mechanisms

The generator uses independent NumPy seed streams for outbreak placement, facilities, daily counts, district line lists and logistics. For a fixed seed, scale and reference cache, its CSV/JSONL file contents are deterministic. Manifest timestamps and measured duration naturally vary.

- **Burden and seasonality:** population-weighted disease intensity, strong urban dengue concentration and hill-district malaria risk. Dengue peaks after the monsoon; diarrhoea has pre-monsoon and post-flood components; influenza and malaria have separate annual cycles.
- **Climate:** dengue intensity responds to real mean rainfall and temperature from 35–62 days earlier. Diarrhoea responds to heavy rainfall 7–13 days earlier. These are constructed teaching mechanisms, not fitted causal estimates.
- **Noise:** negative-binomial daily counts use dispersion 15, giving variance `mu + mu² / 15`. The expected count is slightly above the required minimum; a rare shortfall is topped up with background encounters and explicitly counted in the manifest.
- **Outbreaks:** 34 district events vary in disease, date, duration and multiplier; an 84-day slow-growing dengue event and a 56-day national novel-respiratory event add two more event identities. The national event appears as 64 district-event rows with one shared event ID. Its syndrome is coded as influenza-like illness and does not claim laboratory identification of a new pathogen.
- **Reporting:** right-skewed delays are bounded at 21 days. Most Friday/Saturday and selected fixed-holiday reports move forward. Five Dhaka facilities go silent for three weeks and backfill on 22 September 2024. Lunar holidays are not modelled.
- **Clinical consistency:** admission/laboratory probabilities vary by disease; outbreak admissions rise, deaths occur only among admitted encounters, and elderly synthetic patients have higher fatality risk.
- **Deliberate defects:** about 2% exact encounter resubmissions, 0.5% orphan facility IDs, 1% invalid age/sex, and 0.1% each missing onset dates, reports before onset and invalid ICD codes. These are ingestion test cases.

Patients are sampled from each district's actual WorldPop age/sex proportions. Within an age band, age is uniform; `90+` is represented by synthetic ages 90–100. Repeated synthetic patient IDs permit cross-facility visits, while valid encounter IDs identify visits.

The facility registry contains 640 generated facilities, with at least four in every district and additional facilities allocated by population. Rejection sampling places facilities inside the district polygons. Logistics includes one daily snapshot per facility for the full five years; monthly JSONL files remain small. Stock and occupancy follow declared simulation assumptions, not real hospital inventories.

## File and truth contract

Encounters use the assignment's canonical CSV fields plus `revision=1`. Chunks expand at most 100,000 unique encounters by default and enforce a file-size limit below 50 MB. Stock JSONL fields are `facility_id,date,iv_saline,ns1_kits,ors,antimalarials,occupied_beds`. The reference registry includes `facility_id,district_id,facility_name,facility_type,latitude,longitude,beds,icu_beds`.

`generation_manifest.json` records exact row counts, output hashes, parameters, reference hashes and generation time. Separate truth files are:

- `truth/ground_truth_outbreaks.csv`: `event_id,district_id,disease,start_date,end_date,multiplier,shape,is_national`.
- `truth/reporting_artefacts.csv`: `artefact_id,facility_id,district_id,start_date,end_date,backfill_date,type`.

Truth files are restricted to evaluation. Detection and forecasting must derive their signals from the observed lake tables. The generator intentionally includes background `other` encounters; surveillance marts focus on dengue, diarrhoea, influenza and malaria.
