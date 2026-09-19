"""Auckland Transport GTFS cache used to geocode HOP stops and infer routes.

The AT HOP transaction API only reports a stop name and time for each tap, so
the route travelled is inferred by finding a scheduled trip that serves the
tag-on stop and then the tag-off stop close to the tag-on time.

The feed is cached in its own SQLite file (rebuildable, not worth backing up).
"""

import csv
import io
import logging
import os
import re
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, time, timedelta
from typing import Iterator, List, NamedTuple, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger("athop.gtfs")

AUCKLAND_TZ = ZoneInfo("Pacific/Auckland")

Point = Tuple[float, float]  # (lat, lon)

# AT HOP transaction locations and GTFS stop names use inconsistent
# abbreviations ("Sunnynook Rd" vs "Sunnynook Road"), so both sides are
# normalised before matching.
STOP_NAME_ABBREVIATIONS = {
    "st": "street",
    "rd": "road",
    "ave": "avenue",
    "av": "avenue",
    "dr": "drive",
    "tce": "terrace",
    "pl": "place",
    "hwy": "highway",
    "cres": "crescent",
    "opp": "opposite",
}

SCHEMA = """
CREATE TABLE stops (
    stop_id TEXT PRIMARY KEY,
    stop_code TEXT,
    stop_name TEXT,
    name_key TEXT,
    lat REAL,
    lon REAL,
    parent_station TEXT
);

CREATE TABLE routes (
    route_id TEXT PRIMARY KEY,
    route_short_name TEXT,
    route_long_name TEXT,
    route_type INTEGER
);

CREATE TABLE trips (
    trip_id TEXT PRIMARY KEY,
    route_id TEXT,
    service_id TEXT,
    shape_id TEXT,
    trip_headsign TEXT
);

CREATE TABLE stop_times (
    trip_id TEXT,
    stop_sequence INTEGER,
    stop_id TEXT,
    departure_secs INTEGER
);

CREATE TABLE shapes (
    shape_id TEXT,
    seq INTEGER,
    lat REAL,
    lon REAL
);

CREATE TABLE calendar (
    service_id TEXT PRIMARY KEY,
    weekdays TEXT,
    start_date TEXT,
    end_date TEXT
);

CREATE TABLE calendar_dates (
    service_id TEXT,
    date TEXT,
    exception_type INTEGER,
    PRIMARY KEY (service_id, date)
);

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# Built after the bulk load so inserts don't maintain them row by row
INDEXES = """
CREATE INDEX stops_name_key ON stops (name_key);
CREATE INDEX stops_parent ON stops (parent_station);
CREATE INDEX stop_times_stop ON stop_times (stop_id, trip_id);
CREATE INDEX shapes_id ON shapes (shape_id, seq);
"""


class Journey(NamedTuple):
    """An inferred journey between two stops."""

    origin: Point
    destination: Point
    route_short_name: Optional[str]
    route_long_name: Optional[str]
    trip_headsign: Optional[str]
    path: List[Point]  # Shape between the stops, or a straight line


def normalize_stop_name(name: str) -> str:
    """Normalise a stop name for matching HOP locations against GTFS stops."""
    name = name.lower().strip()
    name = re.sub(r"\s+(bus station|bus interchange|interchange)$", "", name)
    name = re.sub(r"\s+ferry terminal$", " terminal", name)
    name = re.sub(r"\s*/\s*", " / ", name)
    tokens = [STOP_NAME_ABBREVIATIONS.get(t, t) for t in re.split(r"\s+", name)]
    return " ".join(tokens)


def parse_gtfs_time(value: str) -> Optional[int]:
    """Parse GTFS HH:MM:SS (hours may exceed 23) to seconds since midnight."""
    try:
        h, m, s = value.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    except ValueError:
        return None


def encode_polyline(points: Sequence[Point]) -> str:
    """Encode (lat, lon) points using the Google polyline algorithm."""

    def encode_value(value: int) -> str:
        value = ~(value << 1) if value < 0 else value << 1
        chunks = ""
        while value >= 0x20:
            chunks += chr((0x20 | (value & 0x1F)) + 63)
            value >>= 5
        return chunks + chr(value + 63)

    output = ""
    prev_lat = prev_lon = 0
    for lat, lon in points:
        lat_i, lon_i = round(lat * 1e5), round(lon * 1e5)
        output += encode_value(lat_i - prev_lat) + encode_value(lon_i - prev_lon)
        prev_lat, prev_lon = lat_i, lon_i
    return output


def _perpendicular_distance(point: Point, start: Point, end: Point) -> float:
    if start == end:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    dx, dy = end[0] - start[0], end[1] - start[1]
    num = abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0])
    return num / (dx * dx + dy * dy) ** 0.5


def simplify_path(points: List[Point], tolerance: float = 0.00005) -> List[Point]:
    """Ramer-Douglas-Peucker simplification (tolerance in degrees, ~5m)."""
    if len(points) < 3:
        return points
    # Iterative to avoid recursion limits on long shapes
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        max_dist, index = 0.0, -1
        for i in range(start + 1, end):
            d = _perpendicular_distance(points[i], points[start], points[end])
            if d > max_dist:
                max_dist, index = d, i
        if index != -1 and max_dist > tolerance:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [p for p, k in zip(points, keep) if k]


def _nearest_index(points: Sequence[Point], target: Point) -> int:
    return min(
        range(len(points)),
        key=lambda i: (points[i][0] - target[0]) ** 2 + (points[i][1] - target[1]) ** 2,
    )


class GtfsStore:
    """SQLite-backed cache of the AT GTFS feed."""

    def __init__(self, path: str) -> None:
        self.path = path

    def available(self) -> bool:
        return os.path.exists(self.path)

    def connect(self) -> "closing[sqlite3.Connection]":
        """Connection context manager that closes on exit (sqlite3's own
        context manager only commits/rolls back)."""
        return closing(sqlite3.connect(self.path))

    # -- Loading -------------------------------------------------------------

    def is_stale(self, max_age: timedelta) -> bool:
        if not self.available():
            return True
        try:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'fetched_at'"
                ).fetchone()
        except sqlite3.Error:
            return True
        if not row:
            return True
        fetched_at = datetime.fromisoformat(row[0])
        return datetime.now(fetched_at.tzinfo) - fetched_at >= max_age

    def refresh(self, url: str, now: datetime, timeout: int = 300) -> bool:
        """Download the feed and rebuild the cache atomically."""
        logger.info(f"Refreshing GTFS feed from {url}")
        # Fixed temp names so an interrupted build is overwritten next time
        # rather than accumulating in the data directory
        tmp_zip = self.path + ".zip.tmp"
        tmp_db = self.path + ".tmp"
        try:
            with requests.get(url, timeout=timeout, stream=True) as response:
                response.raise_for_status()
                with open(tmp_zip, "wb") as f:
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        f.write(chunk)

            if os.path.exists(tmp_db):
                os.unlink(tmp_db)
            with (
                zipfile.ZipFile(tmp_zip) as zf,
                closing(sqlite3.connect(tmp_db)) as conn,
            ):
                # Throwaway database: durability doesn't matter until the
                # final rename, so skip journaling and fsync for speed
                conn.execute("PRAGMA journal_mode = OFF")
                conn.execute("PRAGMA synchronous = OFF")
                conn.executescript(SCHEMA)
                self._load(conn, zf)
                conn.executescript(INDEXES)
                conn.execute(
                    "INSERT INTO meta VALUES ('fetched_at', ?)", (now.isoformat(),)
                )
                conn.commit()
            os.replace(tmp_db, self.path)
        except Exception as e:
            # Feed contents are outside our control; any parse error must be
            # survivable so the scraper keeps running with the old cache
            logger.error(f"Failed to load GTFS feed: {e}")
            return False
        finally:
            for tmp in (tmp_zip, tmp_db):
                if os.path.exists(tmp):
                    os.unlink(tmp)

        with self.connect() as conn:
            counts = {
                t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("stops", "routes", "trips", "stop_times", "shapes")
            }
        logger.info(f"Loaded GTFS feed: {counts}")
        return True

    @staticmethod
    def _rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict]:
        with zf.open(name) as raw:
            yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))

    def _load(self, conn: sqlite3.Connection, zf: zipfile.ZipFile) -> None:
        conn.executemany(
            "INSERT OR IGNORE INTO stops VALUES (?,?,?,?,?,?,?)",
            (
                (
                    r["stop_id"],
                    r.get("stop_code"),
                    r["stop_name"],
                    normalize_stop_name(r["stop_name"]),
                    float(r["stop_lat"]),
                    float(r["stop_lon"]),
                    r.get("parent_station") or None,
                )
                for r in self._rows(zf, "stops.txt")
                if r.get("stop_lat") and r.get("stop_lon")
            ),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO routes VALUES (?,?,?,?)",
            (
                (
                    r["route_id"],
                    r.get("route_short_name"),
                    r.get("route_long_name"),
                    int(r["route_type"]) if r.get("route_type") else None,
                )
                for r in self._rows(zf, "routes.txt")
            ),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO trips VALUES (?,?,?,?,?)",
            (
                (
                    r["trip_id"],
                    r["route_id"],
                    r["service_id"],
                    r.get("shape_id") or None,
                    r.get("trip_headsign"),
                )
                for r in self._rows(zf, "trips.txt")
            ),
        )
        conn.executemany(
            "INSERT INTO stop_times VALUES (?,?,?,?)",
            (
                (
                    r["trip_id"],
                    int(r["stop_sequence"]),
                    r["stop_id"],
                    parse_gtfs_time(r.get("departure_time") or r["arrival_time"]),
                )
                for r in self._rows(zf, "stop_times.txt")
            ),
        )
        conn.executemany(
            "INSERT INTO shapes VALUES (?,?,?,?)",
            (
                (
                    r["shape_id"],
                    int(r["shape_pt_sequence"]),
                    float(r["shape_pt_lat"]),
                    float(r["shape_pt_lon"]),
                )
                for r in self._rows(zf, "shapes.txt")
            ),
        )
        if "calendar.txt" in zf.namelist():
            conn.executemany(
                "INSERT OR IGNORE INTO calendar VALUES (?,?,?,?)",
                (
                    (
                        r["service_id"],
                        "".join(
                            r[d]
                            for d in (
                                "monday",
                                "tuesday",
                                "wednesday",
                                "thursday",
                                "friday",
                                "saturday",
                                "sunday",
                            )
                        ),
                        r["start_date"],
                        r["end_date"],
                    )
                    for r in self._rows(zf, "calendar.txt")
                ),
            )
        if "calendar_dates.txt" in zf.namelist():
            conn.executemany(
                "INSERT OR IGNORE INTO calendar_dates VALUES (?,?,?)",
                (
                    (r["service_id"], r["date"], int(r["exception_type"]))
                    for r in self._rows(zf, "calendar_dates.txt")
                ),
            )

    # -- Queries -------------------------------------------------------------

    @staticmethod
    def _stop_ids_for_name(conn: sqlite3.Connection, name: str) -> List[str]:
        """All stop IDs matching a HOP location, including station platforms."""
        key = normalize_stop_name(name)
        rows = conn.execute(
            """
            SELECT stop_id FROM stops WHERE name_key = ?
            UNION
            SELECT stop_id FROM stops
            WHERE parent_station IN (SELECT stop_id FROM stops WHERE name_key = ?)
            """,
            (key, key),
        ).fetchall()
        return [r[0] for r in rows]

    def lookup_stop(self, name: str) -> Optional[Point]:
        """Resolve a HOP transaction location to (lat, lon)."""
        if not self.available():
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT lat, lon FROM stops WHERE name_key = ? LIMIT 1",
                (normalize_stop_name(name),),
            ).fetchone()
        return (row[0], row[1]) if row else None

    @staticmethod
    def _service_active(conn: sqlite3.Connection, service_id: str, date: str) -> bool:
        exc = conn.execute(
            "SELECT exception_type FROM calendar_dates WHERE service_id = ? AND date = ?",
            (service_id, date),
        ).fetchone()
        if exc:
            return exc[0] == 1
        cal = conn.execute(
            "SELECT weekdays, start_date, end_date FROM calendar WHERE service_id = ?",
            (service_id,),
        ).fetchone()
        if not cal:
            return False
        weekdays, start, end = cal
        weekday = datetime.strptime(date, "%Y%m%d").weekday()
        return start <= date <= end and weekdays[weekday] == "1"

    def find_journey(
        self,
        origin_name: str,
        destination_name: str,
        tag_on: datetime,
        window: timedelta = timedelta(minutes=45),
    ) -> Optional[Journey]:
        """Infer the trip taken between two stops around the tag-on time.

        Returns None if either stop is unknown. If no scheduled trip matches,
        the journey has no route and a straight-line path.
        """
        if not self.available():
            return None

        with self.connect() as conn:
            origin_ids = self._stop_ids_for_name(conn, origin_name)
            dest_ids = self._stop_ids_for_name(conn, destination_name)
            if not origin_ids or not dest_ids:
                return None

            origin_point = self._stop_point(conn, origin_ids[0])
            dest_point = self._stop_point(conn, dest_ids[0])
            fallback = Journey(
                origin_point, dest_point, None, None, None, [origin_point, dest_point]
            )

            placeholders_o = ",".join("?" * len(origin_ids))
            placeholders_d = ",".join("?" * len(dest_ids))
            candidates = conn.execute(
                f"""
                SELECT st1.trip_id, st1.departure_secs, st1.stop_id, st2.stop_id,
                       t.route_id, t.service_id, t.shape_id, t.trip_headsign
                FROM stop_times st1
                JOIN stop_times st2
                  ON st2.trip_id = st1.trip_id AND st2.stop_sequence > st1.stop_sequence
                JOIN trips t ON t.trip_id = st1.trip_id
                WHERE st1.stop_id IN ({placeholders_o})
                  AND st2.stop_id IN ({placeholders_d})
                """,
                origin_ids + dest_ids,
            ).fetchall()

            # Score each candidate by how close its scheduled departure is to
            # the tag-on. GTFS times can exceed 24:00, so also try the
            # previous service day.
            best = None
            tag_on = tag_on.astimezone(AUCKLAND_TZ)
            for day_offset in (0, 1):
                service_day = (tag_on - timedelta(days=day_offset)).date()
                date = service_day.strftime("%Y%m%d")
                # Midnight in local time, not the tag-on's fixed UTC offset,
                # which differs from midnight's on DST transition days
                midnight = datetime.combine(service_day, time(), tzinfo=AUCKLAND_TZ)
                tag_on_secs = (tag_on - midnight).total_seconds()
                for (
                    trip_id,
                    dep,
                    o_id,
                    d_id,
                    route_id,
                    service_id,
                    shape_id,
                    headsign,
                ) in candidates:
                    if dep is None:
                        continue
                    delta = abs(dep - tag_on_secs)
                    if delta > window.total_seconds():
                        continue
                    if best and delta >= best[0]:
                        continue
                    if not self._service_active(conn, service_id, date):
                        continue
                    best = (delta, trip_id, o_id, d_id, route_id, shape_id, headsign)

            if not best:
                return fallback

            _, trip_id, o_id, d_id, route_id, shape_id, headsign = best
            origin_point = self._stop_point(conn, o_id)
            dest_point = self._stop_point(conn, d_id)
            route = conn.execute(
                "SELECT route_short_name, route_long_name FROM routes WHERE route_id = ?",
                (route_id,),
            ).fetchone()
            path = self._shape_between(conn, shape_id, origin_point, dest_point)
            return Journey(
                origin_point,
                dest_point,
                route[0] if route else None,
                route[1] if route else None,
                headsign,
                path or [origin_point, dest_point],
            )

    @staticmethod
    def _stop_point(conn: sqlite3.Connection, stop_id: str) -> Point:
        row = conn.execute(
            "SELECT lat, lon FROM stops WHERE stop_id = ?", (stop_id,)
        ).fetchone()
        return (row[0], row[1])

    @staticmethod
    def _shape_between(
        conn: sqlite3.Connection,
        shape_id: Optional[str],
        origin: Point,
        destination: Point,
    ) -> List[Point]:
        """Slice a trip's shape to the segment between two stops."""
        if not shape_id:
            return []
        points: List[Point] = [
            (lat, lon)
            for lat, lon in conn.execute(
                "SELECT lat, lon FROM shapes WHERE shape_id = ? ORDER BY seq",
                (shape_id,),
            )
        ]
        if len(points) < 2:
            return []
        start = _nearest_index(points, origin)
        end = _nearest_index(points, destination)
        if end <= start:
            return []
        return [origin] + points[start : end + 1] + [destination]
