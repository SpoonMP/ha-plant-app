"""Plants app — web server.

Served inside Home Assistant through ingress: Home Assistant handles login and
2FA, then forwards requests from the Supervisor (172.30.32.2) to this server.
All page and API paths are relative so they work under the ingress URL.
"""

import json
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
                        os.environ[name] = f.read()
                except OSError:
                    pass
    if os.environ.get("TZ"):
        time.tzset()


_load_container_env()
from datetime import datetime, timedelta

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import db

HERE = os.path.dirname(__file__)
STATIC = os.path.join(HERE, "static")
VERSION = "0.1.2"

# Only the Supervisor's ingress proxy may talk to the app (plus localhost for testing).
ALLOWED_CLIENTS = {"172.30.32.2", "127.0.0.1", "::1"}

# Fixed check times from the existing Home Assistant reminders:
# Water check Wednesday 19:00, Plant check Sunday 15:00 (weekday: Mon=0).
CHECK_TIMES = [(2, 19, 0, "Water check"), (6, 15, 0, "Plant check")]


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


# ---------- pages ----------

async def index(request: Request):
    return FileResponse(
        os.path.join(STATIC, "index.html"),
        headers={"Cache-Control": "no-cache"},
    )


# ---------- API ----------

@handle
async def api_overview(request):
    with db.session() as con:
        data = db.overview(con)
    data["next_check"] = next_check()
    data["version"] = VERSION
    return ok(data)


@handle
async def api_plant(request):
    number = int(request.path_params["number"])
    with db.session() as con:
        if request.method == "GET":
            p = db.get_plant(con, number)
            p["next_check"] = next_check()
            return ok(p)
        if request.method == "PUT":
            db.update_plant(con, number, await body(request))
            return ok(db.get_plant(con, number))
        if request.method == "DELETE":
            db.delete_plant(con, number)
            return ok()
    raise HTTPException(405)


@handle
async def api_plants_create(request):
    with db.session() as con:
        number = db.create_plant(con, await body(request))
        return ok(db.get_plant(con, number), 201)


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
    with db.session() as con:
        if request.method == "PUT":
            data = await body(request)
            if "move" in data:
                db.move(con, "areas", area_id, int(data["move"]))
            if "name" in data:
                db.rename_area(con, area_id, data["name"])
            return ok()
        if request.method == "DELETE":
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
    with db.session() as con:
        if request.method == "PUT":
            data = await body(request)
            if "move" in data:
                db.move(con, "spots", spot_id, int(data["move"]))
            if "name" in data:
                db.rename_spot(con, spot_id, data["name"])
            return ok()
        if request.method == "DELETE":
            db.delete_spot(con, spot_id)
            return ok()
    raise HTTPException(405)


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
    return ok(result)


async def api_health(request):
    def ls(path):
        try:
            return sorted(os.listdir(path))
        except OSError as e:
            return f"error: {e.strerror}"
    return ok({
        "ok": True,
        "version": VERSION,
        "db_path": db.DB_PATH,
        "db_exists": os.path.exists(db.DB_PATH),
        "data_dir": ls(db.DATA_DIR),
        "tz_env": os.environ.get("TZ"),
        "local_time": datetime.now().isoformat(timespec="seconds"),
        "tzname": list(time.tzname),
        "env_names": sorted(k for k in os.environ if not k.startswith("_")),
        "s6_env_dir": ls("/run/s6/container_environment"),
        "mounts": [l.split()[1] for l in open("/proc/mounts") if l.split()[1].startswith(("/data", "/run/s6"))],
    })


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield


routes = [
    Route("/", index),
    Route("/api/health", api_health),
    Route("/api/overview", api_overview),
    Route("/api/plants", api_plants_create, methods=["POST"]),
    Route("/api/plants/{number:int}", api_plant, methods=["GET", "PUT", "DELETE"]),
    Route("/api/locations", api_locations),
    Route("/api/areas", api_areas_create, methods=["POST"]),
    Route("/api/areas/{id:int}", api_area, methods=["PUT", "DELETE"]),
    Route("/api/spots", api_spots_create, methods=["POST"]),
    Route("/api/spots/{id:int}", api_spot, methods=["PUT", "DELETE"]),
    Route("/api/export", api_export),
    Route("/api/import", api_import, methods=["POST"]),
    Mount("/static", app=StaticFiles(directory=STATIC), name="static"),
]

app = SourceGuard(Starlette(routes=routes, lifespan=lifespan))
