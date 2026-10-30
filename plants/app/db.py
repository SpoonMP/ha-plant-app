"""SQLite storage for the Plants app.

Everything lives in one file in the app's data folder (/data on Home Assistant),
which Home Assistant includes in its backups.
"""

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

DATA_DIR = os.environ.get("PLANTS_DATA_DIR", "/data").strip()
DB_PATH = os.path.join(DATA_DIR, "plants.db")

SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS areas (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    sort  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spots (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id INTEGER NOT NULL REFERENCES areas(id) ON DELETE RESTRICT,
    name    TEXT NOT NULL,
    sort    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (area_id, name)
);
CREATE TABLE IF NOT EXISTS plants (
    number        INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    species       TEXT NOT NULL DEFAULT '',
    spot_id       INTEGER REFERENCES spots(id) ON DELETE RESTRICT,
    position      TEXT NOT NULL DEFAULT '',
    pot           TEXT NOT NULL DEFAULT '',
    watering_rule TEXT NOT NULL DEFAULT '',
    grow_light    TEXT NOT NULL DEFAULT '',
    care_notes    TEXT NOT NULL DEFAULT '',
    soil_sensor   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS location_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_number INTEGER NOT NULL REFERENCES plants(number) ON DELETE CASCADE,
    at           TEXT NOT NULL,
    from_text    TEXT NOT NULL,
    to_text      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS photos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_number INTEGER NOT NULL REFERENCES plants(number) ON DELETE CASCADE,
    taken_at     TEXT NOT NULL,
    uploaded_at  TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    width        INTEGER NOT NULL,
    height       INTEGER NOT NULL,
    bytes        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS photos_by_plant ON photos (plant_number, taken_at);
CREATE TABLE IF NOT EXISTS care_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_number INTEGER NOT NULL REFERENCES plants(number) ON DELETE CASCADE,
    at           TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('watered', 'checked')),
    fed          INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'app'
);
CREATE INDEX IF NOT EXISTS care_by_plant ON care_log (plant_number, at);
CREATE TABLE IF NOT EXISTS photo_sets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_number INTEGER NOT NULL REFERENCES plants(number) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS sets_by_plant ON photo_sets (plant_number, created_at);
CREATE TABLE IF NOT EXISTS diagnoses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_number INTEGER NOT NULL REFERENCES plants(number) ON DELETE CASCADE,
    at           TEXT NOT NULL,
    finding      TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'claude',
    seen         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS diagnoses_by_plant ON diagnoses (plant_number, at);
CREATE TABLE IF NOT EXISTS diagnosis_actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis_id INTEGER NOT NULL REFERENCES diagnoses(id) ON DELETE CASCADE,
    sort         INTEGER NOT NULL DEFAULT 0,
    text         TEXT NOT NULL,
    done_at      TEXT
);
CREATE TABLE IF NOT EXISTS soil_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plant_number INTEGER NOT NULL REFERENCES plants(number) ON DELETE CASCADE,
    care_id      INTEGER REFERENCES care_log(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('before', 'wet', 'moist')),
    entity       TEXT NOT NULL,
    due_at       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'missing')),
    value        REAL,
    read_at      TEXT
);
CREATE INDEX IF NOT EXISTS soil_by_plant ON soil_readings (plant_number, due_at);
CREATE INDEX IF NOT EXISTS soil_pending ON soil_readings (status, due_at);
CREATE TABLE IF NOT EXISTS diagnosis_photos (
    diagnosis_id INTEGER NOT NULL REFERENCES diagnoses(id) ON DELETE CASCADE,
    photo_id     INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    PRIMARY KEY (diagnosis_id, photo_id)
);
"""

PLANT_TEXT_FIELDS = (
    "name", "species", "position", "pot", "watering_rule",
    "grow_light", "care_notes", "soil_sensor",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def session():
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with session() as con:
        con.executescript(SCHEMA)
        con.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        con.execute(
            "UPDATE meta SET value=? WHERE key='schema_version' AND CAST(value AS INTEGER) < ?",
            (str(SCHEMA_VERSION), SCHEMA_VERSION),
        )
        # Highest plant number ever used, so numbers of removed plants are never reused.
        con.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('max_number', '0')")
        _migrate_photo_sets(con)
        _migrate_soil(con)


def _migrate_photo_sets(con) -> None:
    """0.4.0: photos belong to a set (one upload). Older photos each become a set of one."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(photos)")}
    if "set_id" not in cols:
        con.execute("ALTER TABLE photos ADD COLUMN set_id INTEGER REFERENCES photo_sets(id)")
    for r in con.execute("SELECT id, plant_number, uploaded_at FROM photos WHERE set_id IS NULL").fetchall():
        set_id = con.execute(
            "INSERT INTO photo_sets (plant_number, created_at, note) VALUES (?,?,'')",
            (r["plant_number"], r["uploaded_at"]),
        ).lastrowid
        con.execute("UPDATE photos SET set_id=? WHERE id=?", (set_id, r["id"]))
    con.execute("CREATE INDEX IF NOT EXISTS photos_by_set ON photos (set_id)")


