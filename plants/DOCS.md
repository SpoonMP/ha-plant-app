# Plants

## Using it

- **Home:** tiles grouped by area, then spot. Tap a tile to open the plant.
- **Plant page:** location, watering rule, next check, grow light, pot and care notes, plus the plant's location history. **Edit details** changes any of these; changing the spot or position is logged as a move.
- **Photos:** take the photos with the phone's camera app, then on the plant page tap **Add photos** and pick one or several. Each photo can have its own note, and the **set note** covers all of them; **Save** uploads them as one set. (The Home Assistant phone app always opens the file picker, not the camera.) The camera icon at the top of the home screen does the same for any plant. Swipe the big photo to go through the photos, newest set first; the line under it shows which set the photo belongs to, the set note and the photo note — ✏️ edits either. **Delete** removes the photo showing.
- **Plant check:** a 🔍 tile means photos were added since that plant's last diagnosis. Say **"plant check"** to Claude in the Home Assistant project: Claude looks at the waiting photos and writes a diagnosis back. The plant page shows open actions under **To do** (tick them when done) and every diagnosis with the photos it used.
- **Watering:** on a plant page, **Watered** logs a watering (tick **with feed** if you fed it); **Checked, still moist** logs a check where it didn't need water. Delete a wrong entry from the history with ✕.
- **Water round:** the button on the home screen lists every plant by area. Tap **Watered** or **Moist** for each one you looked at (tap again to clear), tick **feed** where you fed, then **Save**.
- **Settings (gear icon):** add, edit or remove plants; add, rename, reorder or remove areas and spots; import or export the plant list.

## Plant numbers

Every plant keeps its number for life. A removed plant's number is never given to a new plant.

## Import and export

**Export all** downloads every plant, area, spot, location history entry, watering log entry and diagnosis as a JSON file (photos aren't included — they're in the Home Assistant backups). **Import** takes the same format: plants are matched by number (existing ones are updated, new ones added), and areas and spots are added if missing. Import never deletes anything.

Minimal format:

```json
{
  "areas": [{ "name": "Entrance", "spots": ["Shelf – upper", "Shelf – lower"] }],
  "plants": [
    { "number": 1, "name": "Example plant", "species": "", "area": "Entrance",
      "spot": "Shelf – upper", "position": "left", "watering_rule": "Top ~5 cm dry" }
  ]
}
```

Other optional plant fields: `pot`, `grow_light`, `care_notes`, `soil_sensor`.

## Photos

Photos are stored at 1600 px (plus a small thumbnail). Every photo is kept for about 3 months; after that, one photo per plant per month is kept and the rest are removed (every night at 03:30). HEIC files aren't supported — use JPEG or PNG.

## Home Assistant entities

With the Mosquitto broker app installed, each plant appears in Home Assistant as a device ("Plant 7 – Parlour palm") with:

- `camera.plant_7` — the latest photo (~1024 px); photo date, note and plant details as attributes
- `sensor.plant_7_last_photo` — when the latest photo was taken
- `sensor.plant_7_location` — area › spot · position
- `sensor.plant_7_last_watered`, `sensor.plant_7_last_fed` — from the watering log
- `sensor.plant_7_next_check` — the next Water check (Wed 19:00) or Plant check (Sun 15:00)
- `button.plant_7_watered` — logs a watering, e.g. from a dashboard
- `sensor.plant_7_last_diagnosis` — when Claude last diagnosed the plant; the finding, open actions and whether new photos are waiting are attributes

Plus one **Plants app** device with `camera.plants_review`: the photo Claude asked to look at during a plant check, at full size (1600 px).

Entity IDs are set when a plant first appears; renaming the plant later changes the device name, not the IDs.

## Data

The plant list and photos live in the app's own data folder (`/data/plants.db`, `/data/photos/`), which Home Assistant includes in its backups. Nothing is sent anywhere else.

## API for plant checks

Used by Claude through Home Assistant's app proxy (ingress); not reachable from anywhere else.

- `GET api/check` — every plant waiting for a check (or `?numbers=7,20`): plant details, watering log, previous diagnoses and the waiting photos (ids, dates, notes).
- `POST api/review` `{"photo_id": 12}` — shows that photo in `camera.plants_review`.
- `POST api/plants/7/diagnoses` `{"finding": "…", "actions": ["…"], "photo_ids": [12, 13]}` — saves a diagnosis (optional `at` and `source` for older ones).
- `PUT api/diagnoses/3` `{"finding": "…"}` or `{"add_actions": ["…"]}`; `PUT api/actions/5` `{"done": true}`.
- `PUT api/plants/7` `{"area": "Bedroom", "spot": "Floor"}` — moves a plant (logged in its location history).
