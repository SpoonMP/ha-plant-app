# Changelog

## 0.1.3

- Fix: the time zone and data folder were read with a trailing line break, so times showed in UTC and data saved by 0.1.1–0.1.2 was kept outside Home Assistant's storage (lost on update). Data now always goes to /data.

## 0.1.2

- Health check shows storage and time zone details.

## 0.1.1

- Use Home Assistant's time zone (next check times were shown in UTC).

## 0.1.0

- First version (stage 1): plant tiles grouped by area and spot, plant pages, editing, location history, areas and spots management, import and export.
