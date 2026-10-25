# Plants

## Using it

- **Home:** tiles grouped by area, then spot. Tap a tile to open the plant.
- **Plant page:** location, watering rule, next check, grow light, pot and care notes, plus the plant's location history. **Edit details** changes any of these; changing the spot or position is logged as a move.
- **Settings (gear icon):** add, edit or remove plants; add, rename, reorder or remove areas and spots; import or export the plant list.

## Plant numbers

Every plant keeps its number for life. A removed plant's number is never given to a new plant.

## Import and export

**Export all** downloads every plant, area, spot and location history entry as a JSON file. **Import** takes the same format: plants are matched by number (existing ones are updated, new ones added), and areas and spots are added if missing. Import never deletes anything.

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

## Data

The plant list lives in the app's own data folder (`/data/plants.db`), which Home Assistant includes in its backups. Nothing is sent anywhere else.
