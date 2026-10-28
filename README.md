# Plant app for Home Assistant

A small Home Assistant app that adds a **Plants** page to the sidebar: one tile per plant, grouped by area and spot, with a page per plant for its care details.

This repository holds **code only**. The plant list, photos and history live on the Home Assistant machine (in the app's own data folder) and are never committed here.

## Install

1. In Home Assistant: **Settings → Apps → Install app**.
2. Top-right **⋮ → Repositories**, paste `https://github.com/SpoonMP/ha-plant-app` and select **Add**.
3. Find **Plants** in the list, open it and select **Install**, then **Start**.
4. Turn on **Show in sidebar**.

## Status

| Stage | What | State |
|---|---|---|
| 1 | Skeleton: plant tiles grouped by area/spot, plant pages, settings, import/export | Done |
| 2 | Photos + Home Assistant camera entities | Done |
| 3 | Watering log, water round, Home Assistant sensors | Done |
| 4 | Photo sets, editable notes, diagnoses with action checklists, location changes from chat | Done |
| 5 | Soil sensors | Planned |
