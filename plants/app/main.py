"""Plants app — web server.

Served inside Home Assistant through ingress: Home Assistant handles login and
2FA, then forwards requests from the Supervisor (172.30.32.2) to this server.
All page and API paths are relative so they work under the ingress URL.
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager


def _load_container_env() -> None:
    """Pick up the variables Home Assistant gives the app (TZ, SUPERVISOR_TOKEN).

    The base image's init (S6 overlay) starts the program with a cleaned
    environment and keeps the container's variables as files in this folder.
    """
    env_dir = "/run/s6/container_environment"
    if os.path.isdir(env_dir):
        for name in os.listdir(env_dir):
            if name not in os.environ:
                try:
                    with open(os.path.join(env_dir, name), encoding="utf-8") as f:
                        os.environ[name] = f.read().rstrip("\r\n")
                except OSError:
                    pass
    if os.environ.get("TZ"):
        time.tzset()


_load_container_env()
from datetime import datetime, timedelta

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import db, ha, photos
from .publisher import Publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("plants")

HERE = os.path.dirname(__file__)
STATIC = os.path.join(HERE, "static")
VERSION = "0.5.0"

mqtt = Publisher(VERSION, lambda: next_check()["at"])


def _watered_from_ha(number: int) -> None:
    """The plant's Watered button was pressed in Home Assistant (runs on the MQTT thread)."""
    with db.session() as con:
        db.add_care(con, number, "watered", source="ha")
    mqtt.publish_plant(number, image=False)
    try:
        resolve_soil_readings()
    except Exception:
        log.exception("Soil readings failed")


mqtt.on_watered = _watered_from_ha

# Only the Supervisor's ingress proxy may talk to the app (plus localhost for testing).
ALLOWED_CLIENTS = {"172.30.32.2", "127.0.0.1", "::1"}

# Fixed check times from the existing Home Assistant reminders:
# Water check Wednesday 19:00, Plant check Sunday 15:00 (weekday: Mon=0).
CHECK_TIMES = [(2, 19, 0, "Water check"), (6, 15, 0, "Plant check")]

# Photo retention runs at this time every night.
RETENTION_AT = (3, 30)

IMAGE_CACHE = {"Cache-Control": "private, max-age=31536000, immutable"}