def _migrate_soil(con) -> None:
    """0.5.0: per-plant soil threshold (blank = not calibrated)."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(plants)")}
    if "soil_threshold" not in cols:
        con.execute("ALTER TABLE plants ADD COLUMN soil_threshold REAL")


def norm_time(value) -> str:
    """Any ISO date/time from a caller → the app's own format (local time, seconds)."""
    try:
        when = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        raise UserError(f"'{value}' isn't a date and time (use ISO, e.g. 2026-10-25T21:00).")
    return when.astimezone().isoformat(timespec="seconds")


# ---------- helpers ----------

class UserError(ValueError):
    """An error to show to the user as-is."""


def _bump_max_number(con, number: int) -> None:
    current = int(con.execute("SELECT value FROM meta WHERE key='max_number'").fetchone()[0])
    if number > current:
        con.execute("UPDATE meta SET value=? WHERE key='max_number'", (str(number),))


def next_number(con) -> int:
    row = con.execute("SELECT value FROM meta WHERE key='max_number'").fetchone()
    return int(row[0]) + 1


def location_text(con, spot_id, position: str) -> str:
    if spot_id is None:
        text = "No location"
    else:
        row = con.execute(
            "SELECT a.name AS area, s.name AS spot FROM spots s JOIN areas a ON a.id=s.area_id WHERE s.id=?",
            (spot_id,),
        ).fetchone()
        text = f"{row['area']} › {row['spot']}" if row else "No location"
    if position:
        text += f" · {position}"
    return text


def plant_dict(con, row) -> dict:
    d = dict(row)
    loc = None
    if d["spot_id"] is not None:
        r = con.execute(
            "SELECT s.id AS spot_id, s.name AS spot, a.id AS area_id, a.name AS area "
            "FROM spots s JOIN areas a ON a.id=s.area_id WHERE s.id=?",
            (d["spot_id"],),
        ).fetchone()
        loc = dict(r) if r else None
    d["area"] = loc["area"] if loc else None
    d["area_id"] = loc["area_id"] if loc else None
    d["spot"] = loc["spot"] if loc else None
    d["location"] = location_text(con, d["spot_id"], d["position"])
    latest = latest_photo(con, d["number"])
    d["latest_photo"] = {"id": latest["id"], "taken_at": latest["taken_at"]} if latest else None
    d["photo_count"] = con.execute(
        "SELECT COUNT(*) FROM photos WHERE plant_number=?", (d["number"],)
    ).fetchone()[0]
    d.update(care_summary(con, d["number"]))
    d.update(diagnosis_summary(con, d["number"]))
    return d


# ---------- reads ----------

def overview(con) -> dict:
    areas = [dict(r) for r in con.execute("SELECT * FROM areas ORDER BY sort, name")]
    spots = [dict(r) for r in con.execute("SELECT * FROM spots ORDER BY sort, name")]
    plants = [plant_dict(con, r) for r in con.execute("SELECT * FROM plants ORDER BY number")]
    by_spot: dict = {}
    unplaced = []
    for p in plants:
        if p["spot_id"] is None:
            unplaced.append(p)
        else:
            by_spot.setdefault(p["spot_id"], []).append(p)
    for a in areas:
        a["spots"] = [
            {**s, "plants": by_spot.get(s["id"], [])}
            for s in spots if s["area_id"] == a["id"]
        ]
    return {
        "areas": areas, "unplaced": unplaced, "count": len(plants), "next_number": next_number(con),
        "waiting": [p["number"] for p in plants if p["waiting"]],
    }


def get_plant(con, number: int) -> dict:
    row = con.execute("SELECT * FROM plants WHERE number=?", (number,)).fetchone()
    if not row:
        raise KeyError(number)
    d = plant_dict(con, row)
    d["location_log"] = [
        dict(r) for r in con.execute(
            "SELECT at, from_text, to_text FROM location_log WHERE plant_number=? ORDER BY id DESC",
            (number,),
        )
    ]
    d["photos"] = list_photos(con, number)
    d["sets"] = list_sets(con, number, d["photos"])
    d["care_log"] = list_care(con, number)
    d["diagnoses"] = list_diagnoses(con, number)
    return d


def list_locations(con) -> list:
    areas = [dict(r) for r in con.execute("SELECT * FROM areas ORDER BY sort, name")]
    for a in areas:
        a["spots"] = [
            dict(r) for r in con.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM plants p WHERE p.spot_id=s.id) AS plant_count "
                "FROM spots s WHERE area_id=? ORDER BY sort, name",
                (a["id"],),
            )
        ]
    return areas


# ---------- plant writes ----------

def _clean_plant_fields(data: dict) -> dict:
    out = {}
    for f in PLANT_TEXT_FIELDS:
        if f in data:
            v = data[f]
            out[f] = "" if v is None else str(v).strip()
    if "name" in out and not out["name"]:
        raise UserError("Name can't be empty.")
    if out.get("soil_sensor") and not re.fullmatch(r"sensor\.[a-z0-9_]+", out["soil_sensor"]):
        raise UserError("Soil sensor must be a sensor entity ID, e.g. sensor.soil_sensor_bedroom_soil_moisture.")
    if "soil_threshold" in data:
        out["soil_threshold"] = parse_threshold(data["soil_threshold"])
    if "spot_id" in data:
        v = data["spot_id"]
        out["spot_id"] = int(v) if v not in (None, "", "null") else None
    return out


