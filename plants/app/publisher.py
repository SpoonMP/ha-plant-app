"""Publishes each plant to Home Assistant through Mosquitto (MQTT discovery).

One HA device per plant ("Plant 7 – Parlour palm") with:
  camera.plant_7               latest photo (~1024 px), photo details as attributes
  sensor.plant_7_last_photo    when the latest photo was taken
  sensor.plant_7_location      area › spot · position
  sensor.plant_7_last_watered / _last_fed / _next_check
  button.plant_7_watered       logs a watering from HA
  sensor.plant_7_last_diagnosis  date of Claude's latest diagnosis (finding + actions as attributes)
Plus one "Plants app" device with camera.plants_review: whichever stored photo
Claude asked to look at (full 1600 px), for plant checks.
Everything is published retained, so HA gets it back after a restart.
"""

import json
import logging
import os
import re
import threading
import urllib.request
from datetime import datetime

import paho.mqtt.client as mqtt

from . import db, photos

log = logging.getLogger("plants.mqtt")

BASE = "plants_app"
STATUS_TOPIC = f"{BASE}/status"
REPO_URL = "https://github.com/SpoonMP/ha-plant-app"


def _topic(number: int, kind: str) -> str:
    return f"{BASE}/plant/{number}/{kind}"


def _config_topic(number: int) -> str:
    return f"homeassistant/device/{BASE}_plant_{number}/config"


REVIEW_CONFIG = f"homeassistant/device/{BASE}_review/config"
REVIEW_PHOTO = f"{BASE}/review/photo"
REVIEW_ATTRS = f"{BASE}/review/attributes"


def _broker_settings() -> dict | None:
    """Mosquitto's address and login, from the Supervisor (or env vars when testing)."""
    if os.environ.get("PLANTS_MQTT_HOST"):
        return {
            "host": os.environ["PLANTS_MQTT_HOST"],
            "port": int(os.environ.get("PLANTS_MQTT_PORT", "1883")),
            "username": os.environ.get("PLANTS_MQTT_USER"),
            "password": os.environ.get("PLANTS_MQTT_PASSWORD"),
        }
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        "http://supervisor/services/mqtt",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        data = json.load(res).get("data") or {}
    if not data.get("host"):
        return None
    return {
        "host": data["host"],
        "port": int(data.get("port", 1883)),
        "username": data.get("username"),
        "password": data.get("password"),
    }


