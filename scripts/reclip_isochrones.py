"""Re-clip already-generated isochrone files against the current
docs/data/boundary.geojson, without calling the TravelTime API.

Useful after changing how the boundary is computed (e.g. clipping it to the
coastline) -- since the new boundary is always a subset of whatever boundary
was used originally, re-intersecting the already-clipped saved geometries
against it gives the same result as regenerating from scratch, for free.

Usage:
    python scripts/reclip_isochrones.py
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "docs" / "config.json"
BOUNDARY_PATH = REPO_ROOT / "docs" / "data" / "boundary.geojson"
ISOCHRONE_DIR = REPO_ROOT / "docs" / "data" / "isochrones"


def load_boundary_geometry():
    boundary_geojson = json.loads(BOUNDARY_PATH.read_text())
    geometries = [shape(feature["geometry"]) for feature in boundary_geojson["features"]]
    return unary_union(geometries)


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text())
    boundary_geometry = load_boundary_geometry()

    over_band_id = next(b["id"] for b in config["time_bands"] if b["max_seconds"] is None)
    largest_finite_band_id = min(
        (b for b in config["time_bands"] if b["max_seconds"] is not None),
        key=lambda b: b["order"],
    )["id"]

    files = sorted(ISOCHRONE_DIR.glob("*.geojson"))
    if not files:
        print(f"No isochrone files found in {ISOCHRONE_DIR}")
        return

    for path in files:
        data = json.loads(path.read_text())
        features = data["features"]

        largest_finite_geom = None
        for feature in features:
            band_id = feature["properties"]["band_id"]
            if band_id == over_band_id:
                continue  # recomputed below, after the finite bands are re-clipped
            geom = shape(feature["geometry"]).intersection(boundary_geometry)
            feature["geometry"] = mapping(geom)
            if band_id == largest_finite_band_id:
                largest_finite_geom = geom

        if largest_finite_geom is None:
            print(f"SKIPPING {path.name}: no '{largest_finite_band_id}' band feature found")
            continue

        over_geometry = boundary_geometry.difference(largest_finite_geom)
        for feature in features:
            if feature["properties"]["band_id"] == over_band_id:
                feature["geometry"] = mapping(over_geometry)

        path.write_text(json.dumps(data))
        print(f"Re-clipped {path.name}")

    print(f"\nDone: re-clipped {len(files)} file(s) against {BOUNDARY_PATH}")


if __name__ == "__main__":
    main()
