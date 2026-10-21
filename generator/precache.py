"""Pre-cache weather while optional raster/GIS dependencies are being installed.

Uses polygon area centroids with the shoelace formula, including holes and
multipolygons. The main acquisition subsequently verifies the same references.
"""
import argparse
import json
from pathlib import Path
import time

from generator.acquire import GEO_URL, WEATHER_URL, cached_get


def ring_centroid(ring):
    area2 = cx6 = cy6 = 0.0
    for (x, y), (xx, yy) in zip(ring, ring[1:] + ring[:1]):
        cross = x * yy - xx * y
        area2 += cross
        cx6 += (x + xx) * cross
        cy6 += (y + yy) * cross
    if abs(area2) < 1e-15:
        return 0.0, 0.0, 0.0
    return abs(area2) / 2, cx6 / (3 * area2), cy6 / (3 * area2)


def geometry_centroid(geometry):
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    area = xsum = ysum = 0.0
    for polygon in polygons:
        for index, ring in enumerate(polygon):
            weight, x, y = ring_centroid(ring)
            weight *= 1 if index == 0 else -1
            area += weight
            xsum += weight * x
            ysum += weight * y
    return round(xsum / area, 6), round(ysum / area, 6)


def precache(root):
    cache = root / "bronze" / "reference"
    metadata = json.loads(cached_get(GEO_URL, cache / "geoboundaries_metadata.json").read_text())
    collection = json.loads(cached_get(metadata["simplifiedGeometryGeoJSON"], cache / "districts_original.geojson").read_text())
    features = sorted(collection["features"], key=lambda f: f["properties"]["shapeName"])
    for index, feature in enumerate(features, 1):
        lon, lat = geometry_centroid(feature["geometry"])
        params = dict(latitude=lat, longitude=lon, start_date="2021-01-01", end_date="2025-12-31", daily="temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean", timezone="Asia/Dhaka", models="era5")
        path = cached_get(WEATHER_URL, cache / "open_meteo" / f"district_{index:02}_2021_2025.json", params)
        data = json.loads(path.read_text())
        assert len(data["daily"]["time"]) == 1826
        print(f"Weather cached {index:02}/64 {feature['properties']['shapeName']}", flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data"))
    args = parser.parse_args()
    precache(args.root)
