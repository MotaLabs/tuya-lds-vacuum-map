# Contributing

Thanks for helping. This project grew out of one robot in one house, so reports from other robots and other
homes are the most useful thing you can bring.

## Running it

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
```

To run the service against your own Home Assistant, export the [settings](README.md#settings) and start it:

```sh
export TUYA_DEVICE_ID=... HA_URL=http://homeassistant.local:8123 HA_TOKEN=... MQTT_HOST=...
export DATA_DIR=./data
uv run tuya-lds-vacuum-map
```

`DATA_DIR` keeps your home's map; it is in `.gitignore`, keep it out of commits and issues.

## Layout

| Module | What it does |
|---|---|
| `protocol.py` | Encodes commands and decodes the robot's messages (names, rooms, areas, path) |
| `mapdata.py` | The cloud map grid: decoding and coordinates |
| `path.py` | Rebuilds the live path from the robot's incremental chunks |
| `coverage.py` | The floor the robot covered, used for local-only maps |
| `render.py` | Draws the map and computes the calibration points |
| `taps.py` | Finds the room or area under a tap, and tells a double tap from a single one |
| `tuya_cloud.py` | Signed requests to the Tuya cloud for the realtime map |
| `ha.py` | Home Assistant websocket client |
| `service.py` | Wires it together: events in, map and commands out |

## Tests

Every change comes with a test that fails without it. The suite builds its maps from synthetic grids
([tests/conftest.py](tests/conftest.py)); never commit a real map, a device id, a local key or anything
else from a real home.

## Pull requests

- One change per pull request, with the reason in the description.
- `uv run pytest` passes.
- Code without comments: names and small functions carry the meaning, the why goes in the commit message
  and the pull request.
- New user-facing text goes in both languages in `i18n.py`.

## Adding a robot

Most of the work is finding out whether your robot speaks the same protocol. You need LocalTuya set up for
it, and the robot's data points.

1. **Check the data points.** In the Tuya cloud the product is often called "Lds Robot". In LocalTuya, look
   for raw data points like `path_comm` and `comm_raw`. Write down their numbers and the state data point.
2. **Watch the events.** In Home Assistant, Developer tools, Events, listen to
   `localtuya_device_dp_triggered` and start a cleaning from the app. Decode a raw value with
   `base64 -d`. If you see JSON with `"infoType": 21011` (path) and `"infoType": 21004` (rooms), the robot
   speaks this protocol; compare with [docs/protocol.md](docs/protocol.md).
3. **Run the service** with your data point numbers (`STATE_DP`, `PATH_DP`, `RAW_DP`, `ROOM_MODE_DP`) and,
   if you can, a cloud project. Check the map, the names and a tap-to-clean.
4. **Open a "Robot support" issue** with the brand, model, Tuya product name and id, firmware, the data
   point numbers and what worked. Leave out device ids, local keys, tokens and anything that shows your
   home.

If something differs, for example another state name or another command, the change usually belongs in
`protocol.py` with a setting to choose it, plus a test.