def parse_threshold(v):
    if v is None or str(v).strip() in ("", "null", "none"):
        return None
    try:
        t = float(str(v).strip().rstrip("%"))
    except ValueError:
        raise UserError("Soil threshold must be a number (0–100) or blank.")
    if not 0 <= t <= 100:
        raise UserError("Soil threshold must be between 0 and 100.")
    return round(t, 1)


def create_plant(con, data: dict) -> int:
    fields = _clean_plant_fields(data)
    if not fields.get("name"):
        raise UserError("Name can't be empty.")
    number = data.get("number")
    number = int(number) if number not in (None, "") else next_number(con)
    if number < 1:
        raise UserError("Plant number must be 1 or more.")
    if con.execute("SELECT 1 FROM plants WHERE number=?", (number,)).fetchone():
        raise UserError(f"Plant {number} already exists.")
    ts = now_iso()
    fields.setdefault("spot_id", None)
    cols = ["number", *fields.keys(), "created_at", "updated_at"]
    vals = [number, *fields.values(), ts, ts]
    con.execute(
        f"INSERT INTO plants ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals
    )
    _bump_max_number(con, number)
    return number


def update_plant(con, number: int, data: dict, log_move: bool = True) -> None:
    row = con.execute("SELECT * FROM plants WHERE number=?", (number,)).fetchone()
    if not row:
        raise KeyError(number)
    if "spot_id" not in data and data.get("area") and data.get("spot"):
        data = {**data, "spot_id": spot_id_by_name(con, data["area"], data["spot"])}
    fields = _clean_plant_fields(data)
    if not fields:
        return
    old_loc = location_text(con, row["spot_id"], row["position"])
    fields["updated_at"] = now_iso()
    con.execute(
        f"UPDATE plants SET {', '.join(f'{k}=?' for k in fields)} WHERE number=?",
        [*fields.values(), number],
    )
    new = con.execute("SELECT spot_id, position FROM plants WHERE number=?", (number,)).fetchone()
    new_loc = location_text(con, new["spot_id"], new["position"])
    if log_move and new_loc != old_loc:
        con.execute(
            "INSERT INTO location_log (plant_number, at, from_text, to_text) VALUES (?,?,?,?)",
            (number, fields["updated_at"], old_loc, new_loc),
        )


def spot_id_by_name(con, area: str, spot: str) -> int:
    """An existing spot, by area and spot name (e.g. for a move written from chat)."""
    area, spot = str(area).strip(), str(spot).strip()
    row = con.execute(
        "SELECT s.id FROM spots s JOIN areas a ON a.id=s.area_id WHERE a.name=? AND s.name=?",
        (area, spot),
    ).fetchone()
    if not row:
        known = [f"{r['a']} › {r['s']}" for r in con.execute(
            "SELECT a.name AS a, s.name AS s FROM spots s JOIN areas a ON a.id=s.area_id ORDER BY a.sort, s.sort")]
        raise UserError(f"No spot '{spot}' in area '{area}'. Spots: {', '.join(known)}")
    return row[0]


def delete_plant(con, number: int) -> None:
    cur = con.execute("DELETE FROM plants WHERE number=?", (number,))
    if cur.rowcount == 0:
        raise KeyError(number)


# ---------- photos ----------

PHOTO_FIELDS = ("p.id, p.plant_number, p.set_id, p.taken_at, p.uploaded_at, p.note, "
                "p.width, p.height, p.bytes, s.note AS set_note, s.created_at AS set_created_at")
PHOTO_FROM = "photos p LEFT JOIN photo_sets s ON s.id = p.set_id"
# Newest set first; inside a set, in the order the photos were taken.
PHOTO_ORDER = "s.created_at DESC, p.set_id DESC, p.taken_at, p.id"


def list_photos(con, number: int) -> list:
    """All photos of a plant: newest set first, each set's photos in the order taken."""
    return [dict(r) for r in con.execute(
        f"SELECT {PHOTO_FIELDS} FROM {PHOTO_FROM} WHERE p.plant_number=? ORDER BY {PHOTO_ORDER}",
        (number,),
    )]


def list_sets(con, number: int, photos: list | None = None) -> list:
    """A plant's photo sets, newest first, each with its photos."""
    photos = photos if photos is not None else list_photos(con, number)
    out, by_id = [], {}
    for ph in photos:
        s = by_id.get(ph["set_id"])
        if s is None:
            s = {"id": ph["set_id"], "created_at": ph["set_created_at"], "note": ph["set_note"] or "", "photos": []}
            by_id[ph["set_id"]] = s
            out.append(s)
        s["photos"].append(ph)
    return out


