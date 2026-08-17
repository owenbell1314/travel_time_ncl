"""Fetch the Tyne and Wear boundary from OpenStreetMap (via Nominatim), buffer
it by the configured distance, and clip the result to the coastline (via OSM
coastline data from Overpass) so the buffer doesn't extend out over the sea.
Produces docs/data/boundary.geojson.

Re-run only when config.json's boundary settings change. Not part of the
per-run isochrone pipeline since the boundary is stable. If the coastline
clip step fails for any reason (e.g. Overpass is unreachable), falls back to
the plain buffered boundary rather than failing outright.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import Point, shape
from shapely.ops import linemerge, unary_union

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "docs" / "config.json"
RAW_BOUNDARY_PATH = REPO_ROOT / "data" / "boundary_raw.geojson"
OUTPUT_PATH = REPO_ROOT / "docs" / "data" / "boundary.geojson"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/lookup"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
USER_AGENT = "travel-time-ncl (personal project; contact via GitHub)"
BNG_CRS = "EPSG:27700"  # British National Grid, appropriate for this region
WGS84_CRS = "EPSG:4326"
METRES_PER_MILE = 1609.34
COASTLINE_WALL_DEGREES = 0.001  # ~100m, enough to bridge small gaps between OSM coastline ways
COASTAL_QUERY_WIDTH_DEGREES = 0.35  # eastern strip of the bbox that can actually contain coastline


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


def fetch_coastline_lines(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> list:
    query = (
        f"[out:json][timeout:30];"
        f'way["natural"="coastline"]({min_lat},{min_lon},{max_lat},{max_lon});'
        f"out geom;"
    )

    last_error = None
    for attempt in range(3):
        for url in OVERPASS_URLS:
            try:
                response = requests.post(
                    url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=60
                )
                response.raise_for_status()
            except requests.exceptions.RequestException as exc:
                last_error = exc
                print(f"  Overpass request to {url} failed ({exc}); trying next option...")
                continue

            elements = response.json().get("elements", [])
            lines = [
                [(pt["lon"], pt["lat"]) for pt in element["geometry"]]
                for element in elements
                if element.get("type") == "way" and len(element.get("geometry") or []) >= 2
            ]
            if lines:
                return lines
            last_error = RuntimeError("Overpass returned no coastline ways")

    raise RuntimeError(f"Could not fetch coastline data: {last_error}")


def clip_to_land(buffered_polygon, land_seed_points):
    """Clips buffered_polygon to exclude any area past the coastline.

    Splits the buffer along the OSM coastline (buffered into a thin "wall" so
    it cleanly divides the polygon), then keeps only the pieces containing a
    known-land seed point. The seed points are the configured start points --
    real places that are unambiguously on land.

    Note the unbuffered Tyne and Wear administrative boundary is NOT a usable
    land reference here: it extends offshore over tidal water, so the seaward
    piece touches it too and the sea would be kept."""
    from shapely.geometry import LineString

    min_lon, min_lat, max_lon, max_lat = buffered_polygon.bounds
    # Query only the eastern (seaward) strip: the full box is mostly inland,
    # and asking Overpass for all of it reliably times out.
    coastal_min_lon = max(min_lon, max_lon - COASTAL_QUERY_WIDTH_DEGREES)
    raw_lines = fetch_coastline_lines(coastal_min_lon, min_lat, max_lon, max_lat)
    if not raw_lines:
        raise RuntimeError("Overpass returned no coastline ways for this bounding box")

    coastline = unary_union([LineString(coords) for coords in raw_lines if len(coords) >= 2])
    coastline = linemerge(coastline) if coastline.geom_type == "MultiLineString" else coastline

    wall = coastline.buffer(COASTLINE_WALL_DEGREES)
    pieces = buffered_polygon.difference(wall)
    piece_list = list(pieces.geoms) if pieces.geom_type == "MultiPolygon" else [pieces]

    land_pieces = [p for p in piece_list if any(p.contains(pt) for pt in land_seed_points)]
    if not land_pieces:
        raise RuntimeError("Coastline clip produced no piece containing a known-land seed point")

    unseeded = [p for p in piece_list if p not in land_pieces]
    dropped_area = sum(p.area for p in unseeded)
    kept_area = sum(p.area for p in land_pieces)
    print(
        f"  coastline split the buffer into {len(piece_list)} piece(s); "
        f"kept {len(land_pieces)} containing land seed points "
        f"({dropped_area / (kept_area + dropped_area):.1%} of the buffer dropped as sea)"
    )
    return unary_union(land_pieces)


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

    unbuffered_geom = shape(raw_geometry)
    gdf = gpd.GeoDataFrame(geometry=[unbuffered_geom], crs=WGS84_CRS)
    raw_area_km2 = gdf.to_crs(BNG_CRS).geometry.area.iloc[0] / 1_000_000
    print(f"Raw boundary area: {raw_area_km2:.1f} km^2")

    buffered_gdf = gdf.to_crs(BNG_CRS)
    buffered_gdf["geometry"] = buffered_gdf.buffer(buffer_miles * METRES_PER_MILE)
    buffered_area_km2 = buffered_gdf.geometry.area.iloc[0] / 1_000_000
    print(f"Buffered boundary area ({buffer_miles} mile buffer): {buffered_area_km2:.1f} km^2")

    buffered_wgs84_geom = buffered_gdf.to_crs(WGS84_CRS).geometry.iloc[0]

    land_seed_points = [Point(sp["lng"], sp["lat"]) for sp in config["start_points"]]

    print("Fetching OSM coastline data to clip the buffer to land...")
    try:
        final_geom = clip_to_land(buffered_wgs84_geom, land_seed_points)
        final_gdf = gpd.GeoDataFrame(geometry=[final_geom], crs=WGS84_CRS)
        clipped_area_km2 = final_gdf.to_crs(BNG_CRS).geometry.area.iloc[0] / 1_000_000
        print(f"Coastline-clipped area: {clipped_area_km2:.1f} km^2 (removed {buffered_area_km2 - clipped_area_km2:.1f} km^2 of sea)")
    except Exception as exc:
        print(f"WARNING: coastline clipping failed ({exc}); falling back to the unclipped buffer.")
        final_gdf = gpd.GeoDataFrame(geometry=[buffered_wgs84_geom], crs=WGS84_CRS)

    bounds = final_gdf.total_bounds
    print(f"Final bounding box: lon [{bounds[0]:.4f}, {bounds[2]:.4f}], lat [{bounds[1]:.4f}, {bounds[3]:.4f}]")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    final_gdf.to_file(OUTPUT_PATH, driver="GeoJSON")
    print(f"Wrote boundary to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
