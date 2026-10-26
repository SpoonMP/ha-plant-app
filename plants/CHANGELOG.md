# Changelog

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