class Publisher:
    def __init__(self, version: str, next_check_fn=None):
        self.version = version
        self.next_check_fn = next_check_fn
        self.on_watered = None  # set by the app: called with the plant number
        self.client = None
        self.state = "not started"
        self.last_error = None
        self._lock = threading.Lock()

    # ---------- connection ----------

    def start(self) -> None:
        try:
            settings = _broker_settings()
        except Exception as e:  # Supervisor not reachable or Mosquitto not installed
            self.state, self.last_error = "no broker", str(e)
            log.warning("MQTT not available: %s", e)
            return
        if not settings:
            self.state = "no broker"
            log.warning("MQTT not available: no Mosquitto service found")
            return
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="plants-app")
        if settings.get("username"):
            c.username_pw_set(settings["username"], settings.get("password"))
        c.will_set(STATUS_TOPIC, "offline", qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(min_delay=2, max_delay=60)
        self.client = c
        self.state = "connecting"
        c.connect_async(settings["host"], settings["port"], keepalive=60)
        c.loop_start()

    def stop(self) -> None:
        if self.client:
            self.client.publish(STATUS_TOPIC, "offline", qos=1, retain=True).wait_for_publish(2)
            self.client.disconnect()
            self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.state, self.last_error = "refused", str(reason_code)
            log.warning("MQTT connection refused: %s", reason_code)
            return
        self.state, self.last_error = "connected", None
        log.info("MQTT connected")
        client.publish(STATUS_TOPIC, "online", qos=1, retain=True)
        client.subscribe(f"{BASE}/plant/+/watered/set", qos=1)
        client.publish(REVIEW_CONFIG, json.dumps(self._review_discovery()), qos=1, retain=True)
        # Publishing all plants renders 20+ images, so keep it off the network thread.
        threading.Thread(target=self.publish_all, daemon=True).start()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.state = "disconnected"
        if reason_code and reason_code.is_failure:
            self.last_error = str(reason_code)

    def _on_message(self, client, userdata, msg):
        m = re.fullmatch(rf"{BASE}/plant/(\d+)/watered/set", msg.topic)
        if not m or msg.retain or not self.on_watered:
            return
        try:
            self.on_watered(int(m.group(1)))
            log.info("Plant %s watered (from Home Assistant)", m.group(1))
        except Exception:
            log.exception("Couldn't log the watering for plant %s", m.group(1))

    def _next_check(self):
        if not self.next_check_fn:
            return None
        return datetime.fromisoformat(self.next_check_fn()).astimezone().isoformat(timespec="seconds")

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.is_connected()

    def _pub(self, topic: str, payload, retain: bool = True) -> None:
        self.client.publish(topic, payload, qos=1, retain=retain)

    # ---------- plants ----------

    def publish_all(self, image: bool = True) -> None:
        if not self.connected:
            return
        with db.session() as con:
            numbers = db.plant_numbers(con)
        for n in numbers:
            try:
                self.publish_plant(n, image)
            except Exception:
                log.exception("Couldn't publish plant %s", n)

    def publish_plant(self, number: int, image: bool = True) -> None:
        """Send a plant's device config, state and (optionally) its latest photo."""
        if not self.connected:
            return
        with db.session() as con:
            try:
                p = db.get_plant(con, number)
            except KeyError:
                return self.remove_plant(number)
        latest = p["photos"][0] if p["photos"] else None
        with self._lock:
            self._pub(_config_topic(number), json.dumps(self._discovery(p)))
            self._pub(_topic(number, "state"), json.dumps({
                "last_photo": latest["taken_at"] if latest else None,
                "location": p["location"],
                "last_watered": p["last_watered"],
                "last_checked": p["last_checked"],
                "last_fed": p["last_fed"],
                "next_check": self._next_check(),
                "last_diagnosis": p["last_diagnosis"],
            }, ensure_ascii=False))
            last = p["diagnoses"][0] if p["diagnoses"] else None
            self._pub(_topic(number, "diagnosis"), json.dumps({
                "finding": last["finding"] if last else None,
                "actions": [a["text"] for a in last["actions"]] if last else [],
                "open_actions": [a["text"] for d in p["diagnoses"] for a in d["actions"] if not a["done_at"]],
                "waiting_for_check": p["waiting"],
                "new_photos": p["waiting_photos"],
                "unread": p["new_diagnosis"],
            }, ensure_ascii=False))
            self._pub(_topic(number, "attributes"), json.dumps({
                "plant_number": p["number"],
                "plant_name": p["name"],
                "species": p["species"],
                "location": p["location"],
                "photo_id": latest["id"] if latest else None,
                "taken_at": latest["taken_at"] if latest else None,
                "note": latest["note"] if latest else None,
                "photo_count": len(p["photos"]),
                "watering_rule": p["watering_rule"],
                "last_watered": p["last_watered"],
                "last_checked": p["last_checked"],
            }, ensure_ascii=False))
            if image:
                # An empty retained message clears the photo when the plant has none.
                payload = photos.camera_jpeg(number, latest["id"]) if latest else b""
                self._pub(_topic(number, "photo"), payload)

    def remove_plant(self, number: int) -> None:
        """Remove a plant's device and entities from HA."""
        if not self.connected:
            return
        with self._lock:
            for topic in (_config_topic(number), _topic(number, "state"),
                          _topic(number, "attributes"), _topic(number, "photo"),
                          _topic(number, "diagnosis")):
                self._pub(topic, b"")

    def publish_review(self, ph: dict, plant_name: str) -> None:
        """Put one stored photo (full size) into the review camera."""
        if not self.connected:
            return
        data = photos.full_jpeg(ph["plant_number"], ph["id"])
        with self._lock:
            self._pub(REVIEW_ATTRS, json.dumps({
                "photo_id": ph["id"],
                "plant_number": ph["plant_number"],
                "plant_name": plant_name,
                "set_id": ph["set_id"],
                "taken_at": ph["taken_at"],
                "note": ph["note"],
                "set_note": ph["set_note"],
            }, ensure_ascii=False))
            # QoS 1 and wait, so the image is in HA before the caller fetches it.
            self.client.publish(REVIEW_PHOTO, data, qos=1, retain=True).wait_for_publish(10)

    def _review_discovery(self) -> dict:
        uid = f"{BASE}_review"
        return {
            "device": {
                "identifiers": [uid],
                "name": "Plants app",
                "manufacturer": "Plants app",
                "model": "Plant check",
                "sw_version": self.version,
            },
            "origin": {"name": "Plants app", "sw_version": self.version, "support_url": REPO_URL},
            "components": {
                "review": {
                    "platform": "camera",
                    "name": "Review photo",
                    "unique_id": f"{uid}_photo",
                    "default_entity_id": "camera.plants_review",
                    "topic": REVIEW_PHOTO,
                    "json_attributes_topic": REVIEW_ATTRS,
                    "availability_topic": STATUS_TOPIC,
                },
            },
            "qos": 1,
        }

    def _discovery(self, p: dict) -> dict:
        n = p["number"]
        uid = f"{BASE}_plant_{n}"
        avail = {"availability_topic": STATUS_TOPIC}
        device = {
            "identifiers": [uid],
            "name": f"Plant {n} – {p['name']}",
            "manufacturer": "Plants app",
            "model": p["species"] or "Plant",
            "sw_version": self.version,
        }
        if p.get("area"):
            device["suggested_area"] = p["area"]
        return {
            "device": device,
            "origin": {"name": "Plants app", "sw_version": self.version, "support_url": REPO_URL},
            "components": {
                "photo": {
                    "platform": "camera",
                    "name": "Photo",
                    "unique_id": f"{uid}_photo",
                    "default_entity_id": f"camera.plant_{n}",
                    "topic": _topic(n, "photo"),
                    "json_attributes_topic": _topic(n, "attributes"),
                    **avail,
                },
                "last_photo": {
                    "platform": "sensor",
                    "name": "Last photo",
                    "unique_id": f"{uid}_last_photo",
                    "default_entity_id": f"sensor.plant_{n}_last_photo",
                    "device_class": "timestamp",
                    "icon": "mdi:camera-outline",
                    "state_topic": _topic(n, "state"),
                    "value_template": "{{ value_json.last_photo }}",
                    **avail,
                },
                "last_watered": {
                    "platform": "sensor",
                    "name": "Last watered",
                    "unique_id": f"{uid}_last_watered",
                    "default_entity_id": f"sensor.plant_{n}_last_watered",
                    "device_class": "timestamp",
                    "icon": "mdi:watering-can-outline",
                    "state_topic": _topic(n, "state"),
                    "value_template": "{{ value_json.last_watered }}",
                    **avail,
                },
                "last_fed": {
                    "platform": "sensor",
                    "name": "Last fed",
                    "unique_id": f"{uid}_last_fed",
                    "default_entity_id": f"sensor.plant_{n}_last_fed",
                    "device_class": "timestamp",
                    "icon": "mdi:flask-outline",
                    "state_topic": _topic(n, "state"),
                    "value_template": "{{ value_json.last_fed }}",
                    **avail,
                },
                "next_check": {
                    "platform": "sensor",
                    "name": "Next check",
                    "unique_id": f"{uid}_next_check",
                    "default_entity_id": f"sensor.plant_{n}_next_check",
                    "device_class": "timestamp",
                    "icon": "mdi:calendar-check-outline",
                    "state_topic": _topic(n, "state"),
                    "value_template": "{{ value_json.next_check }}",
                    **avail,
                },
                "watered": {
                    "platform": "button",
                    "name": "Watered",
                    "unique_id": f"{uid}_watered",
                    "default_entity_id": f"button.plant_{n}_watered",
                    "icon": "mdi:watering-can",
                    "command_topic": f"{BASE}/plant/{n}/watered/set",
                    "payload_press": "PRESS",
                    **avail,
                },
                "last_diagnosis": {
                    "platform": "sensor",
                    "name": "Last diagnosis",
                    "unique_id": f"{uid}_last_diagnosis",
                    "default_entity_id": f"sensor.plant_{n}_last_diagnosis",
                    "device_class": "timestamp",
                    "icon": "mdi:stethoscope",
                    "state_topic": _topic(n, "state"),
                    "value_template": "{{ value_json.last_diagnosis }}",
                    "json_attributes_topic": _topic(n, "diagnosis"),
                    **avail,
                },
                "location": {
                    "platform": "sensor",
                    "name": "Location",
                    "unique_id": f"{uid}_location",
                    "default_entity_id": f"sensor.plant_{n}_location",
                    "icon": "mdi:map-marker-outline",
                    "state_topic": _topic(n, "state"),
                    "value_template": "{{ value_json.location }}",
                    **avail,
                },
            },
            "qos": 1,
        }

    def status(self) -> dict:
        return {"state": self.state, "connected": self.connected, "error": self.last_error}
