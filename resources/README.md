# Public reference cache

`public-reference-cache.zip` preserves the public reference inputs used for validation. It contains district geography, WorldPop population rasters and derived district/age/sex denominators, cached daily weather responses and their provenance. It contains no facilities, encounters, stock records, patient hashes, deployment salts or credentials. The adjacent manifest records every member's byte size and SHA256.

Credits and licences:

- District boundaries: geoBoundaries, Bangladesh Bureau of Statistics and OCHA ROAP; CC BY 3.0 IGO. Boundary ID `BGD-ADM2-16705992`, source year 2020.
- Population: Bondarenko et al. (2025), WorldPop, University of Southampton; 2022 UN-adjusted constrained 1 km age/sex estimates, DOI `10.5258/SOTON/WP00842`; CC BY 4.0.
- Weather: Open-Meteo historical archive and Copernicus ERA5, 2021–2025; CC BY 4.0, with the provider attribution retained in the source provenance.

Exact URLs, transformations, geographic coverage limitations and source checksums are in `reference/provenance.json` within the archive and [generator/README.md](../generator/README.md). This cache reduces repeated upstream requests; it does not replace attribution or turn modelled population into observed census counts. The archive is below 50 MB. Generated patient and logistics data remain outside Git.

To use the same inputs before running the normal pipeline:

```bash
python -m zipfile -e resources/public-reference-cache.zip data
bash run_wsl.sh all --scale 1
```

The acquisition stage still checks and derives the reference products from cached original responses. Rebuild the cache after a deliberate source update with `python scripts/build_reference_cache.py --root data`.

## Retained validation evidence

`validation-evidence.zip` retains the measured three-scale aggregate artifacts, an independent SF1 query/UI study and the successful 41-test log after temporary GitHub artifacts expire. It contains protected dashboard releases, aggregate diagnostics, physical plans, actual Spark UI screenshots and source provenance. It excludes clinical feeds, lake tables, failed captures and credentials. The adjacent manifest records the archive checksum, every member's checksum, workflow runs and source revisions. Each member is at most 10 MB and the ZIP is below 50 MB.

Extract into a new directory, validate the recorded evidence, then prepare a reviewable import:

```bash
python -m zipfile -e resources/validation-evidence.zip work/restored-validation
python scripts/import_validation_artifacts.py --directory work/restored-validation/scaling --optimisations-directory work/restored-validation/optimisations --validate-only
python scripts/import_validation_artifacts.py --directory work/restored-validation/scaling --optimisations-directory work/restored-validation/optimisations --stage
```

Validation checks recorded inputs, checksums, privacy controls, study provenance and complete optimisation evidence. Staging writes under `work/`; it does not replace the active dashboard. The original successful test log and its tested Git revision remain under the archive's `tests/` directory. This archive preserves measured evidence; rebuilding the generated feed still uses the normal pipeline.

To rebuild the archive from verified downloads, use `python scripts/package_validation_evidence.py --directory MAIN_CORE_ARTIFACTS --optimisations-directory INDEPENDENT_SF1_ARTIFACTS --tests-directory SUCCESSFUL_TEST_ARTIFACT`. Packaging first runs the same importer in validation-only mode and rejects partial studies or test failures/skips.