def latest_photo(con, number: int):
    """The first photo of the newest set: shown on the tile and in HA's camera entity."""
    row = con.execute(
        f"SELECT {PHOTO_FIELDS} FROM {PHOTO_FROM} WHERE p.plant_number=? ORDER BY {PHOTO_ORDER} LIMIT 1",
        (number,),
    ).fetchone()
    return dict(row) if row else None


def get_photo(con, photo_id: int) -> dict:
    row = con.execute(f"SELECT {PHOTO_FIELDS} FROM {PHOTO_FROM} WHERE p.id=?", (photo_id,)).fetchone()
    if not row:
        raise KeyError(photo_id)
    return dict(row)


def create_set(con, number: int, note: str = "") -> int:
    if not con.execute("SELECT 1 FROM plants WHERE number=?", (number,)).fetchone():
        raise KeyError(number)
    return con.execute(
        "INSERT INTO photo_sets (plant_number, created_at, note) VALUES (?,?,?)",
        (number, now_iso(), (note or "").strip()[:1000]),
    ).lastrowid


def get_set(con, set_id: int) -> dict:
    row = con.execute("SELECT * FROM photo_sets WHERE id=?", (set_id,)).fetchone()
    if not row:
        raise KeyError(set_id)
    return dict(row)


def update_set_note(con, set_id: int, note: str) -> dict:
    s = get_set(con, set_id)
    con.execute("UPDATE photo_sets SET note=? WHERE id=?", ((note or "").strip()[:1000], set_id))
    return s


def update_photo_note(con, photo_id: int, note: str) -> dict:
    p = get_photo(con, photo_id)
    con.execute("UPDATE photos SET note=? WHERE id=?", ((note or "").strip()[:500], photo_id))
    return p


def add_photo(con, number: int, set_id: int, taken_at: str, note: str,
              width: int, height: int, size: int) -> int:
    s = get_set(con, set_id)
    if s["plant_number"] != number:
        raise UserError("That photo set belongs to another plant.")
    return con.execute(
        "INSERT INTO photos (plant_number, set_id, taken_at, uploaded_at, note, width, height, bytes) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (number, set_id, taken_at, now_iso(), (note or "").strip()[:500], width, height, size),
    ).lastrowid


def delete_photo(con, photo_id: int) -> dict:
    """Delete a photo; a set left empty goes too."""
    p = get_photo(con, photo_id)
    con.execute("DELETE FROM photos WHERE id=?", (photo_id,))
    if p["set_id"] is not None and not con.execute(
            "SELECT 1 FROM photos WHERE set_id=?", (p["set_id"],)).fetchone():
        con.execute("DELETE FROM photo_sets WHERE id=?", (p["set_id"],))
    return p


def delete_empty_set(con, set_id: int) -> None:
    if not con.execute("SELECT 1 FROM photos WHERE set_id=?", (set_id,)).fetchone():
        con.execute("DELETE FROM photo_sets WHERE id=?", (set_id,))


def photos_to_thin(con, cutoff_iso: str) -> list:
    """Photos older than the cutoff that retention removes: per plant and calendar
    month, only the newest photo is kept."""
    rows = con.execute(
        "SELECT id, plant_number, taken_at FROM photos WHERE taken_at < ? "
        "ORDER BY plant_number, taken_at DESC, id DESC",
        (cutoff_iso,),
    ).fetchall()
    seen, remove = set(), []
    for r in rows:
        key = (r["plant_number"], r["taken_at"][:7])
        if key in seen:
            remove.append(r["id"])
        else:
            seen.add(key)
    return remove


def photo_stats(con) -> dict:
    count, size = con.execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM photos").fetchone()
    return {"count": count, "megabytes": round(size / 1_000_000, 1)}


def plant_numbers(con) -> list:
    return [r[0] for r in con.execute("SELECT number FROM plants ORDER BY number")]


# ---------- watering ----------

CARE_KINDS = ("watered", "checked")
CARE_FIELDS = "id, plant_number, at, kind, fed, note, source"


def care_summary(con, number: int) -> dict:
    """Last watered / last checked (either kind) / last fed for a plant."""
    def last(where: str):
        row = con.execute(
            f"SELECT MAX(at) FROM care_log WHERE plant_number=? AND {where}", (number,)
        ).fetchone()
        return row[0] if row else None
    return {
        "last_watered": last("kind='watered'"),
        "last_checked": last("1=1"),
        "last_fed": last("fed=1"),
    }


def list_care(con, number: int, limit: int = 30) -> list:
    return [dict(r) for r in con.execute(
        f"SELECT {CARE_FIELDS} FROM care_log WHERE plant_number=? ORDER BY at DESC, id DESC LIMIT ?",
        (number, limit),
    )]


def add_care(con, number: int, kind: str, fed: bool = False, note: str = "",
             source: str = "app", at: str | None = None, queue_soil: bool = True) -> int:
    if kind not in CARE_KINDS:
        raise UserError("Unknown entry — use watered or checked.")
    plant = con.execute("SELECT soil_sensor FROM plants WHERE number=?", (number,)).fetchone()
    if not plant:
        raise KeyError(number)
    if fed and kind != "watered":
        raise UserError("Feed is only logged together with watering.")
    at = at or now_iso()
    care_id = con.execute(
        "INSERT INTO care_log (plant_number, at, kind, fed, note, source) VALUES (?,?,?,?,?,?)",
        (number, at, kind, 1 if fed else 0, (note or "").strip()[:500], source),
    ).lastrowid
    if queue_soil and plant["soil_sensor"]:
        queue_soil_readings(con, number, care_id, kind, at, plant["soil_sensor"])
    return care_id


