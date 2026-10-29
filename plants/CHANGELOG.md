# Changelog

## 0.4.2

- Fix: "today" / "yesterday" now follow calendar days. Something logged at 20:00 showed as "today" until 20:00 the next day.
- Plants checked and still moist no longer show "not logged": tiles show a 👆 **moist** chip (when the check is newer than the last watering), and the Water round shows it next to the last watering.

## 0.4.1

- Fix: picking several photos at once did nothing in the Home Assistant phone app (its file picker returns no files for a multiple selection). Photos are now picked one at a time: after the first, **Add another photo** adds the next one to the same set.

## 0.4.0

- **Photo sets:** add several photos (top, underside, close-up…). They're saved together as one set with a shared **set note**, and each photo can have its own note. Photos upload one by one with progress; if one fails, the others are kept and **Save** retries the rest.
- **Edit notes after saving:** ✏️ next to the set note and the photo note under the big photo.
- **Diagnoses:** Claude writes each plant check's finding and actions to the plant (through the app's API). The plant page shows a **To do** list of open actions (tick them off) and the diagnosis history with the photos each one used.
- **Waiting for check:** tiles show 🔍 when photos were added since the plant's last diagnosis, and the home screen shows how many plants are waiting. 🩺 **New diagnosis** shows until you open the plant; ☐ shows open actions.
- Home Assistant: **Last diagnosis** sensor per plant (`sensor.plant_7_last_diagnosis`; finding and open actions as attributes), and a **Plants app** device with `camera.plants_review`, which shows whichever photo Claude is looking at in full size.
- Moves can be written by area and spot name (for changes from chat).
- Existing photos become a set of one each; their notes are kept.

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
