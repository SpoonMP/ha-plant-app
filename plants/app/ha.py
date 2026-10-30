"""Reads soil sensor values and their history from Home Assistant's REST API.

On Home Assistant the app talks to Core through the Supervisor
(http://supervisor/core/api, token = SUPERVISOR_TOKEN; needs
`homeassistant_api: true` in config.yaml). For local testing set
PLANTS_HA_URL (e.g. http://localhost:8123/api) and PLANTS_HA_TOKEN.

History comes from HA's recorder, which keeps ~10 days by default — enough for
the 7-day chart. Calls are short (3–8 s timeout) and never raise into a page:
a missing value is shown as "no reading".
"""

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger("plants.ha")

# Current values are cached briefly so the home screen doesn't ask HA on every load.
_STATE_TTL = 60
_state_cache: dict = {}


def _settings() -> tuple[str, str] | None:
    url = os.environ.get("PLANTS_HA_URL")
    if url:
        return url.rstrip("/"), os.environ.get("PLANTS_HA_TOKEN", "")
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        return "http://supervisor/core/api", token
    return None


def available() -> bool:
    return _settings() is not None


def _get(path: str, timeout: float):
    s = _settings()
    if not s:
        raise RuntimeError("Home Assistant API isn't available to the app.")
    base, token = s
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_number(state) -> float | None:
    try:
        v = float(state)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN → None


def state(entity_id: str, fresh: bool = False) -> dict | None:
    """{value, unit, at, state} for one entity, or None if HA can't be reached / unknown entity."""
    now = time.monotonic()
    hit = _state_cache.get(entity_id)
    if hit and not fresh and now - hit[0] < _STATE_TTL:
        return hit[1]
    try:
        raw = _get("/states/" + urllib.parse.quote(entity_id, safe="._"), timeout=3)
        out = {
            "state": raw.get("state"),
            "value": to_number(raw.get("state")),
            "unit": (raw.get("attributes") or {}).get("unit_of_measurement") or "",
            "at": raw.get("last_reported") or raw.get("last_updated"),
        }
    except urllib.error.HTTPError as e:
        out = {"state": "not found" if e.code == 404 else f"error {e.code}", "value": None, "unit": "", "at": None}
    except Exception as e:  # network, timeout, no token
        log.warning("Couldn't read %s from HA: %s", entity_id, e)
        return None
    _state_cache[entity_id] = (now, out)
    return out


def _utc(when: datetime) -> str:
    """UTC with a Z suffix: no '+' in the URL (it can turn into a space on the way through)."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def history(entity_id: str, start: datetime, end: datetime | None = None) -> list[tuple[str, float]] | None:
    """[(iso time, value)] oldest first, numeric states only. None if HA can't be reached."""
    params = {
        "filter_entity_id": entity_id,
        "minimal_response": "",
        "no_attributes": "",
    }
    if end:
        params["end_time"] = _utc(end)
    path = "/history/period/" + _utc(start) + "?" + urllib.parse.urlencode(params)
    try:
        raw = _get(path, timeout=8)
    except Exception as e:
        log.warning("Couldn't read history of %s from HA: %s", entity_id, e)
        return None
    rows = raw[0] if raw else []
    if not rows:
        log.info("History of %s is empty (%s)", entity_id, path)
    out = []
    for r in rows:
        v = to_number(r.get("state"))
        at = r.get("last_changed") or r.get("last_updated")
        if v is not None and at:
            out.append((at, v))
    return out
