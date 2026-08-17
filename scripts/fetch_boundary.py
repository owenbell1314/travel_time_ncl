"""Fetch the Tyne and Wear boundary from OpenStreetMap (via Nominatim) and
buffer it by the configured distance, producing docs/data/boundary.geojson.

Re-run only when config.json's boundary settings change. Not part of the
per-run isochrone pipeline since the boundary is stable.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import shape

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "docs" / "config.json"
RAW_BOUNDARY_PATH = REPO_ROOT / "data" / "boundary_raw.geojson"
OUTPUT_PATH = REPO_ROOT / "docs" / "data" / "boundary.geojson"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/lookup"
USER_AGENT = "travel-time-ncl (personal project; contact via GitHub)"
BNG_CRS = "EPSG:27700"  # British National Grid, appropriate for this region
WGS84_CRS = "EPSG:4326"
METRES_PER_MILE = 1609.34


def fetch_raw_boundary(osm_relation_id: int) -> dict:
    response = requests.get(
        NOMINATIM_URL,
        params={
            "osm_ids": f"R{osm_relation_id}",
            "format": "json",
            "polygon_geojson": 1,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise RuntimeError(f"Nominatim returned no results for relation R{osm_relation_id}")
    return results[0]["geojson"]


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text())
    boundary_cfg = config["boundary"]
    osm_relation_id = boundary_cfg["osm_relation_id"]
    buffer_miles = boundary_cfg["buffer_miles"]

    print(f"Fetching Tyne and Wear boundary (OSM relation R{osm_relation_id}) from Nominatim...")
    raw_geometry = fetch_raw_boundary(osm_relation_id)

    RAW_BOUNDARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_BOUNDARY_PATH.write_text(json.dumps(raw_geometry, indent=2))
    print(f"Saved raw boundary to {RAW_BOUNDARY_PATH}")

    gdf = gpd.GeoDataFrame(geometry=[shape(raw_geometry)], crs=WGS84_CRS)
    raw_area_km2 = gdf.to_crs(BNG_CRS).geometry.area.iloc[0] / 1_000_000
    print(f"Raw boundary area: {raw_area_km2:.1f} km^2")

    buffered_gdf = gdf.to_crs(BNG_CRS)
    buffered_gdf["geometry"] = buffered_gdf.buffer(buffer_miles * METRES_PER_MILE)
    buffered_area_km2 = buffered_gdf.geometry.area.iloc[0] / 1_000_000
    print(f"Buffered boundary area ({buffer_miles} mile buffer): {buffered_area_km2:.1f} km^2")

    buffered_wgs84 = buffered_gdf.to_crs(WGS84_CRS)
    bounds = buffered_wgs84.total_bounds
    print(f"Buffered bounding box: lon [{bounds[0]:.4f}, {bounds[2]:.4f}], lat [{bounds[1]:.4f}, {bounds[3]:.4f}]")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    buffered_wgs84.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"Wrote buffered boundary to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
