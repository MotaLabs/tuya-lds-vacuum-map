# Contributing

Thanks for helping. This project grew out of one robot in one house, so reports from other robots and other
homes are the most useful thing you can bring.

## Running it

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest --cov
uv run ruff check
uv run ruff format --check
```

To run the service against your own Home Assistant, export the [settings](README.md#settings) and start it:

```sh
export TUYA_DEVICE_ID=... HA_URL=http://homeassistant.local:8123 HA_TOKEN=... MQTT_HOST=...
export DATA_DIR=./data
uv run tuya-lds-vacuum-map
```

`DATA_DIR` keeps your home's map; it is in `.gitignore`, keep it out of commits and issues.

## Layout

Everything is under `src/tuya_lds_vacuum_map/`.

| Module | What it does |
|---|---|
| `profiles/` | Robot profiles (`*.toml`) and their loader, with `extends` |
| `lds/codec.py` | Base64 JSON values and the base36 room names |
| `lds/messages.py` | Reads the robot's messages: rooms, areas, path chunks, spot commands |
| `lds/commands.py` | Builds the cleaning commands: rooms, areas, spot |
| `map/grid.py` | The cloud map grid: decoding and coordinates |
| `map/path.py` | Rebuilds the live path from the robot's incremental chunks |
| `map/coverage.py` | The floor the robot covered, drawn over the cloud map or on its own |
| `map/model.py` | Keeps the cloud map and the coverage together and composes what is drawn |
| `map/hit.py` | Finds the room or area under a point |
| `map/colors.py` | Room colors: `#rrggbb` text and random colors that keep away from the rooms' hues |
| `map/render.py` | Draws the map and computes the calibration points |
| `integrations/` | Home Assistant websocket, LocalTuya events and commands, MQTT, Tuya cloud |
| `robot.py` | The robot's state, rooms, path and spot, updated from its data points |
| `gestures.py` | Turns taps into cleaning commands, and tells a double tap from a single one |
| `cloud_sync.py` | When to fetch the cloud map |
| `storage.py` | Saves and restores the state in `DATA_DIR` |
| `config.py` | Settings from the environment and the robots file |
| `i18n.py`, `translations/` | User-facing text, one TOML file per language |
| `session.py` | One robot: its events, map, taps, cloud refresh and saved state |
| `app.py` | The shared Home Assistant, MQTT and cloud connections, and which robot each event or topic belongs to |

Protocol facts that hold for every robot of the family (message types, command shapes) live in `lds/`.
Anything that can differ between robots goes in the profile, never in a constant in the code.
Whatever comes from outside (the robot, the cloud, MQTT) is read through the `as_*` helpers in
`lds/messages.py`: a field of the wrong kind is dropped or refused with a `ValueError`, so it never
breaks the service later.

## Tests

Every change comes with a test that fails without it. The suite builds its maps from synthetic grids
([tests/support/maps.py](tests/support/maps.py)); never commit a real map, a device id, a local key or
anything else from a real home.

- Tests mirror the package: `tests/map/test_grid.py` tests `src/tuya_lds_vacuum_map/map/grid.py`.
- Builders, fakes and the app harness live in `tests/support/`; `conftest.py` holds fixtures only.
- A test that waits for something to happen polls with `support.waiting.eventually`; a fixed sleep is
  only for showing that something did not happen.
- Code that reads outside input gets a property test with [Hypothesis](https://hypothesis.readthedocs.io/):
  any input is either read or refused with a `ValueError`. Shared strategies live in
  [tests/support/strategies.py](tests/support/strategies.py).
- `uv run pytest --cov` measures branch coverage and fails under 95%.

## Pull requests

- One change per pull request, with the reason in the description.
- `uv run pytest --cov`, `uv run ruff check` and `uv run ruff format --check` pass.
- Code without comments: names and small functions carry the meaning, the why goes in the commit message
  and the pull request.
- New user-facing text goes in every file under `translations/`; a test checks they all have the same keys.

## Adding a robot

Most of the work is finding out whether your robot speaks the same protocol. You need LocalTuya set up for
it, and the robot's data points.

1. **Check the data points.** In the Tuya cloud the product is often called "Lds Robot". In LocalTuya, look
   for raw data points like `path_comm` and `comm_raw`. Write down their numbers and the state data point.
2. **Watch the events.** In Home Assistant, Developer tools, Events, listen to
   `localtuya_device_dp_triggered` and start a cleaning from the app. Decode a raw value with
   `base64 -d`. If you see JSON with `"infoType": 21011` (path) and `"infoType": 21004` (rooms), the robot
   speaks this protocol; compare with [docs/protocol.md](docs/protocol.md).
3. **Write a profile** if anything differs from `lds-robot`: a `.toml` file that starts with
   `extends = "lds-robot"` and lists only the values that change (see
   [Robot profiles](README.md#robot-profiles)). The robot's state names come from the cloud model.
4. **Run the service** with `ROBOT_PROFILE` pointing at your file and, if you can, a cloud project. Check
   the map, the names and a tap-to-clean.
5. **Open a "Robot support" issue** with the brand, model, Tuya product name and id, firmware, your
   profile and what worked. Leave out device ids, local keys, tokens and anything that shows your home.

Once a robot is confirmed, its profile moves into `src/tuya_lds_vacuum_map/profiles/` as a built-in one,
with a line in the README's robot table. A difference a profile cannot express, for example another
command shape, goes in `lds/` with a profile value to choose it, plus a test.

## Adding a language

Copy `src/tuya_lds_vacuum_map/translations/en.toml` to a file named after the language tag, for example
`de.toml`, and translate the values; keep the keys. Try it with `LANGUAGE=de`. The tests check that every
built-in language has the same keys as English.
