# Changelog

## 0.3.0

- Watering log: **Watered** (optionally **with feed**) and **Checked, still moist** on each plant page, with a history you can delete entries from.
- **Water round** (button on the home screen): mark each plant Watered / Moist, with a feed tick, and save them all in one go.
- Tiles show when each plant was last watered.
- Home Assistant: per plant **Last watered**, **Last fed** and **Next check** sensors, and a **Watered** button (`button.plant_7_watered`) that logs a watering from HA.
- Export includes the watering log.

## 0.2.2

- One **Add photo** button instead of Take photo / From gallery: the Companion app always opens the file picker, so photos are taken with the camera app first and picked here.

## 0.2.1

- Fix: after an update, phones could keep using the old app script (the photo screen didn't open). Files are now re-checked on every load and the script link carries the version.

## 0.2.0

- Photos: take a photo or pick one from the gallery (plant page, or the camera icon on the home screen), with an optional note.
- Photo history on the plant page: swipe through every photo with its date and note; delete a wrong one.
- Tiles show each plant's latest photo and how long ago it was taken.
- Photos are stored at 1600 px with a 480 px thumbnail, turned the right way up, dated from the camera's own time stamp when there is one.
- Retention: every photo is kept for about 3 months, then one per plant per month. Runs at start-up and every night at 03:30.
- Home Assistant: one device per plant through MQTT (Mosquitto), with a camera entity showing the latest photo (`camera.plant_7`), plus Last photo and Location sensors.

## 0.1.3

- Fix: the time zone and data folder were read with a trailing line break, so times showed in UTC and data saved by 0.1.1–0.1.2 was kept outside Home Assistant's storage (lost on update). Data now always goes to /data.

## 0.1.2

- Health check shows storage and time zone details.

## 0.1.1

- Use Home Assistant's time zone (next check times were shown in UTC).

## 0.1.0

- First version (stage 1): plant tiles grouped by area and spot, plant pages, editing, location history, areas and spots management, import and export.
