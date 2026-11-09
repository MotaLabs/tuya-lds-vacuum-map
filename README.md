# tuya-lds-vacuum-map

A live, clickable map for Tuya LDS (laser) robot vacuums in Home Assistant.

Many robot vacuums sold under different brands run on the same laser-navigation platform and talk to the
world through Tuya. In the Tuya cloud they show up as **"Lds Robot"**. Home Assistant can already control
them locally with [LocalTuya](https://github.com/xZetsubou/hass-localtuya), but there is no map: the map lives
in the vendor app. This service brings it to Home Assistant, draws it the way the app does, and turns taps on
the map into cleaning commands sent over the local network.

> Status: early. It runs every day against one robot (a KaBuM! Smart 900). Expect rough edges, and please
> report what you find.

## Features

| Status | Feature | Notes |
|---|---|---|
| Done | Map from the Tuya cloud | Walls, rooms and obstacles, refreshed during and after every cleaning |
| Done | Local-only mode | Uses the last cloud map and paints the floor the robot covers since then; works with no cloud at all, from the path alone |
| Done | Live path and robot position | From the robot's local path messages, every ~2 s |
| Done | Room names, as in the app | Read from the robot, so renaming a room in the app shows up here |
| Done | Per-room suction and water icons | Shown while per-room settings are on |
| Done | Room colors | Random for each new room, kept across restarts, editable from Home Assistant |
| Done | Areas and no-go zones | Read from the robot; edit them in the app and the map follows |
| Done | Tap a room to clean it | Local command, no cloud |
| Done | Tap an area to clean it | Uses the areas as they are on the robot |
| Done | Double tap a room to open its settings | Selects the room on a Home Assistant select and fires an event |
| Done | Spot cleaning | Tap a point, and the robot cleans the 1.5 m square around it, drawn on the map with its path while it works |
| Done | Map source switch | `Automatic` (cloud while it answers, local otherwise) or `Local only`, from Home Assistant |
| Done | English and Brazilian Portuguese | Entity names and map labels |
| Done | Several robots in one instance | Each robot gets its own device, map, topics and saved state; so far only one robot has run it |
| Planned | Clean several rooms or points in the order tapped | Tap modes with a list and a start that waits for the last tap ([#4](https://github.com/MotaLabs/tuya-lds-vacuum-map/issues/4), [#5](https://github.com/MotaLabs/tuya-lds-vacuum-map/issues/5)) |
| Planned | Draw an area and clean it | Needs the app's zone command captured ([#7](https://github.com/MotaLabs/tuya-lds-vacuum-map/issues/7)) |
| Planned | Clean one area when the robot has several | Today a tap on an area cleans all of the robot's areas ([#8](https://github.com/MotaLabs/tuya-lds-vacuum-map/issues/8)) |
| Planned | Room settings, parts, warnings and statistics from the service | Today they need Home Assistant helpers, templates and automations ([milestone 0.4](https://github.com/MotaLabs/tuya-lds-vacuum-map/milestone/3)) |
| Planned | Edit areas and no-go zones from the map | Today they are edited in the app ([#15](https://github.com/MotaLabs/tuya-lds-vacuum-map/issues/15)) |
| Not possible | Live map without the cloud | The robot only sends the path locally; the walls go to the cloud ([why](docs/protocol.md#data-points)) |

## How it works

```mermaid
flowchart LR
    robot[Robot] -- local, DP 123/127 --> localtuya[LocalTuya]
    robot -. uploads map .-> cloud[Tuya cloud]
    localtuya -- events --> ha[Home Assistant]
    ha -- websocket events --> service[tuya-lds-vacuum-map]
    cloud -. realtime map .-> service
    service -- MQTT: camera, selects --> mqtt[(MQTT broker)]
    mqtt --> ha
    card[xiaomi-vacuum-map-card] -- taps via mqtt.publish --> mqtt
    service -- localtuya.set_dp --> ha
    ha -- local command --> robot
```

1. LocalTuya already receives everything the robot sends. The service listens to those events through the
   Home Assistant websocket: the robot state, the live path (DP 123) and the room list, areas and commands
   (DP 127).
2. When a cloud project is configured, it downloads the robot's realtime map from the Tuya cloud during and
   after each cleaning. The map is a grid of 5 cm cells where each cell holds a room id, a wall or nothing.
3. It draws the map with Pillow, flipped and coloured like the app, adds names, settings, areas, the dock,
   the path and the robot, and publishes it to MQTT as a Home Assistant camera, with calibration points for
   the card.
4. [xiaomi-vacuum-map-card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card) shows the
   camera. A tap publishes its coordinates back to MQTT; the service finds the room or area under the finger
   and sends the cleaning command through LocalTuya.

The protocol details, with examples, are in [docs/protocol.md](docs/protocol.md).

## Supported robots

| Robot | Tuya product | Profile | Status |
|---|---|---|---|
| KaBuM! Smart 900 (KBSF009, KBSF010, KBSF011, KBSF012) | `Lds Robot`, `7vx4ulan46paequq` | `lds-robot` | Confirmed |

Robots from other brands that appear in the Tuya cloud as "Lds Robot", or whose LocalTuya data points
include `path_comm` (123) and `comm_raw` (127), are likely to work. If you have one, please follow
[Adding a robot](CONTRIBUTING.md#adding-a-robot) and open an issue with what you find, working or not.

## Requirements

- Home Assistant with:
  - [LocalTuya](https://github.com/xZetsubou/hass-localtuya) set up for the robot. The service listens to its
    `localtuya_device_dp_triggered` and `localtuya_status_update` events and sends commands with
    `localtuya.set_dp`.
  - The MQTT integration, with discovery on.
  - [xiaomi-vacuum-map-card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card), from HACS.
  - A long-lived access token.
- An MQTT broker the service can reach.
- Optional, for the cloud map: a project on the [Tuya developer platform](https://platform.tuya.com) with the
  **IoT Core** service and the **Sweeping Robot Open Service** API, linked to the app account that owns the
  robot. Without it the service runs in local-only mode.

## Installation

The service is a container. With Docker Compose:

```yaml
services:
  vacuum-map:
    image: ghcr.io/motalabs/tuya-lds-vacuum-map:0.1.0
    restart: unless-stopped
    environment:
      TUYA_DEVICE_ID: your-device-id
      HA_URL: http://homeassistant:8123
      HA_TOKEN: ${HA_TOKEN}
      MQTT_HOST: mosquitto
      MQTT_USERNAME: vacuum-map
      MQTT_PASSWORD: ${MQTT_PASSWORD}
      TUYA_CLOUD_CLIENT_ID: ${TUYA_CLOUD_CLIENT_ID}
      TUYA_CLOUD_CLIENT_SECRET: ${TUYA_CLOUD_CLIENT_SECRET}
      TUYA_CLOUD_REGION: eu
      LANGUAGE: en
      DEVICE_NAME: Robot vacuum
    volumes:
      - vacuum-map:/data

volumes:
  vacuum-map:
```

The service runs as uid 1000 and keeps its state in `/data`. A named volume starts out owned by that user. A
folder of your own works too if uid 1000 can write to it (`mkdir vacuum-map && sudo chown 1000:1000 vacuum-map`,
then `./vacuum-map:/data`); a folder Docker creates for a bind mount belongs to root, and the state is then never
saved, which the log says on every attempt.

Every release publishes the image with three tags: the version (`0.1.0`), the git tag (`v0.1.0`) and
`latest`. To run a change that is not released yet, build it from this repository with `docker build -t
tuya-lds-vacuum-map .`.

### Several robots

One instance serves every robot. Instead of `TUYA_DEVICE_ID`, `DEVICE_NAME` and `ROBOT_PROFILE`, list the
robots in a TOML file and point `ROBOTS_FILE` at it:

```toml
[[robots]]
device_id = "first-device-id"
name = "Downstairs vacuum"

[[robots]]
device_id = "second-device-id"
name = "Upstairs vacuum"
profile = "upstairs.toml"
```

`name` and `profile` are optional. A relative profile path is read from the folder of the robots file.
Setting both `TUYA_DEVICE_ID` and `ROBOTS_FILE` is an error. Each robot gets its own Home Assistant device,
its own topics under `<prefix>/<device id>/` and its own folder in `DATA_DIR`; the Home Assistant
connection, the MQTT connection and the cloud project are shared.

### Settings

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `TUYA_DEVICE_ID` | one robot | | The robot's Tuya device id, as in LocalTuya |
| `ROBOTS_FILE` | several robots | | Path to the [robots file](#several-robots) |
| `HA_URL` | yes | | Home Assistant URL |
| `HA_TOKEN` | yes | | Long-lived access token |
| `MQTT_HOST` | yes | | MQTT broker host |
| `MQTT_PORT` | | `1883` | |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | | | |
| `TUYA_CLOUD_CLIENT_ID`, `TUYA_CLOUD_CLIENT_SECRET`, `TUYA_CLOUD_REGION` | | | Cloud project; region is `us`, `eu`, `cn`, `in` or `sg`. Leave them out for local-only |
| `LANGUAGE` | | `en` | `en`, `pt-BR` or the path to a translation `.toml`; texts missing from the file stay in English |
| `DEVICE_NAME` | | `Vacuum map <id>` | With `TUYA_DEVICE_ID`: name of the Home Assistant device the entities belong to |
| `DATA_DIR` | | `/data` | Where the last map, the rooms and the covered floor are kept between restarts, one folder per robot |
| `ROBOT_PROFILE` | | `lds-robot` | With `TUYA_DEVICE_ID`: a built-in [robot profile](#robot-profiles) or the path to a `.toml` file |
| `MAP_SCALE` | | `4` | Image pixels per map cell |
| `TOPIC_PREFIX` | | `tuya_lds_vacuum_map` | MQTT topics are `<prefix>/<device id>/...`, plus `<prefix>/availability` |
| `DISCOVERY_PREFIX` | | `homeassistant` | MQTT discovery prefix configured in Home Assistant |
| `CLOUD_REFRESH_SECONDS` | | `60` | How often the cloud map is fetched while the robot moves |
| `CLOUD_IDLE_REFRESH_SECONDS` | | `21600` | How often the cloud map is fetched while the robot is still |
| `CLOUD_AFTER_CLEANING_SECONDS` | | `20` | Wait after a cleaning ends before fetching the final map |
| `DOUBLE_TAP_SECONDS` | | `0.6` | Window for a double tap; a single tap waits this long before cleaning |
| `RENDER_INTERVAL_SECONDS` | | `1.5` | Shortest time between two map images |
| `SAVE_INTERVAL_SECONDS` | | `30` | How often the state in `DATA_DIR` is written |
| `FONT_PATH` | | DejaVu Sans | TrueType font for the labels |
| `LOG_LEVEL` | | `INFO` | |

### Robot profiles

What differs between robots lives in a profile, not in the code: the data point numbers, which states mean
the robot is moving or cleaning a spot, which text names each state, the cell codes of the cloud map, the
cleaning width and spot size, and the suction and water levels. The built-in `lds-robot` profile is in
[profiles/lds_robot.toml](src/tuya_lds_vacuum_map/profiles/lds_robot.toml).

A robot that differs in a few values does not need a full copy. A profile can extend another one and
change only what differs:

```toml
extends = "lds-robot"
name = "my-robot"

[data_points]
raw = 128

[geometry]
spot_size_mm = 1000
```

Mount the file in the container and point `ROBOT_PROFILE` at it, for example
`ROBOT_PROFILE=/data/my-robot.toml`, or name it in the robot's `profile` in the robots file.

## Home Assistant setup

The service creates, through MQTT discovery:

| Entity | What it is |
|---|---|
| `camera.<device>_map` | The map, with `calibration_points`, `robot_state` (the robot's own code), `robot_state_name` (its text in the service's language, such as "Emptying the bin"), `robot_position`, `map_source` and `rooms` (id, name, color) attributes; the position changes as the robot moves, so a card that redraws when the camera changes follows it |
| `select.<device>_map_source` | `Automatic` or `Local only` |
| `select.<device>_room_settings` | The room picked by a double tap, or `None` |
| `text.<device>_room_color` | The picked room's color as `#rrggbb`; write a new one to change it |
| `button.<device>_random_room_color` | Gives the picked room a new random color |

A double tap also fires the `tuya_lds_vacuum_map_room_settings` event with `device_id`, `room_id` and
`room_name`, for automations.

The card needs two map modes that publish taps to the service. Replace the device id in the topic:

```yaml
type: custom:xiaomi-vacuum-map-card
entity: vacuum.robot
map_source:
  camera: camera.robot_vacuum_map
calibration_source:
  camera: true
map_modes:
  - name: Tap to clean, double tap for settings
    icon: mdi:gesture-tap
    selection_type: MANUAL_POINT
    coordinates_rounding: true
    run_immediately: true
    service_call_schema:
      service: mqtt.publish
      service_data:
        topic: tuya_lds_vacuum_map/<device-id>/tap
        payload: '{"mode": "room", "x": [[point_x]], "y": [[point_y]]}'
  - name: Spot cleaning
    icon: mdi:map-marker-radius
    selection_type: MANUAL_POINT
    coordinates_rounding: true
    service_call_schema:
      service: mqtt.publish
      service_data:
        topic: tuya_lds_vacuum_map/<device-id>/tap
        payload: '{"mode": "spot", "x": [[point_x]], "y": [[point_y]]}'
```

To show a room's settings below the map after a double tap, use a `conditional` card on
`select.<device>_room_settings` for each room. The room color entities act on the same picked room, so they
fit in the same card.

## Map source

| Mode | Cloud calls | What is drawn |
|---|---|---|
| Automatic | During and after each cleaning, and every 6 h | The latest cloud map, plus the floor covered since it was fetched |
| Local only | None | The last cloud map saved, plus every bit of floor the robot covered since; with no saved map, only the covered floor |

If the cloud stops answering, for example when the IoT Core subscription expires, Automatic keeps drawing
from the saved map and the live path, so nothing breaks; the walls simply stop updating.

## Roadmap

Planned work is tracked as [issues](https://github.com/MotaLabs/tuya-lds-vacuum-map/issues) grouped in
[milestones](https://github.com/MotaLabs/tuya-lds-vacuum-map/milestones). The direction: first make it easy
to install, then move into this service everything a robot still needs from Home Assistant helpers and
automations, and then ship it as a Home Assistant integration. Support for more robots comes from
[robot support issues](https://github.com/MotaLabs/tuya-lds-vacuum-map/issues/new?template=robot-support.yml).

## Contributing

Bug reports, robot reports and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to
run it, how the code is laid out and how to add a robot.

## Acknowledgements

- [LocalTuya](https://github.com/xZetsubou/hass-localtuya), which does the hard part of talking to the robot.
- [xiaomi-vacuum-map-card](https://github.com/PiotrMachowski/lovelace-xiaomi-vacuum-map-card), which draws the
  map and turns taps into coordinates.
- [Xiaomi Cloud Map Extractor](https://github.com/PiotrMachowski/Home-Assistant-custom-components-Xiaomi-Cloud-Map-Extractor),
  whose Roidmi parser showed that this map format is shared across brands.
- [tuya-vacuum-maps](https://github.com/cornguo/tuya-vacuum-maps), which pointed at the cloud map endpoint.

## License

[MIT](LICENSE): use it as you wish. The software is provided as is, without warranty.
