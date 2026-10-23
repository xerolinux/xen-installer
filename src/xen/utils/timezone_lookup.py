"""Offline timezone lookups for the interactive map (TimezoneMap.qml).

timezone_at() resolves a clicked (lat, lon) to the IANA zone that actually
contains it, using bundled zone boundaries (data/timezone_boundaries.json.gz,
built by scripts/build_timezone_boundaries.py from timezone-boundary-builder).
Picking the zone with the nearest reference city instead (zone1970.tab, as
Calamares' offline fallback does) gets whole regions wrong - Lyon resolved to
Zurich, central Kentucky to America/Indiana/Vevay.

coordinate_for_timezone() goes the other way, giving a zone's reference
city from the system's zone1970.tab to place the pin when a zone is picked
from the list.
"""

from __future__ import annotations

import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

_DEFAULT_ZONE1970 = Path("/usr/share/zoneinfo/zone1970.tab")
_BOUNDARIES_FILE = Path(__file__).resolve().parent / "data" / "timezone_boundaries.json.gz"

# Clicks just outside every zone (e.g. a little off a coast, or in a gap the
# simplified borders left) snap to the nearest zone within this distance.
_SNAP_DEGREES = 1.0

# (min_x, min_y, max_x, max_y, rings) in the file's scaled integer units;
# rings[0] is the outer boundary, the rest are holes.
_Polygon = tuple[int, int, int, int, list[list[tuple[int, int]]]]

# ISO 6709 coordinate pair: ±DDMM[SS] (lat, 2-digit degrees) immediately
# followed by ±DDDMM[SS] (lon, 3-digit degrees), no separator between them.
_COORD_RE = re.compile(
    r"^(?P<lat_sign>[+-])(?P<lat_deg>\d{2})(?P<lat_min>\d{2})(?P<lat_sec>\d{2})?"
    r"(?P<lon_sign>[+-])(?P<lon_deg>\d{3})(?P<lon_min>\d{2})(?P<lon_sec>\d{2})?$"
)


def _decode_iso6709(coord: str) -> tuple[float, float]:
    match = _COORD_RE.match(coord)
    if not match:
        raise ValueError(f"Unrecognized ISO 6709 coordinate: {coord!r}")

    def _to_decimal(sign: str, deg: str, minutes: str, seconds: str | None) -> float:
        value = int(deg) + int(minutes) / 60 + (int(seconds) / 3600 if seconds else 0)
        return -value if sign == "-" else value

    lat = _to_decimal(
        match["lat_sign"], match["lat_deg"], match["lat_min"], match["lat_sec"]
    )
    lon = _to_decimal(
        match["lon_sign"], match["lon_deg"], match["lon_min"], match["lon_sec"]
    )
    return lat, lon


def parse_zone1970(path: Path = _DEFAULT_ZONE1970) -> list[tuple[str, float, float]]:
    """Parse zone1970.tab into a list of (zone_name, latitude, longitude).

    Returns an empty list (rather than raising) if the file is missing or
    unreadable, and skips lines with a malformed coordinate - a stripped-down
    or odd tzdata install should degrade the map to fewer results instead of
    crashing the bridge Slot that calls into this on every map click.
    """
    entries: list[tuple[str, float, float]] = []
    try:
        text = path.read_text()
    except OSError:
        return entries
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        columns = line.split("\t")
        if len(columns) < 3:
            continue
        coord, zone_name = columns[1], columns[2]
        try:
            lat, lon = _decode_iso6709(coord)
        except ValueError:
            continue
        entries.append((zone_name, lat, lon))
    return entries


@lru_cache(maxsize=1)
def _cached_entries() -> tuple[tuple[str, float, float], ...]:
    return tuple(parse_zone1970())


def coordinate_for_timezone(
    zone_name: str, entries: list[tuple[str, float, float]] | None = None
) -> tuple[float, float] | None:
    """Return the (lat, lon) zone1970.tab records for an exact zone name,
    or None if it isn't listed there (e.g. a legacy alias)."""
    candidates = entries if entries is not None else _cached_entries()
    for name, lat, lon in candidates:
        if name == zone_name:
            return (lat, lon)
    return None


def _decode_ring(flat: list[int]) -> list[tuple[int, int]]:
    x, y = flat[0], flat[1]
    points = [(x, y)]
    for i in range(2, len(flat), 2):
        x += flat[i]
        y += flat[i + 1]
        points.append((x, y))
    return points


@lru_cache(maxsize=1)
def _load_boundaries() -> tuple[int, dict[str, list[_Polygon]]]:
    """(scale, {zone: polygons}); empty if the bundled file is unreadable,
    which degrades map clicks to "no zone" instead of crashing the bridge."""
    try:
        with gzip.open(_BOUNDARIES_FILE, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return 1, {}
    zones: dict[str, list[_Polygon]] = {}
    for zone, polygons in payload["zones"].items():
        decoded = []
        for polygon in polygons:
            rings = [_decode_ring(ring) for ring in polygon]
            xs = [x for x, _ in rings[0]]
            ys = [y for _, y in rings[0]]
            decoded.append((min(xs), min(ys), max(xs), max(ys), rings))
        zones[zone] = decoded
    return payload["scale"], zones


def _boundaries() -> dict[str, list[_Polygon]]:
    return _load_boundaries()[1]


def _inside_ring(x: float, y: float, ring: list[tuple[int, int]]) -> bool:
    inside = False
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        x1, y1 = x2, y2
    return inside


def _distance2_to_ring(x: float, y: float, ring: list[tuple[int, int]]) -> float:
    best = float("inf")
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / seg2))
        px, py = x1 + t * dx - x, y1 + t * dy - y
        best = min(best, px * px + py * py)
        x1, y1 = x2, y2
    return best


def timezone_at(lat: float, lon: float) -> str:
    """IANA zone containing (lat, lon), or the nearest one within
    _SNAP_DEGREES; "" for open ocean (or missing boundary data)."""
    scale, zones = _load_boundaries()
    x, y = lon * scale, lat * scale
    for zone, polygons in zones.items():
        for min_x, min_y, max_x, max_y, rings in polygons:
            if min_x <= x <= max_x and min_y <= y <= max_y and _inside_ring(x, y, rings[0]):
                if not any(_inside_ring(x, y, hole) for hole in rings[1:]):
                    return zone

    snap = _SNAP_DEGREES * scale
    best_zone, best_d2 = "", snap * snap
    for zone, polygons in zones.items():
        for min_x, min_y, max_x, max_y, rings in polygons:
            if min_x - snap <= x <= max_x + snap and min_y - snap <= y <= max_y + snap:
                d2 = _distance2_to_ring(x, y, rings[0])
                if d2 < best_d2:
                    best_zone, best_d2 = zone, d2
    return best_zone
