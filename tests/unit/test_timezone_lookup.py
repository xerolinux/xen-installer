"""Unit tests for xen.utils.timezone_lookup: offline timezone lookup for
the interactive map (bundled zone boundaries) and zone coordinates from the
system's own /usr/share/zoneinfo/zone1970.tab."""

from __future__ import annotations

import pytest

from xen.utils.timezone_lookup import coordinate_for_timezone, parse_zone1970, timezone_at


class TestParseZone1970:
    def test_parses_iso6709_coordinates(self, tmp_path) -> None:
        sample = tmp_path / "zone1970.tab"
        sample.write_text(
            "AE,OM\t+2518+05518\tAsia/Dubai\tCrozet\n"
            "US\t+404251-0740023\tAmerica/New_York\n"
        )
        entries = parse_zone1970(sample)
        assert entries[0][0] == "Asia/Dubai"
        assert entries[0][1] == pytest.approx(25.3, abs=0.01)
        assert entries[0][2] == pytest.approx(55.3, abs=0.01)

        assert entries[1][0] == "America/New_York"
        assert entries[1][1] == pytest.approx(40.714, abs=0.01)
        assert entries[1][2] == pytest.approx(-74.006, abs=0.01)

    def test_missing_file_returns_empty_list(self, tmp_path) -> None:
        missing = tmp_path / "does-not-exist.tab"
        assert parse_zone1970(missing) == []

    def test_skips_malformed_coordinate_lines(self, tmp_path) -> None:
        sample = tmp_path / "zone1970.tab"
        sample.write_text(
            "XX\tnot-a-coordinate\tBroken/Zone\n"
            "AD\t+4230+00131\tEurope/Andorra\n"
        )
        assert [e[0] for e in parse_zone1970(sample)] == ["Europe/Andorra"]

    def test_skips_comments_and_blank_lines(self, tmp_path) -> None:
        sample = tmp_path / "zone1970.tab"
        sample.write_text(
            "# tzdb timezone descriptions\n"
            "#\n"
            "\n"
            "AD\t+4230+00131\tEurope/Andorra\n"
        )
        entries = parse_zone1970(sample)
        assert len(entries) == 1
        assert entries[0][0] == "Europe/Andorra"


class TestTimezoneAt:
    """Point-in-polygon against the bundled IANA zone boundaries - the zone
    a point is actually in, not the nearest zone's reference city."""

    @pytest.mark.parametrize(
        ("lat", "lon", "expected"),
        [
            (38.04, -84.50, "America/New_York"),  # Lexington, KY
            (38.25, -85.76, "America/Kentucky/Louisville"),
            (37.08, -88.60, "America/Chicago"),  # Paducah, KY (Central)
            (36.16, -86.78, "America/Chicago"),  # Nashville, TN
            (45.76, 4.83, "Europe/Paris"),  # Lyon - nearest city is Zurich
            (44.84, -0.58, "Europe/Paris"),  # Bordeaux - nearest is Andorra
            (49.84, 24.03, "Europe/Kyiv"),  # Lviv - nearest is Warsaw
            (39.74, -104.99, "America/Denver"),
            (33.45, -112.07, "America/Phoenix"),
            (-31.95, 115.86, "Australia/Perth"),
            (35.68, 139.76, "Asia/Tokyo"),
        ],
    )
    def test_resolves_the_zone_containing_the_point(self, lat, lon, expected) -> None:
        assert timezone_at(lat, lon) == expected

    def test_open_ocean_returns_empty_string(self) -> None:
        assert timezone_at(30.0, -40.0) == ""

    def test_just_off_the_coast_snaps_to_the_nearby_zone(self) -> None:
        # ~40 km off Brittany: outside the territorial sea, still clearly France.
        assert timezone_at(48.2, -5.4) == "Europe/Paris"

    def test_every_bundled_zone_is_known_to_the_system_tz_database(self) -> None:
        from zoneinfo import available_timezones

        from xen.utils.timezone_lookup import _boundaries

        assert set(_boundaries()) <= available_timezones()


class TestCoordinateForTimezone:
    def test_returns_the_zones_own_coordinate(self) -> None:
        entries = [("Europe/Paris", 48.85, 2.35), ("Asia/Tokyo", 35.68, 139.76)]
        assert coordinate_for_timezone("Asia/Tokyo", entries) == (35.68, 139.76)

    def test_unknown_timezone_returns_none(self) -> None:
        entries = [("Europe/Paris", 48.85, 2.35)]
        assert coordinate_for_timezone("Not/AZone", entries) is None

    def test_defaults_to_the_real_system_file(self) -> None:
        lat, lon = coordinate_for_timezone("Europe/Paris")
        assert lat == pytest.approx(48.85, abs=0.1)
        assert lon == pytest.approx(2.35, abs=0.1)
