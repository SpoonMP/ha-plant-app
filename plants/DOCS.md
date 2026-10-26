# Plants

## Using it

- **Home:** tiles grouped by area, then spot. Tap a tile to open the plant.
- **Plant page:** location, watering rule, next check, grow light, pot and care notes, plus the plant's location history. **Edit details** changes any of these; changing the spot or position is logged as a move.
- **Photos:** take the photo with the phone's camera app, then on the plant page tap **Add photo** and pick it; add a note if you like, then **Save photo**. (The Home Assistant phone app always opens the file picker, not the camera.) The camera icon at the top of the home screen does the same for any plant. Swipe the big photo to go back through older ones; **Delete** removes the one showing.
- **Settings (gear icon):** add, edit or remove plants; add, rename, reorder or remove areas and spots; import or export the plant list.

## Plant numbers

Every plant keeps its number for life. A removed plant's number is never given to a new plant.

## Import and export

**Export all** downloads every plant, area, spot and location history entry as a JSON file (photos aren't included — they're in the Home Assistant backups). **Import** takes the same format: plants are matched by number (existing ones are updated, new ones added), and areas and spots are added if missing. Import never deletes anything.

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

Entity IDs are set when a plant first appears; renaming the plant later changes the device name, not the IDs.

## Data

The plant list and photos live in the app's own data folder (`/data/plants.db`, `/data/photos/`), which Home Assistant includes in its backups. Nothing is sent anywhere else.