# ---------- soil calibration readings ----------

# A Watered log records the reading just before watering ("before" = the plant's dry
# point, when Michael decided it needed water) and the reading ~1 h later ("wet").
# A "Checked, still moist" log records the reading at that moment ("moist").
WET_AFTER = timedelta(minutes=60)
SOIL_KIND_LABELS = {"before": "Before watering", "wet": "1 h after watering", "moist": "Checked, still moist"}


def queue_soil_readings(con, number: int, care_id: int, kind: str, at: str, entity: str) -> None:
    rows = [("moist", at)] if kind == "checked" else [
        ("before", at),
        ("wet", (datetime.fromisoformat(at) + WET_AFTER).isoformat(timespec="seconds")),
    ]
    for rkind, due in rows:
        con.execute(
            "INSERT INTO soil_readings (plant_number, care_id, kind, entity, due_at) VALUES (?,?,?,?,?)",
            (number, care_id, rkind, entity, due),
        )


def due_soil_readings(con) -> list:
    """Pending readings whose time has come (compared as real times, not text)."""
    now = datetime.now().astimezone()
    out = []
    for r in con.execute("SELECT * FROM soil_readings WHERE status='pending' ORDER BY due_at"):
        if datetime.fromisoformat(r["due_at"]) <= now:
            out.append(dict(r))
    return out


def set_soil_reading(con, reading_id: int, status: str, value=None, read_at=None) -> None:
    con.execute(
        "UPDATE soil_readings SET status=?, value=?, read_at=? WHERE id=?",
        (status, value, read_at, reading_id),
    )


def list_soil_readings(con, number: int, limit: int = 20) -> list:
    rows = con.execute(
        "SELECT r.id, r.kind, r.due_at, r.status, r.value, r.read_at, r.care_id, c.at AS care_at "
        "FROM soil_readings r LEFT JOIN care_log c ON c.id=r.care_id "
        "WHERE r.plant_number=? ORDER BY r.due_at DESC, r.id DESC LIMIT ?",
        (number, limit),
    )
    return [{**dict(r), "label": SOIL_KIND_LABELS[r["kind"]]} for r in rows]


def soil_calibration(con, number: int) -> dict:
    """Readings collected so far, by kind, and a suggested threshold once there are
    at least two before-watering readings (their average — refined with every watering)."""
    out = {}
    for kind in ("before", "wet", "moist"):
        vals = [r[0] for r in con.execute(
            "SELECT value FROM soil_readings WHERE plant_number=? AND kind=? AND status='done' "
            "ORDER BY due_at DESC LIMIT 10", (number, kind))]
        out[kind] = {
            "count": len(vals),
            "average": round(sum(vals) / len(vals), 1) if vals else None,
            "low": min(vals) if vals else None,
            "high": max(vals) if vals else None,
        }
    before = out["before"]
    out["suggested_threshold"] = round(before["average"]) if before["count"] >= 2 else None
    out["pending"] = con.execute(
        "SELECT COUNT(*) FROM soil_readings WHERE plant_number=? AND status='pending'", (number,)
    ).fetchone()[0]
    return out


def soil_status(value, threshold) -> str:
    if value is None:
        return "no reading"
    if threshold is None:
        return "not calibrated"
    return "needs water" if value <= threshold else "ok"


def delete_care(con, care_id: int) -> int:
    row = con.execute("SELECT plant_number FROM care_log WHERE id=?", (care_id,)).fetchone()
    if not row:
        raise KeyError(care_id)
    con.execute("DELETE FROM care_log WHERE id=?", (care_id,))
    return row[0]


# ---------- diagnoses ----------

def _last_diagnosis_at(con, number: int):
    return con.execute("SELECT MAX(at) FROM diagnoses WHERE plant_number=?", (number,)).fetchone()[0]


def waiting_photos(con, number: int) -> list:
    """Photos added since the plant's last diagnosis (all photos if it has none)."""
    last = _last_diagnosis_at(con, number)
    return [dict(r) for r in con.execute(
        f"SELECT {PHOTO_FIELDS} FROM {PHOTO_FROM} WHERE p.plant_number=? AND (? IS NULL OR p.uploaded_at > ?) "
        f"ORDER BY {PHOTO_ORDER}",
        (number, last, last),
    )]


