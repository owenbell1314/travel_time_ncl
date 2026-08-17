# Travel Time Map — Newcastle upon Tyne / Tyne and Wear

An interactive travel-time (isochrone) map. Pick a start point and a transport mode,
and the map colors the area around it by how long it takes to get there, covering
Tyne and Wear plus a 5 mile buffer.

Isochrones are precomputed offline with the [TravelTime API](https://traveltime.com)
and shipped as static GeoJSON, so the live site (`docs/`) is a plain static
HTML/JS/Leaflet page with no backend and no exposed API key — deployable directly
on GitHub Pages.

## One-time setup

1. Sign up for a free TravelTime API key at https://account.traveltime.com/ (you'll
   get an App ID and an API Key).
2. Copy `.env.example` to `.env` and fill in your credentials:
   ```
   cp .env.example .env
   ```
3. Install Python dependencies (Python 3.10+ recommended):
   ```
   pip install -r requirements.txt
   ```

## Generating the map data

1. Fetch the Tyne and Wear boundary, buffer it by 5 miles, and clip it to the
   coastline so the map never colours the sea (only needs re-running if
   `docs/config.json`'s `boundary` settings change):
   ```
   python scripts/fetch_boundary.py
   ```
   The coastline comes from OpenStreetMap via Overpass. If Overpass is
   unreachable the script warns and falls back to the unclipped buffer rather
   than failing — worth re-running if you see that warning, since the fallback
   extends out over the North Sea.
2. Generate all isochrones:
   ```
   python scripts/generate_isochrones.py
   ```
   This calls the TravelTime API once per (start point x transport mode x time
   band) combination — 8 start points x 4 modes x 4 finite bands = 128 API
   "hits". **The permanent free tier is rate-limited to 5 hits/minute**, so a
   full run takes roughly 25+ minutes — this is a hard account limit, not a
   script setting, so don't mistake the wait for a hang. The script logs
   progress as it goes (`[12/128] ...`) and prints an ETA.

   The script is safe to re-run: it writes each (start point, mode) pair to
   disk as soon as its bands come back and skips pairs that already have an
   output file, so an interrupted run resumes without re-spending API hits.
   Pass `--force` to regenerate everything from scratch, e.g. after changing
   time bands, colors, or start points in `docs/config.json`.

3. **Only if you changed the boundary**, re-clip the existing isochrones to it
   — no API calls, so this is free and takes seconds:
   ```
   python scripts/reclip_isochrones.py
   ```
   Use this instead of `generate_isochrones.py --force` whenever the start
   points, modes, and time bands are unchanged and only the boundary moved.

## Previewing locally

Browsers block `fetch()` of local files under `file://`, so serve the `docs/`
folder over HTTP:
```
python -m http.server --directory docs 8000
```
Then open http://localhost:8000

## Deploying to GitHub Pages

Repo Settings → Pages → Source: `main` branch, `/docs` folder. Once enabled,
the site is live at `https://<your-username>.github.io/<repo-name>/`.

## Configuration

Everything the map shows is controlled by `docs/config.json`:

- **`time_bands`** — the color-coded time thresholds (label, `max_seconds`,
  `color`, and `order` for draw/legend ordering). The last band should have
  `max_seconds: null` — it's computed locally as "everything within the
  boundary not reached by the largest finite band," not an API call.
- **`transport_modes`** — which modes appear in the dropdown and their
  TravelTime API `transportation` type.
- **`start_points`** — the fixed list of selectable start locations
  (name + lat/lng).
- **`boundary`** — the OSM relation used for the Tyne and Wear boundary, the
  buffer distance in miles, and the `departure_time` journeys are timed from
  (a weekday morning by default, which matters for public transport since it
  follows real timetables).

After editing `time_bands`, `transport_modes`, or `start_points`, re-run
`python scripts/generate_isochrones.py --force` to regenerate the data —
the static GeoJSON files won't update themselves. After editing only
`boundary`, run `fetch_boundary.py` then `reclip_isochrones.py` instead, which
needs no API calls.

The start points double as the land reference when clipping the buffer to the
coastline, so they must all be real on-land locations.

## Project layout

```
scripts/
  traveltime_client.py    # TravelTime API client (auth, batching, throttling, retries)
  fetch_boundary.py       # OSM boundary -> 5mi buffer -> coastline clip -> docs/data/boundary.geojson
  generate_isochrones.py  # main: calls the API, clips to boundary, writes docs/data/isochrones/*.geojson
  reclip_isochrones.py    # re-clip existing isochrones to a changed boundary, no API calls
docs/                     # GitHub Pages root — static site, no backend
  config.json              # single source of truth for bands/modes/start points/boundary
  index.html, css/, js/    # the map UI
  data/
    boundary.geojson       # Tyne and Wear + 5mi, clipped to the coastline
    isochrones/            # one GeoJSON file per (start point, mode) pair
```