def next_check(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    best = None
    for weekday, hour, minute, label in CHECK_TIMES:
        days = (weekday - now.weekday()) % 7
        when = (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=7)
        if best is None or when < best[0]:
            best = (when, label)
    return {"at": best[0].isoformat(timespec="minutes"), "label": best[1]}


class SourceGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if not client or client[0] not in ALLOWED_CLIENTS:
                if scope["type"] == "http":
                    await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def ok(data=None, status=200):
    return JSONResponse(data if data is not None else {"ok": True}, status_code=status)


def error(message: str, status=400):
    return JSONResponse({"error": message}, status_code=status)


async def body(request: Request) -> dict:
    try:
        return await request.json()
    except (json.JSONDecodeError, ValueError):
        raise db.UserError("Couldn't read the request.")


def handle(fn):
    """Wrap an endpoint so user errors and missing items become JSON errors."""
    async def wrapper(request: Request):
        try:
            return await fn(request)
        except db.UserError as e:
            return error(str(e))
        except KeyError:
            return error("Not found.", 404)
    return wrapper


async def publish(number: int, image: bool = True) -> None:
    """Update a plant in Home Assistant; never fails the request if MQTT is down."""
    try:
        await run_in_threadpool(mqtt.publish_plant, number, image)
    except Exception:
        log.exception("Couldn't publish plant %s", number)


async def publish_all(image: bool = False) -> None:
    try:
        await run_in_threadpool(mqtt.publish_all, image)
    except Exception:
        log.exception("Couldn't publish the plants")


# ---------- pages ----------

async def index(request: Request):
    # The version in the script/style links makes phones fetch fresh files after an update.
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read().replace("__VERSION__", VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


class RevalidatingStatic(StaticFiles):
    """Static files that the browser must re-check before reusing (cheap: 304 if unchanged)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# ---------- API: plants ----------

@handle
async def api_overview(request):
    with db.session() as con:
        data = db.overview(con)
    plants = [p for a in data["areas"] for s in a["spots"] for p in s["plants"]] + data["unplaced"]
    await run_in_threadpool(_attach_soil, plants)
    data["next_check"] = next_check()
    data["version"] = VERSION
    return ok(data)


@handle
async def api_plant(request):
    number = int(request.path_params["number"])
    if request.method == "GET":
        with db.session() as con:
            p = db.get_plant(con, number)
        p["next_check"] = next_check()
        return ok(p)
    if request.method == "PUT":
        data = await body(request)
        with db.session() as con:
            db.update_plant(con, number, data)
            p = db.get_plant(con, number)
        await publish(number, image=False)
        return ok(p)
    if request.method == "DELETE":
        with db.session() as con:
            db.delete_plant(con, number)
        photos.remove_plant_files(number)
        await run_in_threadpool(mqtt.remove_plant, number)
        return ok()
    raise HTTPException(405)


@handle
async def api_plants_create(request):
    data = await body(request)
    with db.session() as con:
        number = db.create_plant(con, data)
        p = db.get_plant(con, number)
    await publish(number)
    return ok(p, 201)


# ---------- soil sensors (read from Home Assistant) ----------

def _attach_soil(plants: list) -> None:
    """Add the current soil reading + status to each plant that has a sensor (cached ~1 min)."""
    for p in plants:
        if not p.get("soil_sensor"):
            p["soil"] = None
            continue
        st = ha.state(p["soil_sensor"])
        value = st["value"] if st else None
        p["soil"] = {
            "value": value,
            "unit": (st or {}).get("unit") or "%",
            "at": (st or {}).get("at"),
            "status": db.soil_status(value, p.get("soil_threshold")),
        }


def _value_at(points: list, when: datetime):
    """Last value at or before `when` from [(iso, value)] oldest first."""
    best = None
    for at, v in points:
        if datetime.fromisoformat(at) <= when:
            best = (at, v)
        else:
            break
    return best


def resolve_soil_readings() -> int:
    """Fill in calibration readings whose time has come, from HA's history.
    before = lowest value in the 6 h up to the Watered log (the pre-watering dry point,
    even if the log was saved a while after watering); wet / moist = value at that time."""
    with db.session() as con:
        due = db.due_soil_readings(con)
    done = 0
    for r in due:
        due_at = datetime.fromisoformat(r["due_at"])
        lookback = timedelta(hours=6 if r["kind"] == "before" else 3)
        hist = ha.history(r["entity"], due_at - lookback, due_at + timedelta(minutes=1))
        if hist is None:
            continue  # HA not reachable — try again next time
        pts = [(at, v) for at, v in hist if datetime.fromisoformat(at) <= due_at]
        if r["kind"] == "before" and pts:
            at, v = min(pts, key=lambda x: x[1])
            found = (at, v)
        else:
            found = _value_at(pts, due_at)
        with db.session() as con:
            if found:
                db.set_soil_reading(con, r["id"], "done", found[1], db.norm_time(found[0]))
                done += 1
            elif datetime.now().astimezone() - due_at > timedelta(hours=6):
                db.set_soil_reading(con, r["id"], "missing")
    return done


async def resolve_soil_soon() -> None:
    try:
        await run_in_threadpool(resolve_soil_readings)
    except Exception:
        log.exception("Soil readings failed")


def _downsample(points: list, limit: int = 400) -> list:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    out = [points[int(i * step)] for i in range(limit)]
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


@handle
async def api_soil(request):
    """Soil card for one plant: current reading, status, 24 h change, 7-day history,
    waterings in that time, threshold and calibration readings."""
    number = int(request.path_params["number"])
    days = min(10, max(1, int(request.query_params.get("days", "7"))))
    with db.session() as con:
        p = db.get_plant(con, number)
        calibration = db.soil_calibration(con, number)
        readings = db.list_soil_readings(con, number)
    entity = p["soil_sensor"]
    if not entity:
        return ok({"entity": None})
    now = datetime.now().astimezone()
    start = now - timedelta(days=days)
    cur, hist = await asyncio.gather(
        run_in_threadpool(ha.state, entity, True),
        run_in_threadpool(ha.history, entity, start),
    )
    value = cur["value"] if cur else None
    change_24h = None
    if hist and value is not None:
        old = _value_at(hist, now - timedelta(hours=24))
        if old:
            change_24h = round(value - old[1], 1)
    waterings = [c for c in p["care_log"] if datetime.fromisoformat(c["at"]) >= start]
    return ok({
        "entity": entity,
        "ha": ha.available() and cur is not None,
        "value": value,
        "state": cur["state"] if cur else None,
        "unit": (cur or {}).get("unit") or "%",
        "at": (cur or {}).get("at"),
        "status": db.soil_status(value, p["soil_threshold"]),
        "threshold": p["soil_threshold"],
        "change_24h": change_24h,
        "days": days,
        "start": start.isoformat(timespec="seconds"),
        "history": [[db.norm_time(at), v] for at, v in _downsample(hist or [])],
        "care": [{"at": c["at"], "kind": c["kind"]} for c in waterings],
        "calibration": calibration,
        "readings": readings,
    })


# ---------- API: watering ----------

def _care_entry(data: dict) -> tuple[str, bool, str]:
    kind = str(data.get("kind") or "watered")
    fed = bool(data.get("fed"))
    note = str(data.get("note") or "")
    return kind, fed, note


@handle
async def api_care_add(request):
    number = int(request.path_params["number"])
    kind, fed, note = _care_entry(await body(request))
    with db.session() as con:
        care_id = db.add_care(con, number, kind, fed, note)
        p = db.get_plant(con, number)
    await publish(number, image=False)
    asyncio.create_task(resolve_soil_soon())
    return ok({"id": care_id, "plant": p}, 201)


@handle
async def api_care_delete(request):
    care_id = int(request.path_params["id"])
    with db.session() as con:
        number = db.delete_care(con, care_id)
    await publish(number, image=False)
    return ok()


@handle
async def api_round(request):
    """Water round: several plants logged in one go, all with the same time."""
    data = await body(request)
    entries = data.get("entries") or []
    if not entries:
        raise db.UserError("Nothing ticked yet.")
    at = db.now_iso()
    numbers = []
    with db.session() as con:
        for e in entries:
            number = int(e.get("number"))
            kind, fed, note = _care_entry(e)
            db.add_care(con, number, kind, fed, note, source="round", at=at)
            numbers.append(number)
    for n in numbers:
        await publish(n, image=False)
    asyncio.create_task(resolve_soil_soon())
    watered = sum(1 for e in entries if (e.get("kind") or "watered") == "watered")
    return ok({"watered": watered, "checked": len(entries) - watered}, 201)


# ---------- API: photos ----------

@handle
async def api_photo_upload(request):
    """One photo per request. The first photo of an upload creates the set (with the set
    note); the phone sends the others with that set_id, so each one shows progress and
    a failure doesn't lose the ones already saved."""
    number = int(request.path_params["number"])
    with db.session() as con:
        db.get_plant(con, number)  # 404 if the plant doesn't exist
    form = await request.form(max_files=1)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise db.UserError("No photo was sent.")
    raw = await upload.read()
    note = str(form.get("note") or "")[:500]
    set_id = form.get("set_id")
    hint = form.get("taken_hint")
    # Decoding and resizing takes a second or two on the Pi: keep it off the event loop.
    processed = await run_in_threadpool(photos.process_upload, raw, hint)
    with db.session() as con:
        if set_id not in (None, ""):
            set_id = int(set_id)
            new_set = False
        else:
            set_id = db.create_set(con, number, str(form.get("set_note") or ""))
            new_set = True
        photo_id = db.add_photo(con, number, set_id, processed["taken_at"], note, 0, 0, 0)
        try:
            w, h, size = await run_in_threadpool(photos.store, number, photo_id, processed["image"])
        except Exception:
            photos.remove_files(number, photo_id)
            raise
        con.execute("UPDATE photos SET width=?, height=?, bytes=? WHERE id=?", (w, h, size, photo_id))
        photo = db.get_photo(con, photo_id)
    # The camera entity shows the newest set's first photo: only re-send it for a new set.
    await publish(number, image=new_set)
    return ok(photo, 201)


@handle
async def api_photo(request):
    photo_id = int(request.path_params["id"])
    if request.method == "DELETE":
        with db.session() as con:
            p = db.delete_photo(con, photo_id)
        photos.remove_files(p["plant_number"], photo_id)
        await publish(p["plant_number"])
        return ok()
    if request.method == "PUT":
        data = await body(request)
        with db.session() as con:
            p = db.update_photo_note(con, photo_id, str(data.get("note") or ""))
        await publish(p["plant_number"], image=False)
        return ok()
    with db.session() as con:
        return ok(db.get_photo(con, photo_id))


@handle
async def api_set(request):
    set_id = int(request.path_params["id"])
    if request.method == "PUT":
        data = await body(request)
        with db.session() as con:
            s = db.update_set_note(con, set_id, str(data.get("note") or ""))
        await publish(s["plant_number"], image=False)
        return ok()
    with db.session() as con:
        return ok(db.get_set(con, set_id))


# ---------- API: diagnoses ----------

@handle
async def api_diagnosis_add(request):
    """Written by Claude after a plant check (through ha-mcp's app proxy).
    Body: {finding, actions: [text | {text, done}], photo_ids: [...], at?, source?}"""
    number = int(request.path_params["number"])
    data = await body(request)
    with db.session() as con:
        did = db.add_diagnosis(
            con, number, data.get("finding"), data.get("actions"), data.get("photo_ids"),
            data.get("at"), data.get("source") or "claude", bool(data.get("seen")),
        )
        d = db.get_diagnosis(con, did)
    await publish(number, image=False)
    return ok(d, 201)


@handle
async def api_diagnosis(request):
    did = int(request.path_params["id"])
    if request.method == "PUT":
        data = await body(request)
        with db.session() as con:
            number = db.update_diagnosis(con, did, data)
            d = db.get_diagnosis(con, did)
        await publish(number, image=False)
        return ok(d)
    if request.method == "DELETE":
        with db.session() as con:
            number = db.delete_diagnosis(con, did)
        await publish(number, image=False)
        return ok()
    with db.session() as con:
        return ok(db.get_diagnosis(con, did))


@handle
async def api_action(request):
    action_id = int(request.path_params["id"])
    if request.method == "DELETE":
        with db.session() as con:
            number = db.delete_action(con, action_id)
    else:
        data = await body(request)
        with db.session() as con:
            number = db.update_action(con, action_id, data)
    await publish(number, image=False)
    return ok()


@handle
async def api_seen(request):
    number = int(request.path_params["number"])
    with db.session() as con:
        changed = db.mark_seen(con, number)
    if changed:
        await publish(number, image=False)
    return ok()


@handle
async def api_check(request):
    """Plant check bundle: ?numbers=7,20 for those plants, otherwise every plant waiting
    (photos added since its last diagnosis)."""
    raw = request.query_params.get("numbers", "").strip()
    with db.session() as con:
        if raw:
            numbers = [int(x) for x in raw.replace(" ", "").split(",") if x]
        else:
            numbers = db.overview(con)["waiting"]
        plants = [db.check_bundle(con, n) for n in numbers]
    return ok({"waiting": numbers if not raw else None, "plants": plants, "now": db.now_iso()})


@handle
async def api_review(request):
    """Show one stored photo (1600 px) in HA's review camera, so Claude can look at it."""
    data = await body(request)
    with db.session() as con:
        ph = db.get_photo(con, int(data.get("photo_id")))
        name = db.get_plant(con, ph["plant_number"])["name"]
    if not mqtt.connected:
        raise db.UserError("MQTT isn't connected, so the review camera can't be updated.")
    await run_in_threadpool(mqtt.publish_review, ph, name)
    return ok({"photo_id": ph["id"], "plant_number": ph["plant_number"], "camera": "camera.plants_review"})


@handle
async def api_photo_file(request):
    photo_id = int(request.path_params["id"])
    size = request.path_params["size"]
    with db.session() as con:
        p = db.get_photo(con, photo_id)
    n = p["plant_number"]
    if size == "full":
        path = photos.full_path(n, photo_id)
    elif size == "thumb":
        path = photos.thumb_path(n, photo_id)
    elif size == "camera":
        data = await run_in_threadpool(photos.camera_jpeg, n, photo_id)
        return Response(data, media_type="image/jpeg", headers=IMAGE_CACHE)
    else:
        raise KeyError(size)
    if not os.path.exists(path):
        raise KeyError(photo_id)
    return FileResponse(path, media_type="image/jpeg", headers=IMAGE_CACHE)


# ---------- API: areas & spots ----------

@handle
async def api_locations(request):
    with db.session() as con:
        return ok({"areas": db.list_locations(con)})


@handle
async def api_areas_create(request):
    data = await body(request)
    with db.session() as con:
        return ok({"id": db.create_area(con, data.get("name", ""))}, 201)


@handle
async def api_area(request):
    area_id = int(request.path_params["id"])
    if request.method == "PUT":
        data = await body(request)
        with db.session() as con:
            if "move" in data:
                db.move(con, "areas", area_id, int(data["move"]))
            if "name" in data:
                db.rename_area(con, area_id, data["name"])
        if "name" in data:
            await publish_all()  # location text changes
        return ok()
    if request.method == "DELETE":
        with db.session() as con:
            db.delete_area(con, area_id)
        return ok()
    raise HTTPException(405)


@handle
async def api_spots_create(request):
    data = await body(request)
    with db.session() as con:
        return ok({"id": db.create_spot(con, int(data.get("area_id", 0)), data.get("name", ""))}, 201)


@handle
async def api_spot(request):
    spot_id = int(request.path_params["id"])
    if request.method == "PUT":
        data = await body(request)
        with db.session() as con:
            if "move" in data:
                db.move(con, "spots", spot_id, int(data["move"]))
            if "name" in data:
                db.rename_spot(con, spot_id, data["name"])
        if "name" in data:
            await publish_all()
        return ok()
    if request.method == "DELETE":
        with db.session() as con:
            db.delete_spot(con, spot_id)
        return ok()
    raise HTTPException(405)


# ---------- API: import / export / health ----------

@handle
async def api_export(request):
    with db.session() as con:
        data = db.export_all(con)
    stamp = datetime.now().strftime("%Y-%m-%d")
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="plants-export-{stamp}.json"'},
    )


@handle
async def api_import(request):
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        raw = await upload.read() if upload is not None and hasattr(upload, "read") else b""
        text = raw.decode("utf-8-sig") if raw else str(form.get("text") or "")
    else:
        text = (await request.body()).decode("utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise db.UserError(f"That isn't valid JSON (line {e.lineno}).")
    with db.session() as con:
        result = db.import_all(con, data)
    await publish_all(image=True)
    return ok(result)


@handle
async def api_publish(request):
    """Re-send every plant to Home Assistant (photos included)."""
    await publish_all(image=True)
    return ok({"mqtt": mqtt.status()})


async def api_health(request):
    with db.session() as con:
        stats = db.photo_stats(con)
    return ok({
        "ok": True,
        "version": VERSION,
        "db_path": db.DB_PATH,
        "db_exists": os.path.exists(db.DB_PATH),
        "tz": os.environ.get("TZ"),
        "local_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "photos": stats,
        "mqtt": mqtt.status(),
        "ha_api": ha.available(),
    })


# ---------- background ----------

async def retention_loop():
    """Thin out old photos once at start-up, then every night."""
    while True:
        try:
            removed = await run_in_threadpool(photos.apply_retention)
            if removed:
                log.info("Photo retention removed %s old photos", removed)
        except Exception:
            log.exception("Photo retention failed")
        now = datetime.now()
        nxt = now.replace(hour=RETENTION_AT[0], minute=RETENTION_AT[1], second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())


async def next_check_loop():
    """When a check time passes, re-send every plant so HA's Next check moves on."""
    while True:
        when = datetime.fromisoformat(next_check()["at"])
        await asyncio.sleep(max(1.0, (when - datetime.now()).total_seconds() + 60))
        await publish_all(image=False)


async def soil_loop():
    """Every 5 minutes, fill in calibration readings that have come due."""
    while True:
        await resolve_soil_soon()
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    os.makedirs(photos.PHOTO_DIR, exist_ok=True)
    mqtt.start()
    tasks = [asyncio.create_task(retention_loop()), asyncio.create_task(next_check_loop()),
             asyncio.create_task(soil_loop())]
    yield
    for t in tasks:
        t.cancel()
    mqtt.stop()


routes = [
    Route("/", index),
    Route("/api/health", api_health),
    Route("/api/overview", api_overview),
    Route("/api/publish", api_publish, methods=["POST"]),
    Route("/api/plants", api_plants_create, methods=["POST"]),
    Route("/api/plants/{number:int}", api_plant, methods=["GET", "PUT", "DELETE"]),
    Route("/api/plants/{number:int}/photos", api_photo_upload, methods=["POST"]),
    Route("/api/plants/{number:int}/care", api_care_add, methods=["POST"]),
    Route("/api/plants/{number:int}/soil", api_soil),
    Route("/api/care/{id:int}", api_care_delete, methods=["DELETE"]),
    Route("/api/round", api_round, methods=["POST"]),
    Route("/api/photos/{id:int}", api_photo, methods=["GET", "PUT", "DELETE"]),
    Route("/api/photos/{id:int}/{size:str}", api_photo_file),
    Route("/api/sets/{id:int}", api_set, methods=["GET", "PUT"]),
    Route("/api/plants/{number:int}/diagnoses", api_diagnosis_add, methods=["POST"]),
    Route("/api/plants/{number:int}/seen", api_seen, methods=["POST"]),
    Route("/api/diagnoses/{id:int}", api_diagnosis, methods=["GET", "PUT", "DELETE"]),
    Route("/api/actions/{id:int}", api_action, methods=["PUT", "DELETE"]),
    Route("/api/check", api_check),
    Route("/api/review", api_review, methods=["POST"]),
    Route("/api/locations", api_locations),
    Route("/api/areas", api_areas_create, methods=["POST"]),
    Route("/api/areas/{id:int}", api_area, methods=["PUT", "DELETE"]),
    Route("/api/spots", api_spots_create, methods=["POST"]),
    Route("/api/spots/{id:int}", api_spot, methods=["PUT", "DELETE"]),
    Route("/api/export", api_export),
    Route("/api/import", api_import, methods=["POST"]),
    Mount("/static", app=RevalidatingStatic(directory=STATIC), name="static"),
]

app = SourceGuard(Starlette(routes=routes, lifespan=lifespan))