def diagnosis_summary(con, number: int) -> dict:
    last = _last_diagnosis_at(con, number)
    waiting = con.execute(
        "SELECT COUNT(*) FROM photos WHERE plant_number=? AND (? IS NULL OR uploaded_at > ?)",
        (number, last, last),
    ).fetchone()[0]
    unseen = con.execute(
        "SELECT COUNT(*) FROM diagnoses WHERE plant_number=? AND seen=0", (number,)
    ).fetchone()[0]
    open_actions = con.execute(
        "SELECT COUNT(*) FROM diagnosis_actions a JOIN diagnoses d ON d.id=a.diagnosis_id "
        "WHERE d.plant_number=? AND a.done_at IS NULL", (number,)
    ).fetchone()[0]
    return {
        "last_diagnosis": last,
        "waiting": waiting > 0,
        "waiting_photos": waiting,
        "new_diagnosis": unseen > 0,
        "open_actions": open_actions,
    }


def _diagnosis_dict(con, row) -> dict:
    d = dict(row)
    d["seen"] = bool(d["seen"])
    d["actions"] = [dict(a) for a in con.execute(
        "SELECT id, text, done_at FROM diagnosis_actions WHERE diagnosis_id=? ORDER BY sort, id", (d["id"],))]
    d["photo_ids"] = [r[0] for r in con.execute(
        "SELECT dp.photo_id FROM diagnosis_photos dp JOIN photos p ON p.id=dp.photo_id "
        "WHERE dp.diagnosis_id=? ORDER BY p.taken_at, p.id", (d["id"],))]
    return d


def list_diagnoses(con, number: int) -> list:
    """A plant's diagnoses, newest first, each with its actions and the photos it used."""
    return [_diagnosis_dict(con, r) for r in con.execute(
        "SELECT * FROM diagnoses WHERE plant_number=? ORDER BY at DESC, id DESC", (number,))]


def get_diagnosis(con, diagnosis_id: int) -> dict:
    row = con.execute("SELECT * FROM diagnoses WHERE id=?", (diagnosis_id,)).fetchone()
    if not row:
        raise KeyError(diagnosis_id)
    return _diagnosis_dict(con, row)


def _clean_actions(actions) -> list:
    if actions is None:
        return []
    if not isinstance(actions, list):
        raise UserError("Actions must be a list.")
    out = []
    for a in actions:
        text, done = (a.get("text"), a.get("done")) if isinstance(a, dict) else (a, False)
        text = str(text or "").strip()[:500]
        if text:
            out.append((text, bool(done)))
    return out


def add_diagnosis(con, number: int, finding: str, actions=None, photo_ids=None,
                  at=None, source: str = "claude", seen: bool = False) -> int:
    if not con.execute("SELECT 1 FROM plants WHERE number=?", (number,)).fetchone():
        raise KeyError(number)
    finding = str(finding or "").strip()[:5000]
    if not finding:
        raise UserError("A diagnosis needs a finding.")
    when = norm_time(at) if at else now_iso()
    did = con.execute(
        "INSERT INTO diagnoses (plant_number, at, finding, source, seen) VALUES (?,?,?,?,?)",
        (number, when, finding, str(source or "claude")[:20], 1 if seen else 0),
    ).lastrowid
    for i, (text, done) in enumerate(_clean_actions(actions)):
        con.execute(
            "INSERT INTO diagnosis_actions (diagnosis_id, sort, text, done_at) VALUES (?,?,?,?)",
            (did, i, text, when if done else None),
        )
    for pid in photo_ids or []:
        row = con.execute("SELECT plant_number FROM photos WHERE id=?", (int(pid),)).fetchone()
        if not row:
            raise UserError(f"Photo {pid} doesn't exist.")
        if row[0] != number:
            raise UserError(f"Photo {pid} belongs to plant {row[0]}, not {number}.")
        con.execute("INSERT OR IGNORE INTO diagnosis_photos (diagnosis_id, photo_id) VALUES (?,?)", (did, int(pid)))
    return did


def update_diagnosis(con, diagnosis_id: int, data: dict) -> int:
    d = get_diagnosis(con, diagnosis_id)
    if "finding" in data:
        finding = str(data["finding"] or "").strip()[:5000]
        if not finding:
            raise UserError("A diagnosis needs a finding.")
        con.execute("UPDATE diagnoses SET finding=?, seen=0 WHERE id=?", (finding, diagnosis_id))
    for text, done in _clean_actions(data.get("add_actions")):
        sort = con.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM diagnosis_actions WHERE diagnosis_id=?",
                           (diagnosis_id,)).fetchone()[0]
        con.execute("INSERT INTO diagnosis_actions (diagnosis_id, sort, text, done_at) VALUES (?,?,?,?)",
                    (diagnosis_id, sort, text, now_iso() if done else None))
    return d["plant_number"]


def delete_diagnosis(con, diagnosis_id: int) -> int:
    d = get_diagnosis(con, diagnosis_id)
    con.execute("DELETE FROM diagnoses WHERE id=?", (diagnosis_id,))
    return d["plant_number"]


def _action_plant(con, action_id: int) -> int:
    row = con.execute(
        "SELECT d.plant_number FROM diagnosis_actions a JOIN diagnoses d ON d.id=a.diagnosis_id WHERE a.id=?",
        (action_id,),
    ).fetchone()
    if not row:
        raise KeyError(action_id)
    return row[0]


