#!/usr/bin/env python3
"""Build src/xen/utils/data/timezone_boundaries.json.gz.

Input: timezones.geojson.zip from
https://github.com/evansiroky/timezone-boundary-builder/releases
(ODbL, derived from OpenStreetMap). The output keeps every IANA zone's
polygons, simplified (Douglas-Peucker) and quantized to 0.01 degrees
(~1 km), which is plenty for a click-to-pick map and small enough to ship.

Output format:
    {"source": ..., "scale": 100,
     "zones": {zone: [polygon, ...]}}
polygon = [outer_ring, hole_ring, ...]; each ring is a flat list of
integers [x0, y0, dx1, dy1, ...] (lon/lat * scale, delta-encoded).

Usage: scripts/build_timezone_boundaries.py timezones.geojson.zip RELEASE_TAG
"""

from __future__ import annotations

import gzip
import json
import sys
import zipfile
from pathlib import Path

SCALE = 100
TOLERANCE = 1 / SCALE
OUTPUT = Path(__file__).resolve().parents[1] / "src/xen/utils/data/timezone_boundaries.json.gz"


def _simplify(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """Iterative Douglas-Peucker; keeps both endpoints of a closed ring."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tolerance * tolerance
    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best, best_d2 = -1, tol2
        for i in range(start + 1, end):
            px, py = points[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > best_d2:
                best, best_d2 = i, d2
        if best >= 0:
            keep[best] = True
            stack.append((start, best))
            stack.append((best, end))
    return [p for p, k in zip(points, keep) if k]


def _encode_ring(ring: list[list[float]]) -> list[int] | None:
    simplified = _simplify(ring, TOLERANCE)
    quantized: list[tuple[int, int]] = []
    for lon, lat in simplified:
        q = (round(lon * SCALE), round(lat * SCALE))
        if not quantized or quantized[-1] != q:
            quantized.append(q)
    if len(quantized) < 4:
        # Too small to survive simplification (tiny islands): keep a
        # minimal box around it so it stays clickable.
        xs = [round(lon * SCALE) for lon, _ in ring]
        ys = [round(lat * SCALE) for _, lat in ring]
        x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
        quantized = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    flat = [quantized[0][0], quantized[0][1]]
    for (px, py), (x, y) in zip(quantized, quantized[1:]):
        flat += [x - px, y - py]
    return flat


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    archive, tag = Path(sys.argv[1]), sys.argv[2]
    with zipfile.ZipFile(archive) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".json"))
        data = json.loads(zf.read(name))

    zones: dict[str, list[list[list[int]]]] = {}
    for feature in data["features"]:
        zone = feature["properties"]["tzid"]
        geometry = feature["geometry"]
        polygons = geometry["coordinates"]
        if geometry["type"] == "Polygon":
            polygons = [polygons]
        for polygon in polygons:
            zones.setdefault(zone, []).append([_encode_ring(ring) for ring in polygon])

    payload = {
        "source": f"timezone-boundary-builder {tag} (ODbL, OpenStreetMap contributors)",
        "scale": SCALE,
        "zones": zones,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUTPUT, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(payload, fh, separators=(",", ":"), sort_keys=True)
    print(f"{len(zones)} zones -> {OUTPUT} ({OUTPUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
