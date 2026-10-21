"""Cache attributable public references, then derive district-level tables.

Population is a zonal aggregation of actual WorldPop age/sex rasters, not an
invented age distribution or a claim to reproduce BBS district census totals.
Run: python -m generator.acquire --root data
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
import requests

GEO_URL = "https://www.geoboundaries.org/api/current/gbOpen/BGD/ADM2/"
WORLDPOP_PAGE = "https://hub.worldpop.org/geodata/summary?id=95740"
WORLDPOP_BASE = "https://data.worldpop.org/GIS/AgeSex_structures/Global_2015_2030/R2025A/2022/BGD/v1/1km_ua/constrained/"
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
AGES = [0, 1] + list(range(5, 95, 5))
DIVISIONS = {
    "Barishal": "Barguna Barisal Bhola Jhalokati Patuakhali Pirojpur".split(),
    "Chattogram": ["Bandarban", "Brahamanbaria", "Chandpur", "Chittagong", "Comilla", "Cox's Bazar", "Feni", "Khagrachhari", "Lakshmipur", "Noakhali", "Rangamati"],
    "Dhaka": "Dhaka Faridpur Gazipur Gopalganj Kishoreganj Madaripur Manikganj Munshiganj Narayanganj Narsingdi Rajbari Shariatpur Tangail".split(),
    "Khulna": "Bagerhat Chuadanga Jessore Jhenaidah Khulna Kushtia Magura Meherpur Narail Satkhira".split(),
    "Mymensingh": "Jamalpur Mymensingh Netrakona Sherpur".split(),
    "Rajshahi": "Bogra Joypurhat Naogaon Natore Nawabganj Pabna Rajshahi Sirajganj".split(),
    "Rangpur": "Dinajpur Gaibandha Kurigram Lalmonirhat Nilphamari Panchagarh Rangpur Thakurgaon".split(),
    "Sylhet": "Habiganj Maulvibazar Sunamganj Sylhet".split(),
}


def cached_get(url: str, path: Path, params: dict | None = None) -> Path:
    """Atomic on-disk response cache, with bounded retry for transient failures."""
    if path.exists() and path.stat().st_size:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(7):
        try:
            response = requests.get(url, params=params, timeout=(20, 180), headers={"User-Agent": "EpiScope-coursework/1.0 (noncommercial educational reference acquisition)"})
            if response.status_code == 429:
                time.sleep(min(90, 15 * (attempt + 1)))
                continue
            response.raise_for_status()
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(response.content)
            tmp.replace(path)
            return path
        except requests.RequestException:
            if attempt == 6:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Rate limit persisted: {url}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def acquire(root: Path, weather: bool = True) -> dict:
    from rasterio.features import rasterize
    import rasterio
    from shapely.geometry import shape

    ref, cache = root / "reference", root / "bronze" / "reference"
    ref.mkdir(parents=True, exist_ok=True)
    metadata_path = cached_get(GEO_URL, cache / "geoboundaries_metadata.json")
    metadata = json.loads(metadata_path.read_text())
    geometry_path = cached_get(metadata["simplifiedGeometryGeoJSON"], cache / "districts_original.geojson")
    collection = json.loads(geometry_path.read_text())
    features = sorted(collection["features"], key=lambda f: f["properties"]["shapeName"])
    if len(features) != 64:
        raise ValueError("Expected Bangladesh's 64 ADM2 districts")
    division_lookup = {district: division for division, districts in DIVISIONS.items() for district in districts}
    districts = []
    for i, feature in enumerate(features, 1):
        name = feature["properties"]["shapeName"]
        centre = shape(feature["geometry"]).centroid
        properties = dict(district_id=f"{i:02}", district_name=name, division=division_lookup[name], latitude=round(centre.y, 6), longitude=round(centre.x, 6))
        feature["properties"].update(properties)
        districts.append(properties)
    collection["features"] = features
    (ref / "districts.geojson").write_text(json.dumps(collection, separators=(",", ":")))
    cached_get(WORLDPOP_PAGE, cache / "worldpop_dataset.html")
    raster_paths = []
    for sex in ("f", "m"):
        for age in AGES:
            name = f"bgd_{sex}_{age:02}_2022_CN_1km_R2025A_UA_v1.tif"
            raster_paths.append((sex, age, cache / "worldpop" / name, WORLDPOP_BASE + name))
    def fetch_raster(item):
        cached_get(item[3], item[2])
        return item
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(fetch_raster, raster_paths))
    with rasterio.open(raster_paths[0][2]) as dataset:
        labels = rasterize([(feature["geometry"], i) for i, feature in enumerate(features, 1)], out_shape=dataset.shape, transform=dataset.transform, fill=0, dtype="uint8", all_touched=False)
        grid_shape, grid_transform = dataset.shape, tuple(dataset.transform)
    population = []
    source_total = assigned_total = 0.0
    for sex, age, path, _ in raster_paths:
        with rasterio.open(path) as dataset:
            if dataset.shape != grid_shape or tuple(dataset.transform) != grid_transform:
                raise ValueError("WorldPop rasters do not share the expected grid")
            values = dataset.read(1, masked=True).filled(0).astype(np.float64)
            values = np.maximum(values, 0)
            totals = np.bincount(labels.ravel(), weights=values.ravel(), minlength=65)
            source_total += float(values.sum())
            assigned_total += float(totals[1:].sum())
            band = "0" if age == 0 else "1-4" if age == 1 else "90+" if age == 90 else f"{age}-{age+4}"
            for district, value in zip(districts, totals[1:]):
                population.append(dict(district_id=district["district_id"], age_band=band, sex=sex.upper(), population=int(round(value)), population_estimate=float(value), reference_year=2022, source="WorldPop_R2025A_v1_1km_UN_adjusted"))
    pop = pd.DataFrame(population)
    pop.to_csv(ref / "population.csv", index=False)
    district_totals = pop.groupby("district_id").population.sum()
    registry = pd.DataFrame(districts)
    registry["population"] = registry.district_id.map(district_totals)
    registry.to_csv(ref / "districts.csv", index=False)
    for feature in features:
        feature["properties"]["population"] = int(district_totals.loc[feature["properties"]["district_id"]])
    (ref / "districts.geojson").write_text(json.dumps(collection, separators=(",", ":")))
    print(f"Geography: 64 districts; WorldPop zonal population: {int(registry.population.sum()):,}", flush=True)

    if weather:
        weather_rows = []
        weather_requests = []
        for district in districts:
            params = dict(latitude=district["latitude"], longitude=district["longitude"], start_date="2021-01-01", end_date="2025-12-31", daily="temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean", timezone="Asia/Dhaka", models="era5")
            cache_path = cache / "open_meteo" / f"district_{district['district_id']}_2021_2025.json"
            was_cached = cache_path.exists()
            path = cached_get(WEATHER_URL, cache_path, params)
            payload = json.loads(path.read_text())
            weather_requests.append(dict(district_id=district["district_id"], url=requests.Request("GET", WEATHER_URL, params=params).prepare().url, response_file=str(path.relative_to(root)).replace("\\", "/"), response_latitude=payload["latitude"], response_longitude=payload["longitude"]))
            daily = payload["daily"]
            if len(daily["time"]) != 1826:
                raise ValueError(f"Incomplete weather series: {district['district_name']}")
            for date, rain, temp, humidity in zip(daily["time"], daily["precipitation_sum"], daily["temperature_2m_mean"], daily["relative_humidity_2m_mean"]):
                if rain is None or temp is None or humidity is None:
                    raise ValueError(f"Missing public weather value: {district['district_name']} {date}")
                weather_rows.append(dict(date=date, district_id=district["district_id"], rainfall_mm=rain, temperature_c=temp, humidity_pct=humidity))
            print(f"Weather cached: {district['district_id']} {district['district_name']}", flush=True)
            if not was_cached:
                time.sleep(0.5)
        pd.DataFrame(weather_rows).to_parquet(ref / "weather.parquet", index=False)
        (cache / "open_meteo_requests.json").write_text(json.dumps(weather_requests, indent=2))

    provenance = {
        "processed_utc": datetime.now(timezone.utc).isoformat(),
        "geography": {"source": GEO_URL, "download": metadata["simplifiedGeometryGeoJSON"], "boundary_id": metadata["boundaryID"], "year": metadata["boundaryYearRepresented"], "licence": metadata["boundaryLicense"], "credit": "geoBoundaries; Bangladesh Bureau of Statistics; OCHA ROAP", "transformation": "Source district names retained; alphabetical local two-digit IDs, division mapping, planar geometry centroids and population fields added. Coordinates WGS84. IDs are project IDs, not official BBS geocodes."},
        "population": {"source": WORLDPOP_PAGE, "doi": "10.5258/SOTON/WP00842", "licence": "CC BY 4.0", "credit": "Bondarenko et al. (2025), WorldPop, University of Southampton", "year": 2022, "transformation": "Pixel-centre zonal sum of 40 UN-adjusted constrained 1km male/female age-band rasters over geoBoundaries district polygons. Each district/age/sex cell rounded to the nearest person. District total is the sum of these rounded cells.", "national_raster_population": source_total, "district_assigned_population_before_rounding": assigned_total, "unassigned_population": source_total - assigned_total, "limitations": "Modelled gridded estimates, not observed BBS district census counts; boundary/grid mismatch and pixel-centre assignment exclude some edge/coastal pixels. The 2022 denominator is held fixed for 2021–2025. Raster sex categories contain only male/female and do not represent all gender identities. Population reference was published in 2025 and is used only as a fixed synthetic teaching denominator, not as an information-available-in-2021 forecasting covariate."},
        "weather": {"source": WEATHER_URL, "docs": "https://open-meteo.com/en/docs/historical-weather-api", "licence": "CC BY 4.0", "credit": "Open-Meteo; Copernicus Climate Change Service ERA5 reanalysis", "model": "era5", "period": ["2021-01-01", "2025-12-31"], "timezone": "Asia/Dhaka", "variables": ["precipitation_sum", "temperature_2m_mean", "relative_humidity_2m_mean"], "limitations": "Real historical reanalysis at the nearest ERA5 grid cell to the district geometry centroid; not a station observation, district spatial average or forecast. Rainfall summed per local day, temperature and humidity daily means. API responses cached unmodified in bronze/reference/open_meteo."},
        "files": [{"path": str(p.relative_to(root)).replace("\\", "/"), "bytes": p.stat().st_size, "cached_at_utc": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(), "sha256": sha256(p)} for p in sorted(cache.rglob("*")) if p.is_file() and not p.name.endswith(".part")],
    }
    (ref / "provenance.json").write_text(json.dumps(provenance, indent=2))
    return {"districts": 64, "population_rows": len(pop), "weather_rows": 116864 if weather else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--no-weather", action="store_true", help="Acquire geography/population only; intended for offline unit testing")
    args = parser.parse_args()
    print(json.dumps(acquire(args.root, not args.no_weather)))


if __name__ == "__main__":
    main()