def update_action(con, action_id: int, data: dict) -> int:
    number = _action_plant(con, action_id)
    if "done" in data:
        con.execute("UPDATE diagnosis_actions SET done_at=? WHERE id=?",
                    (now_iso() if data["done"] else None, action_id))
    if "text" in data:
        text = str(data["text"] or "").strip()[:500]
        if not text:
            raise UserError("An action can't be empty.")
        con.execute("UPDATE diagnosis_actions SET text=? WHERE id=?", (text, action_id))
    return number


def delete_action(con, action_id: int) -> int:
    number = _action_plant(con, action_id)
    con.execute("DELETE FROM diagnosis_actions WHERE id=?", (action_id,))
    return number


def mark_seen(con, number: int) -> bool:
    """Mark a plant's diagnoses as read. Returns True if anything changed."""
    return con.execute("UPDATE diagnoses SET seen=1 WHERE plant_number=? AND seen=0", (number,)).rowcount > 0


def check_bundle(con, number: int) -> dict:
    """Everything a plant check needs for one plant, in one read."""
    p = get_plant(con, number)
    waiting = waiting_photos(con, number)
    return {
        "number": p["number"], "name": p["name"], "species": p["species"],
        "location": p["location"], "position": p["position"], "pot": p["pot"],
        "grow_light": p["grow_light"], "watering_rule": p["watering_rule"],
        "care_notes": p["care_notes"], "soil_sensor": p["soil_sensor"],
        "soil_threshold": p["soil_threshold"],
        "soil_calibration": soil_calibration(con, number) if p["soil_sensor"] else None,
        "last_watered": p["last_watered"], "last_checked": p["last_checked"], "last_fed": p["last_fed"],
        "care_log": p["care_log"][:15],
        "location_log": p["location_log"][:5],
        "waiting_photos": [
            {k: ph[k] for k in ("id", "set_id", "taken_at", "uploaded_at", "note", "set_note")}
            for ph in waiting
        ],
        "diagnoses": p["diagnoses"][:5],
    }


# ---------- area / spot writes ----------

def create_area(con, name: str) -> int:
    name = (name or "").strip()
    if not name:
        raise UserError("Area name can't be empty.")
    if con.execute("SELECT 1 FROM areas WHERE name=?", (name,)).fetchone():
        raise UserError(f"Area '{name}' already exists.")
    sort = con.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM areas").fetchone()[0]
    return con.execute("INSERT INTO areas (name, sort) VALUES (?, ?)", (name, sort)).lastrowid


