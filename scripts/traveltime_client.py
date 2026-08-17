"""Thin client for the TravelTime API's Time Map (isochrone) endpoint.

Docs: https://docs.traveltime.com/api/reference/isochrones
Auth: X-Application-Id / X-Api-Key headers.
Rate limiting is per-search ("hit"), not per HTTP request: the permanent free
tier allows only 5 hits/minute (60/min during the 2-week trial), so callers
must throttle even though up to 10 searches can be batched into one request.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass

import requests

TIME_MAP_URL = "https://api.traveltimeapp.com/v4/time-map"
MAX_SEARCHES_PER_REQUEST = 10
DEFAULT_MAX_HITS_PER_MINUTE = 5


@dataclass
class Search:
    id: str
    lat: float
    lng: float
    departure_time: str
    travel_time_seconds: int
    transportation_type: str


class TravelTimeClient:
    def __init__(
        self,
        app_id: str | None = None,
        api_key: str | None = None,
        max_hits_per_minute: int = DEFAULT_MAX_HITS_PER_MINUTE,
    ):
        self.app_id = app_id or os.environ.get("TRAVELTIME_APP_ID")
        self.api_key = api_key or os.environ.get("TRAVELTIME_API_KEY")
        if not self.app_id or not self.api_key:
            raise RuntimeError(
                "Missing TRAVELTIME_APP_ID / TRAVELTIME_API_KEY. "
                "Copy .env.example to .env and fill in your credentials."
            )
        self.max_hits_per_minute = max_hits_per_minute
        self._min_seconds_between_hits = 60.0 / max_hits_per_minute
        self._session = requests.Session()

    def _headers(self) -> dict:
        return {
            "X-Application-Id": self.app_id,
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/geo+json",
        }

    def run_searches(self, searches: list[Search]) -> dict:
        """Run up to MAX_SEARCHES_PER_REQUEST searches in one request, return
        the response GeoJSON FeatureCollection. Throttles by search count so
        the account's per-minute hit limit is respected regardless of how
        many searches are batched into this single HTTP call."""
        if not searches:
            return {"type": "FeatureCollection", "features": []}
        if len(searches) > MAX_SEARCHES_PER_REQUEST:
            raise ValueError(
                f"Cannot batch more than {MAX_SEARCHES_PER_REQUEST} searches per request"
            )

        payload = {
            "departure_searches": [
                {
                    "id": s.id,
                    "coords": {"lat": s.lat, "lng": s.lng},
                    "departure_time": s.departure_time,
                    "travel_time": s.travel_time_seconds,
                    "transportation": {"type": s.transportation_type},
                }
                for s in searches
            ]
        }

        response = self._request_with_retry(payload)
        self._throttle(len(searches))
        return response.json()

    def _request_with_retry(self, payload: dict, max_retries: int = 5) -> requests.Response:
        backoff_seconds = 5
        for attempt in range(max_retries + 1):
            try:
                response = self._session.post(
                    TIME_MAP_URL, json=payload, headers=self._headers(), timeout=30
                )
            except requests.exceptions.RequestException as exc:
                if attempt < max_retries:
                    print(
                        f"  network error ({exc.__class__.__name__}: {exc}), "
                        f"retrying in {backoff_seconds}s ({attempt + 1}/{max_retries})...",
                        file=sys.stderr,
                    )
                    time.sleep(backoff_seconds)
                    backoff_seconds = min(backoff_seconds * 2, 60)
                    continue
                raise RuntimeError(
                    f"TravelTime API: network error after {max_retries} retries: {exc}"
                ) from exc

            if response.status_code == 429 and attempt < max_retries:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else backoff_seconds
                time.sleep(wait)
                backoff_seconds = min(backoff_seconds * 2, 60)
                continue
            if not response.ok:
                raise RuntimeError(
                    f"TravelTime API error {response.status_code}: {response.text[:500]}"
                )
            return response
        raise RuntimeError("TravelTime API: exceeded retries after repeated 429 responses")

    def _throttle(self, hit_count: int) -> None:
        time.sleep(self._min_seconds_between_hits * hit_count)
