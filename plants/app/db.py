"""SQLite storage for the Plants app.

Everything lives in one file in the app's data folder (/data on Home Assistant),
which Home Assistant includes in its backups.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DATA_DIR = os.environ.get("PLANTS_DATA_DIR", "/data").strip()
DB_PATH = os.path.join(DATA_DIR, "plants.db")

SCHEMA_VERSION = 3

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
    return {"areas": areas, "unplaced": unplaced, "count": len(plants), "next_number": next_number(con)}


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
    d["care_log"] = list_care(con, number)
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
    if "spot_id" in data:
        v = data["spot_id"]
        out["spot_id"] = int(v) if v not in (None, "", "null") else None
    return out


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


def delete_plant(con, number: int) -> None:
    cur = con.execute("DELETE FROM plants WHERE number=?", (number,))
    if cur.rowcount == 0:
        raise KeyError(number)


# ---------- photos ----------

PHOTO_FIELDS = "id, plant_number, taken_at, uploaded_at, note, width, height, bytes"


def list_photos(con, number: int) -> list:
    """All photos of a plant, newest first."""
    return [dict(r) for r in con.execute(
        f"SELECT {PHOTO_FIELDS} FROM photos WHERE plant_number=? ORDER BY taken_at DESC, id DESC",
        (number,),
    )]


def latest_photo(con, number: int):
    row = con.execute(
        f"SELECT {PHOTO_FIELDS} FROM photos WHERE plant_number=? ORDER BY taken_at DESC, id DESC LIMIT 1",
        (number,),
    ).fetchone()
    return dict(row) if row else None


def get_photo(con, photo_id: int) -> dict:
    row = con.execute(f"SELECT {PHOTO_FIELDS} FROM photos WHERE id=?", (photo_id,)).fetchone()
    if not row:
        raise KeyError(photo_id)
    return dict(row)


def add_photo(con, number: int, taken_at: str, note: str, width: int, height: int, size: int) -> int:
    if not con.execute("SELECT 1 FROM plants WHERE number=?", (number,)).fetchone():
        raise KeyError(number)
    return con.execute(
        "INSERT INTO photos (plant_number, taken_at, uploaded_at, note, width, height, bytes) "
        "VALUES (?,?,?,?,?,?,?)",
        (number, taken_at, now_iso(), (note or "").strip(), width, height, size),
    ).lastrowid


def delete_photo(con, photo_id: int) -> dict:
    p = get_photo(con, photo_id)
    con.execute("DELETE FROM photos WHERE id=?", (photo_id,))
    return p


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
             source: str = "app", at: str | None = None) -> int:
    if kind not in CARE_KINDS:
        raise UserError("Unknown entry — use watered or checked.")
    if not con.execute("SELECT 1 FROM plants WHERE number=?", (number,)).fetchone():
        raise KeyError(number)
    if fed and kind != "watered":
        raise UserError("Feed is only logged together with watering.")
    return con.execute(
        "INSERT INTO care_log (plant_number, at, kind, fed, note, source) VALUES (?,?,?,?,?,?)",
        (number, at or now_iso(), kind, 1 if fed else 0, (note or "").strip()[:500], source),
    ).lastrowid


def delete_care(con, care_id: int) -> int:
    row = con.execute("SELECT plant_number FROM care_log WHERE id=?", (care_id,)).fetchone()
    if not row:
        raise KeyError(care_id)
    con.execute("DELETE FROM care_log WHERE id=?", (care_id,))
    return row[0]


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
                         entry.get("source", "import"), entry["at"])
            added += 1
    return {"added": added, "updated": updated}