def rename_area(con, area_id: int, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise UserError("Area name can't be empty.")
    try:
        con.execute("UPDATE areas SET name=? WHERE id=?", (name, area_id))
    except sqlite3.IntegrityError:
        raise UserError(f"Area '{name}' already exists.")


def delete_area(con, area_id: int) -> None:
    if con.execute("SELECT 1 FROM spots WHERE area_id=?", (area_id,)).fetchone():
        raise UserError("Remove or move this area's spots first.")
    con.execute("DELETE FROM areas WHERE id=?", (area_id,))


def create_spot(con, area_id: int, name: str) -> int:
    name = (name or "").strip()
    if not name:
        raise UserError("Spot name can't be empty.")
    if not con.execute("SELECT 1 FROM areas WHERE id=?", (area_id,)).fetchone():
        raise UserError("That area doesn't exist.")
    if con.execute("SELECT 1 FROM spots WHERE area_id=? AND name=?", (area_id, name)).fetchone():
        raise UserError(f"Spot '{name}' already exists in this area.")
    sort = con.execute(
        "SELECT COALESCE(MAX(sort), -1) + 1 FROM spots WHERE area_id=?", (area_id,)
    ).fetchone()[0]
    return con.execute(
        "INSERT INTO spots (area_id, name, sort) VALUES (?, ?, ?)", (area_id, name, sort)
    ).lastrowid


def rename_spot(con, spot_id: int, name: str) -> None:
    name = (name or "").strip()
    if not name:
        raise UserError("Spot name can't be empty.")
    try:
        con.execute("UPDATE spots SET name=? WHERE id=?", (name, spot_id))
    except sqlite3.IntegrityError:
        raise UserError(f"Spot '{name}' already exists in this area.")


def delete_spot(con, spot_id: int) -> None:
    if con.execute("SELECT 1 FROM plants WHERE spot_id=?", (spot_id,)).fetchone():
        raise UserError("Move the plants in this spot somewhere else first.")
    con.execute("DELETE FROM spots WHERE id=?", (spot_id,))


def move(con, table: str, item_id: int, direction: int) -> None:
    """Swap an area or spot with its neighbour (direction -1 = up, +1 = down)."""
    if table == "areas":
        rows = [r["id"] for r in con.execute("SELECT id FROM areas ORDER BY sort, name")]
    else:
        area_id = con.execute("SELECT area_id FROM spots WHERE id=?", (item_id,)).fetchone()
        if not area_id:
            raise KeyError(item_id)
        rows = [r["id"] for r in con.execute(
            "SELECT id FROM spots WHERE area_id=? ORDER BY sort, name", (area_id[0],))]
    if item_id not in rows:
        raise KeyError(item_id)
    i = rows.index(item_id)
    j = i + direction
    if 0 <= j < len(rows):
        rows[i], rows[j] = rows[j], rows[i]
    for pos, rid in enumerate(rows):
        con.execute(f"UPDATE {table} SET sort=? WHERE id=?", (pos, rid))


# ---------- import / export ----------

def export_all(con) -> dict:
    areas = list_locations(con)
    spot_lookup = {}
    out_areas = []
    for a in areas:
        out_areas.append({"name": a["name"], "spots": [s["name"] for s in a["spots"]]})
        for s in a["spots"]:
            spot_lookup[s["id"]] = (a["name"], s["name"])
    plants = []
    for r in con.execute("SELECT * FROM plants ORDER BY number"):
        p = {"number": r["number"]}
        for f in PLANT_TEXT_FIELDS:
            p[f] = r[f]
        p["soil_threshold"] = r["soil_threshold"]
        p["soil_readings"] = [
            dict(x) for x in con.execute(
                "SELECT kind, entity, due_at, value, read_at FROM soil_readings "
                "WHERE plant_number=? AND status='done' ORDER BY due_at, id", (r["number"],),
            )
        ]
        area_spot = spot_lookup.get(r["spot_id"])
        p["area"], p["spot"] = area_spot if area_spot else (None, None)
        p["location_log"] = [
            dict(x) for x in con.execute(
                "SELECT at, from_text, to_text FROM location_log WHERE plant_number=? ORDER BY id",
                (r["number"],),
            )
        ]
        p["care_log"] = [
            dict(x) for x in con.execute(
                "SELECT at, kind, fed, note, source FROM care_log WHERE plant_number=? ORDER BY at, id",
                (r["number"],),
            )
        ]
        p["diagnoses"] = [
            {"at": d["at"], "finding": d["finding"], "source": d["source"],
             "actions": [{"text": a["text"], "done": a["done_at"] is not None} for a in d["actions"]]}
            for d in reversed(list_diagnoses(con, r["number"]))
        ]
        plants.append(p)
    return {
        "format": "ha-plant-app",
        "schema_version": SCHEMA_VERSION,
        "exported_at": now_iso(),
        "areas": out_areas,
        "plants": plants,
    }


def import_all(con, data: dict) -> dict:
    """Merge an export (or a hand-written seed) into the database.

    Areas and spots are matched by name and added if missing. Plants are matched
    by number: existing ones are updated (a location change is logged), new ones added.
    """
    if not isinstance(data, dict) or not isinstance(data.get("plants", []), list):
        raise UserError("That doesn't look like a plant list.")
    added = updated = 0

    def area_id_for(name: str) -> int:
        row = con.execute("SELECT id FROM areas WHERE name=?", (name,)).fetchone()
        return row[0] if row else create_area(con, name)

    def spot_id_for(area: str, spot: str) -> int:
        aid = area_id_for(area)
        row = con.execute("SELECT id FROM spots WHERE area_id=? AND name=?", (aid, spot)).fetchone()
        return row[0] if row else create_spot(con, aid, spot)

    for a in data.get("areas", []) or []:
        aid = area_id_for(str(a["name"]).strip())
        for s in a.get("spots", []) or []:
            spot_id_for(str(a["name"]).strip(), str(s).strip())

    for p in data["plants"]:
        try:
            number = int(p["number"])
        except (KeyError, TypeError, ValueError):
            raise UserError("Every plant needs a number.")
        fields = {f: p[f] for f in PLANT_TEXT_FIELDS if f in p}
        if "soil_threshold" in p:
            fields["soil_threshold"] = p["soil_threshold"]
        if p.get("area") and p.get("spot"):
            fields["spot_id"] = spot_id_for(str(p["area"]).strip(), str(p["spot"]).strip())
        exists = con.execute("SELECT 1 FROM plants WHERE number=?", (number,)).fetchone()
        if exists:
            update_plant(con, number, fields)
            updated += 1
        else:
            if not str(fields.get("name", "")).strip():
                raise UserError(f"Plant {number} needs a name.")
            create_plant(con, {**fields, "number": number})
            for entry in p.get("location_log", []) or []:
                con.execute(
                    "INSERT INTO location_log (plant_number, at, from_text, to_text) VALUES (?,?,?,?)",
                    (number, entry["at"], entry["from_text"], entry["to_text"]),
                )
            for entry in p.get("care_log", []) or []:
                add_care(con, number, entry["kind"], bool(entry.get("fed")), entry.get("note", ""),
                         entry.get("source", "import"), entry["at"], queue_soil=False)
            for entry in p.get("soil_readings", []) or []:
                con.execute(
                    "INSERT INTO soil_readings (plant_number, kind, entity, due_at, status, value, read_at) "
                    "VALUES (?,?,?,?,'done',?,?)",
                    (number, entry["kind"], entry["entity"], entry["due_at"], entry["value"], entry.get("read_at")),
                )
            for entry in p.get("diagnoses", []) or []:
                add_diagnosis(con, number, entry.get("finding"), entry.get("actions"), None,
                              entry.get("at"), entry.get("source", "import"), seen=True)
            added += 1
    return {"added": added, "updated": updated}
