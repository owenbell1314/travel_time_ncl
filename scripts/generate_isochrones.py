"""Generate precomputed isochrone GeoJSON files for every (start point x
transport mode) combination, using the TravelTime API for the finite time
bands and local geometry for the open-ended "over" band.

Usage:
    python scripts/generate_isochrones.py [--force] [--max-hits-per-minute N]

Idempotent and crash-safe: each (start point, mode) pair is fetched and
written to disk as soon as its bands come back, so a failure partway through
(network blip, API error, Ctrl+C) never loses already-completed pairs.
Re-running skips any pair that already has an output file, unless --force is
passed. The permanent free tier is rate-limited to 5 hits/minute, so a full
run (8 start points x 4 modes x 4 finite bands = 128 hits) takes roughly
25+ minutes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent))
from traveltime_client import Search, TravelTimeClient

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "docs" / "config.json"
BOUNDARY_PATH = REPO_ROOT / "docs" / "data" / "boundary.geojson"
OUTPUT_DIR = REPO_ROOT / "docs" / "data" / "isochrones"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def load_boundary_geometry():
    boundary_geojson = json.loads(BOUNDARY_PATH.read_text())
    geometries = [shape(feature["geometry"]) for feature in boundary_geojson["features"]]
    return unary_union(geometries)


def output_path(start_id: str, mode_id: str) -> Path:
    return OUTPUT_DIR / f"{start_id}__{mode_id}.geojson"


def search_id_for(start_point: dict, mode: dict, band: dict) -> str:
    return f"{start_point['id']}__{mode['id']}__{band['id']}"


def generate_pair(
    client: TravelTimeClient,
    start_point: dict,
    mode: dict,
    finite_bands_sorted: list[dict],
    largest_finite_band: dict,
    over_band: dict,
    boundary_geometry,
    departure_time: str,
) -> Path:
    """Fetches all finite-band isochrones for one (start point, mode) pair in
    a single batched API call, clips them to the boundary, derives the
    open-ended band locally, and writes the combined GeoJSON file."""
    searches = [
        Search(
            id=search_id_for(start_point, mode, band),
            lat=start_point["lat"],
            lng=start_point["lng"],
            departure_time=departure_time,
            travel_time_seconds=band["max_seconds"],
            transportation_type=mode["api_value"],
        )
        for band in finite_bands_sorted
    ]

    response = client.run_searches(searches)
    geometry_by_search_id = {
        feature["properties"]["search_id"]: shape(feature["geometry"])
        for feature in response.get("features", [])
    }

    features = []
    largest_finite_geom = None
    for band in finite_bands_sorted:
        search_id = search_id_for(start_point, mode, band)
        geometry = geometry_by_search_id.get(search_id)
        if geometry is None:
            raise RuntimeError(f"TravelTime API response is missing a result for {search_id}")
        clipped = geometry.intersection(boundary_geometry)
        if band["id"] == largest_finite_band["id"]:
            largest_finite_geom = clipped
        features.append(
            {
                "type": "Feature",
                "properties": {"band_id": band["id"], "order": band["order"]},
                "geometry": mapping(clipped),
            }
        )

    over_geometry = boundary_geometry.difference(largest_finite_geom)
    features.insert(
        0,
        {
            "type": "Feature",
            "properties": {"band_id": over_band["id"], "order": over_band["order"]},
            "geometry": mapping(over_geometry),
        },
    )

    feature_collection = {"type": "FeatureCollection", "features": features}
    out_path = output_path(start_point["id"], mode["id"])
    out_path.write_text(json.dumps(feature_collection))
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Regenerate pairs even if output already exists")
    parser.add_argument(
        "--max-hits-per-minute",
        type=int,
        default=5,
        help="TravelTime API hit rate limit to throttle to (default 5, the permanent free-tier limit)",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    config = load_config()
    boundary_geometry = load_boundary_geometry()

    finite_bands_sorted = sorted(
        (b for b in config["time_bands"] if b["max_seconds"] is not None),
        key=lambda b: b["order"],
    )
    over_band = next(b for b in config["time_bands"] if b["max_seconds"] is None)
    largest_finite_band = finite_bands_sorted[0]  # smallest "order" value = largest/farthest finite band
    departure_time = config["boundary"]["departure_time"]

    pending_pairs = [
        (start_point, mode)
        for start_point in config["start_points"]
        for mode in config["transport_modes"]
        if args.force or not output_path(start_point["id"], mode["id"]).exists()
    ]
    if not pending_pairs:
        print("Nothing to do: all (start point, mode) pairs already have output files. Use --force to regenerate.")
        return

    total_hits = len(pending_pairs) * len(finite_bands_sorted)
    print(
        f"Generating {len(pending_pairs)} start-point/mode pairs "
        f"({total_hits} API hits, ~{total_hits / args.max_hits_per_minute:.0f} min at "
        f"{args.max_hits_per_minute} hits/min)."
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = TravelTimeClient(max_hits_per_minute=args.max_hits_per_minute)
    start_time = time.monotonic()

    for i, (start_point, mode) in enumerate(pending_pairs, 1):
        try:
            out_path = generate_pair(
                client,
                start_point,
                mode,
                finite_bands_sorted,
                largest_finite_band,
                over_band,
                boundary_geometry,
                departure_time,
            )
        except Exception:
            print(
                f"\nFailed on {start_point['id']}/{mode['id']} ({i}/{len(pending_pairs)}). "
                f"{i - 1} pair(s) already saved to disk this run. "
                "Just rerun the script — completed pairs are skipped automatically.",
                file=sys.stderr,
            )
            raise

        elapsed = time.monotonic() - start_time
        rate = i / elapsed if elapsed > 0 else 0
        remaining = (len(pending_pairs) - i) / rate if rate > 0 else 0
        print(
            f"[{i}/{len(pending_pairs)}] wrote {out_path.name} "
            f"(elapsed {elapsed / 60:.1f} min, ~{remaining / 60:.1f} min remaining)"
        )

    print(f"Done: {len(pending_pairs)} pair(s) written to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
