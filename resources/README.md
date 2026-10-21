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
